#!/usr/bin/env python3
"""Build reproducible skill and configuration ZIPs for distribution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile
import io
import re


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parents[1]
VERSION = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
SKILL_NAME = "supercut-chatcut-workflow"
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
TEXT_SUFFIXES = {".md", ".json", ".yaml", ".yml", ".py", ".ps1", ".txt", ".toml"}
FORBIDDEN_PATTERNS = {
    "local_drive_path": re.compile(r"(?i)(?<![A-Za-z])[A-Z]:[\\/]"),
    "download_folder_marker": re.compile(r"(?i)" + "browser" + "download"),
    "chatcut_uuid": re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"),
    "private_token": re.compile(r"(?i)\b(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]+|sk-[A-Za-z0-9_-]{20,})\b"),
}


def _write_reproducible_zip(source: Path, output: Path, prefix: str = "") -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            relative = path.relative_to(source).as_posix()
            if source == SKILL_ROOT and relative == "scripts/build_release.py":
                continue
            if (
                source == SKILL_ROOT
                and relative.startswith("assets/config/supercut-chatcut-config-")
                and relative != f"assets/config/supercut-chatcut-config-{VERSION}.zip"
            ):
                continue
            arcname = f"{prefix.rstrip('/')}/{relative}" if prefix else relative
            info = zipfile.ZipInfo(arcname, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def _copy_runtime(staging: Path) -> None:
    runtime = staging / "runtime"
    runtime.mkdir(parents=True)
    for name in [
        "supercut.py",
        "workflow.py",
        "effect_runtime.py",
        "chatcut_bridge.py",
        "chatcut_proxy.py",
        "media_acquisition.py",
        "rhythm.py",
        "requirements.txt",
    ]:
        shutil.copy2(REPO_ROOT / name, runtime / name)
    shutil.copy2(REPO_ROOT / "supercut.config.json", runtime / "supercut.config.json")

    tools_target = runtime / "tools"
    tools_target.mkdir()
    shutil.copy2(REPO_ROOT / "tools" / "render_layered_plan.py", tools_target / "render_layered_plan.py")

    shutil.copytree(REPO_ROOT / "knowledge", staging / "knowledge", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(REPO_ROOT / "templates", staging / "templates", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(REPO_ROOT / "WORKFLOW_FINAL.md", staging / "WORKFLOW_FINAL.md")

    config = staging / "config"
    config.mkdir()
    for name in ["workflow.defaults.json", "request-template.json", "runtime.config.json"]:
        shutil.copy2(SKILL_ROOT / "assets" / "defaults" / name, config / name)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scan_text(label: str, payload: bytes, findings: list[str]) -> None:
    text = payload.decode("utf-8", errors="ignore")
    for name, pattern in FORBIDDEN_PATTERNS.items():
        if pattern.search(text):
            findings.append(f"{name}: {label}")


def _scan_zip_bytes(label: str, payload: bytes, findings: list[str]) -> None:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            data = archive.read(member)
            nested_label = f"{label}!{member.filename}"
            if member.filename.lower().endswith(".zip"):
                _scan_zip_bytes(nested_label, data, findings)
            elif Path(member.filename).suffix.lower() in TEXT_SUFFIXES:
                _scan_text(nested_label, data, findings)


def assert_public_safe_archive(path: Path) -> None:
    findings: list[str] = []
    _scan_zip_bytes(path.name, path.read_bytes(), findings)
    if findings:
        raise RuntimeError("privacy scan failed: " + "; ".join(findings))


def main() -> int:
    existing_manifest = {}
    config_manifest_path = SKILL_ROOT / "assets" / "config-source.json"
    if config_manifest_path.is_file():
        existing_manifest = json.loads(config_manifest_path.read_text(encoding="utf-8"))
    remote_url = existing_manifest.get("package", {}).get("url")
    config_zip = SKILL_ROOT / "assets" / "config" / f"supercut-chatcut-config-{VERSION}.zip"
    with tempfile.TemporaryDirectory(prefix="supercut-config-build-") as temporary:
        staging = Path(temporary)
        _copy_runtime(staging)
        _write_reproducible_zip(staging, config_zip)

    config_manifest = {
        "schema": "supercut-config-source-v1",
        "channel": "stable",
        "version": VERSION,
        "fallback_to_embedded": True,
        "package": {
            "url": remote_url,
            "embedded": f"config/{config_zip.name}",
            "sha256": _sha256(config_zip),
            "size_bytes": config_zip.stat().st_size,
        },
    }
    config_manifest_path.write_text(json.dumps(config_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    dist = REPO_ROOT / "dist" / f"{SKILL_NAME}-{VERSION}"
    dist.mkdir(parents=True, exist_ok=True)
    skill_zip = dist / f"{SKILL_NAME}-{VERSION}.zip"
    _write_reproducible_zip(SKILL_ROOT, skill_zip, SKILL_NAME)
    assert_public_safe_archive(skill_zip)
    standalone_config = dist / config_zip.name
    shutil.copy2(config_zip, standalone_config)
    shutil.copy2(SKILL_ROOT / "install.ps1", dist / "install.ps1")
    shutil.copy2(SKILL_ROOT / "scripts" / "install_workflow.py", dist / "install_workflow.py")
    shutil.copy2(SKILL_ROOT / "references" / "INSTALL.md", dist / "INSTALL.md")

    release_manifest = {
        "schema": "supercut-workflow-release-v1",
        "name": SKILL_NAME,
        "version": VERSION,
        "artifacts": {
            "skill": {
                "file": skill_zip.name,
                "sha256": _sha256(skill_zip),
                "size_bytes": skill_zip.stat().st_size,
            },
            "config": {
                "file": standalone_config.name,
                "sha256": _sha256(standalone_config),
                "size_bytes": standalone_config.stat().st_size,
            },
        },
        "remote_publish": {
            "config_manifest_url": None,
            "note": "Set package.url in assets/config-source.json to the hosted HTTPS config ZIP and rebuild the skill ZIP.",
        },
    }
    release_manifest_path = dist / "release-manifest.json"
    release_manifest_path.write_text(json.dumps(release_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "built",
        "version": VERSION,
        "dist": str(dist),
        "skill_zip": str(skill_zip),
        "skill_sha256": release_manifest["artifacts"]["skill"]["sha256"],
        "config_zip": str(standalone_config),
        "config_sha256": release_manifest["artifacts"]["config"]["sha256"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
