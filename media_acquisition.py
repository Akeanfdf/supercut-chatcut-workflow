"""Rights-gated acquisition for local files and public Bilibili URLs.

The downloader intentionally does not use browser cookies or bypass access
controls.  Every successful remote acquisition produces a provenance manifest.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


BVID_RE = re.compile(r"(?i)(BV[0-9A-Za-z]{10})")
ALLOWED_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv", "www.b23.tv"}


class AcquisitionError(RuntimeError):
    """An acquisition that should transition the workflow to needs_input."""


@dataclass(frozen=True)
class BilibiliTarget:
    original_url: str
    resolved_url: str
    bvid: str
    part: int


def _normalized_host(value: str) -> str:
    return (urlparse(value).hostname or "").lower().rstrip(".")


def is_bilibili_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and _normalized_host(value) in ALLOWED_HOSTS


def parse_bilibili_url(value: str, *, resolve_short: bool = True, timeout: float = 15.0) -> BilibiliTarget:
    if not is_bilibili_url(value):
        raise ValueError("仅支持 bilibili.com/video/BV…、移动域名或 b23.tv 短链")
    original = value
    resolved = value
    host = _normalized_host(value)
    if host in {"b23.tv", "www.b23.tv"}:
        if not resolve_short:
            raise ValueError("b23.tv 短链需要先解析公开重定向")
        request = Request(value, headers={"User-Agent": "supercut-chatcut-workflow/1.1"})
        with urlopen(request, timeout=timeout) as response:
            resolved = response.geturl()
        if _normalized_host(resolved) not in ALLOWED_HOSTS - {"b23.tv", "www.b23.tv"}:
            raise ValueError("b23.tv 重定向目标不是受支持的 B 站域名")
    parsed = urlparse(resolved)
    match = BVID_RE.search(parsed.path)
    if not match:
        raise ValueError("链接中未找到有效 BV 号")
    part_values = parse_qs(parsed.query).get("p", ["1"])
    try:
        part = int(part_values[0])
    except (TypeError, ValueError) as exc:
        raise ValueError("分P参数 p 必须为正整数") from exc
    if part < 1:
        raise ValueError("分P参数 p 必须为正整数")
    return BilibiliTarget(original, resolved, match.group(1), part)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _yt_dlp_command() -> list[str]:
    executable = shutil.which("yt-dlp")
    if executable:
        return [executable]
    probe = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if probe.returncode == 0:
        return [sys.executable, "-m", "yt_dlp"]
    raise AcquisitionError("未找到 yt-dlp；请安装 requirements.txt 中锁定的版本，或改用本地合法素材")


def _probe(path: Path, ffprobe: str | None) -> dict[str, Any]:
    executable = ffprobe or shutil.which("ffprobe")
    if not executable:
        return {"duration": None, "format": path.suffix.lower().lstrip(".")}
    completed = subprocess.run(
        [executable, "-v", "error", "-show_entries", "format=duration,format_name", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        return {"duration": None, "format": path.suffix.lower().lstrip(".")}
    payload = json.loads(completed.stdout or "{}")
    fmt = payload.get("format", {})
    duration = fmt.get("duration")
    return {
        "duration": round(float(duration), 6) if duration not in {None, "N/A"} else None,
        "format": fmt.get("format_name") or path.suffix.lower().lstrip("."),
    }


def acquisition_requirement_report(reference: str | None, source: str | None) -> dict[str, Any]:
    remote = [value for value in (reference, source) if value and is_bilibili_url(value)]
    return {
        "schema": "supercut-acquisition-manifest-v1",
        "status": "needs_input",
        "reason": "media_rights_confirmation_required",
        "remote_inputs": remote,
        "required_action": "重新运行并添加 --confirm-media-rights，或提供本地合法素材。",
        "download_attempted": False,
    }


def acquire_bilibili(
    value: str,
    cache_dir: Path,
    *,
    confirm_media_rights: bool,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    timeout: float = 30.0,
) -> tuple[Path, dict[str, Any]]:
    if not confirm_media_rights:
        raise AcquisitionError("下载前必须显式使用 --confirm-media-rights 确认素材权利")
    target = parse_bilibili_url(value, timeout=timeout)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = sorted(
        (path for path in cache_dir.glob(f"{target.bvid}-p{target.part}.*") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
    )
    if cached:
        path = cached[-1].resolve()
        metadata = _probe(path, ffprobe)
        return path, {
            "source_kind": "bilibili_public_url",
            "original_url": target.original_url,
            "resolved_url": target.resolved_url,
            "bvid": target.bvid,
            "part": target.part,
            "file": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            **metadata,
            "rights_confirmed": True,
            "cookies_used": False,
            "cache_hit": True,
        }
    output_template = cache_dir / f"{target.bvid}-p{target.part}.%(ext)s"
    command = _yt_dlp_command() + [
        "--no-playlist",
        "--no-cookies",
        "--restrict-filenames",
        "--merge-output-format",
        "mp4",
        "--print",
        "after_move:filepath",
        "-o",
        str(output_template),
    ]
    if ffmpeg:
        command += ["--ffmpeg-location", str(Path(ffmpeg).resolve().parent)]
    command.append(target.resolved_url)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AcquisitionError(f"B站素材获取失败：{exc}；请改用本地合法素材") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout)[-1600:].strip()
        raise AcquisitionError(f"B站素材不可公开获取或网络/空间不足：{detail}；请改用本地合法素材")
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    path = Path(lines[-1]).resolve() if lines else Path()
    if not path.is_file() or cache_dir.resolve() not in path.parents:
        candidates = sorted(cache_dir.glob(f"{target.bvid}-p{target.part}.*"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise AcquisitionError("yt-dlp 未返回可用媒体文件；请改用本地合法素材")
        path = candidates[-1].resolve()
    metadata = _probe(path, ffprobe)
    record = {
        "source_kind": "bilibili_public_url",
        "original_url": target.original_url,
        "resolved_url": target.resolved_url,
        "bvid": target.bvid,
        "part": target.part,
        "file": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        **metadata,
        "rights_confirmed": True,
        "cookies_used": False,
        "cache_hit": False,
    }
    return path, record


def resolve_input(
    value: str,
    cache_dir: Path,
    *,
    confirm_media_rights: bool,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    if is_bilibili_url(value):
        return acquire_bilibili(
            value,
            cache_dir,
            confirm_media_rights=confirm_media_rights,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
    path = Path(value).expanduser().resolve()
    parsed = urlparse(value)
    if parsed.scheme and not (len(parsed.scheme) == 1 and value[1:3] in {":\\", ":/"}):
        raise AcquisitionError("仅允许本地文件或受支持的 B 站视频链接")
    if not path.is_file():
        raise AcquisitionError(f"本地素材不存在：{path}")
    metadata = _probe(path, ffprobe)
    return path, {
        "source_kind": "local_file",
        "file": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        **metadata,
        "rights_confirmed": None,
    }


def write_manifest(path: Path, records: list[dict[str, Any]], *, status: str = "acquired") -> dict[str, Any]:
    payload = {
        "schema": "supercut-acquisition-manifest-v1",
        "status": status,
        "items": records,
        "privacy": {"cache_scope": "private", "browser_cookies_read": False, "access_controls_bypassed": False},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
