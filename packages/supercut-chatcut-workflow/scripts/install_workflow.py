#!/usr/bin/env python3
"""Install the Supercut ChatCut skill from a local directory, ZIP, or HTTPS URL."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile


SKILL_NAME = "supercut-chatcut-workflow"
MAX_PACKAGE_BYTES = 128 * 1024 * 1024


def _skills_dir() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    return (Path(codex_home) if codex_home else Path.home() / ".codex") / "skills"


def _download(source: str, expected_sha256: str | None) -> Path:
    parsed = urlparse(source)
    if parsed.scheme.lower() != "https":
        raise RuntimeError("Remote skill packages must use HTTPS")
    if not expected_sha256:
        raise RuntimeError("--sha256 is required for a remote skill package")
    request = Request(source, headers={"User-Agent": "supercut-chatcut-installer/1.0"})
    with urlopen(request, timeout=30) as response:
        payload = response.read(MAX_PACKAGE_BYTES + 1)
    if len(payload) > MAX_PACKAGE_BYTES:
        raise RuntimeError("Skill package is too large")
    actual = hashlib.sha256(payload).hexdigest()
    if actual.lower() != expected_sha256.lower():
        raise RuntimeError(f"Skill package SHA-256 mismatch: expected {expected_sha256}, got {actual}")
    handle = tempfile.NamedTemporaryFile(prefix="supercut-skill-", suffix=".zip", delete=False)
    handle.write(payload)
    handle.close()
    return Path(handle.name)


def _safe_extract(source: Path, destination: Path) -> None:
    total = 0
    root = destination.resolve()
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            normalized = member.filename.replace("\\", "/")
            pure = PurePosixPath(normalized)
            unix_type = (member.external_attr >> 16) & 0o170000
            if pure.is_absolute() or ".." in pure.parts or ":" in normalized:
                raise RuntimeError(f"Unsafe path in skill package: {member.filename}")
            if unix_type == stat.S_IFLNK:
                raise RuntimeError(f"Symlinks are not allowed in skill packages: {member.filename}")
            total += member.file_size
            if total > MAX_PACKAGE_BYTES:
                raise RuntimeError("Expanded skill package is too large")
            target = (destination / Path(*pure.parts)).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"Unsafe extraction target: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as incoming, target.open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)


def _find_skill_root(root: Path) -> Path:
    direct = root / "SKILL.md"
    nested = root / SKILL_NAME / "SKILL.md"
    if direct.is_file():
        return root
    if nested.is_file():
        return root / SKILL_NAME
    matches = list(root.glob(f"**/{SKILL_NAME}/SKILL.md"))
    if len(matches) == 1:
        return matches[0].parent
    raise RuntimeError(f"Could not locate {SKILL_NAME}/SKILL.md")


def main() -> int:
    own_skill = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(own_skill), help="Skill directory, ZIP, or HTTPS URL")
    parser.add_argument("--sha256", help="Required SHA-256 for HTTPS skill packages")
    parser.add_argument("--skills-dir", default=str(_skills_dir()), help="Codex skills directory")
    parser.add_argument("--config-target", help="Optional workflow configuration home")
    parser.add_argument("--manifest", help="Optional configuration manifest URL")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    downloaded: Path | None = None
    staging_root = Path(tempfile.mkdtemp(prefix="supercut-install-"))
    try:
        source_value = args.source
        if urlparse(source_value).scheme.lower() in {"http", "https"}:
            downloaded = _download(source_value, args.sha256)
            _safe_extract(downloaded, staging_root)
            source_root = _find_skill_root(staging_root)
        else:
            local_source = Path(source_value).expanduser().resolve()
            if local_source.is_file():
                if args.sha256:
                    actual = hashlib.sha256(local_source.read_bytes()).hexdigest()
                    if actual.lower() != args.sha256.lower():
                        raise RuntimeError(f"Skill package SHA-256 mismatch: expected {args.sha256}, got {actual}")
                _safe_extract(local_source, staging_root)
                source_root = _find_skill_root(staging_root)
            else:
                source_root = _find_skill_root(local_source)

        skills_dir = Path(args.skills_dir).expanduser().resolve()
        skills_dir.mkdir(parents=True, exist_ok=True)
        destination = skills_dir / SKILL_NAME
        install_staging = skills_dir / f".{SKILL_NAME}.installing"
        if install_staging.exists():
            shutil.rmtree(install_staging)
        shutil.copytree(source_root, install_staging)

        backup = None
        if destination.exists():
            backup = skills_dir / f"{SKILL_NAME}.backup-{time.strftime('%Y%m%d-%H%M%S')}"
            destination.replace(backup)
        install_staging.replace(destination)

        command = [sys.executable, str(destination / "scripts" / "bootstrap.py"), "--ensure", "--json"]
        if args.config_target:
            command.extend(["--target", args.config_target])
        if args.manifest:
            command.extend(["--manifest", args.manifest])
        child_environment = os.environ.copy()
        child_environment["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=child_environment,
        )
        bootstrap_result = json.loads(completed.stdout)
        result = {
            "status": "installed",
            "skill_path": str(destination),
            "backup_path": str(backup) if backup else None,
            "bootstrap": bootstrap_result,
        }
        print(json.dumps(result, ensure_ascii=True, indent=2) if args.json else f"Installed at {destination}\nConfig ready at {bootstrap_result['active_path']}")
        return 0
    except Exception as exc:
        failure = {"status": "error", "error": str(exc)}
        print(json.dumps(failure, ensure_ascii=True, indent=2) if args.json else f"ERROR: {exc}")
        return 1
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
        if downloaded:
            downloaded.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
