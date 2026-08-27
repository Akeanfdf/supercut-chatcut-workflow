#!/usr/bin/env python3
"""Install or update the versioned Supercut workflow configuration safely."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import time
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile


SKILL_ROOT = Path(__file__).resolve().parents[1]
LOCAL_MANIFEST = SKILL_ROOT / "assets" / "config-source.json"
DEFAULT_MAX_BYTES = 64 * 1024 * 1024


class BootstrapError(RuntimeError):
    pass


def _default_target() -> Path:
    override = os.environ.get("SUPERCUT_WORKFLOW_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".supercut-chatcut").resolve()


def _is_url(value: str) -> bool:
    return urlparse(value).scheme.lower() in {"http", "https"}


def _download_https(url: str, max_bytes: int, timeout: float) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise BootstrapError("Remote manifests and packages must use HTTPS")
    request = Request(url, headers={"User-Agent": "supercut-chatcut-bootstrap/1.0"})
    with urlopen(request, timeout=timeout) as response:
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > max_bytes:
            raise BootstrapError(f"Remote file exceeds {max_bytes} bytes")
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise BootstrapError(f"Remote file exceeds {max_bytes} bytes")
    return payload


def _load_json_source(source: str, max_bytes: int, timeout: float) -> dict[str, Any]:
    if _is_url(source):
        raw = _download_https(source, max_bytes, timeout)
    else:
        raw = Path(source).expanduser().resolve().read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"Invalid UTF-8 JSON manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise BootstrapError("Manifest root must be an object")
    return value


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != "supercut-config-source-v1":
        raise BootstrapError("Unsupported config manifest schema")
    version = manifest.get("version")
    package = manifest.get("package")
    if not isinstance(version, str) or not version.strip():
        raise BootstrapError("Manifest version is missing")
    if not isinstance(package, dict):
        raise BootstrapError("Manifest package is missing")
    digest = package.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise BootstrapError("Manifest package SHA-256 must contain 64 hexadecimal characters")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise BootstrapError("Manifest package SHA-256 is not hexadecimal") from exc


def _read_package(
    manifest: dict[str, Any],
    *,
    offline: bool,
    max_bytes: int,
    timeout: float,
) -> tuple[bytes, str, list[str]]:
    package = manifest["package"]
    warnings: list[str] = []
    remote_url = package.get("url")
    embedded = package.get("embedded")
    if remote_url and not offline:
        try:
            return _download_https(str(remote_url), max_bytes, timeout), str(remote_url), warnings
        except Exception as exc:
            if not manifest.get("fallback_to_embedded", True):
                raise
            warnings.append(f"remote package unavailable; used bundled fallback: {exc}")
    if not isinstance(embedded, str) or not embedded:
        raise BootstrapError("No bundled configuration package is available")
    embedded_path = (SKILL_ROOT / "assets" / embedded).resolve()
    assets_root = (SKILL_ROOT / "assets").resolve()
    if embedded_path != assets_root and assets_root not in embedded_path.parents:
        raise BootstrapError("Bundled package path escapes the skill assets directory")
    payload = embedded_path.read_bytes()
    if len(payload) > max_bytes:
        raise BootstrapError(f"Bundled package exceeds {max_bytes} bytes")
    return payload, str(embedded_path), warnings


def _verify_digest(payload: bytes, expected: str) -> str:
    actual = hashlib.sha256(payload).hexdigest()
    if actual.lower() != expected.lower():
        raise BootstrapError(f"SHA-256 mismatch: expected {expected}, got {actual}")
    return actual


def _safe_extract(payload: bytes, destination: Path, max_bytes: int) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    destination_root = destination.resolve()
    total = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            normalized = member.filename.replace("\\", "/")
            pure = PurePosixPath(normalized)
            unix_type = (member.external_attr >> 16) & 0o170000
            if pure.is_absolute() or ".." in pure.parts or ":" in normalized:
                raise BootstrapError(f"Unsafe path in configuration package: {member.filename}")
            if unix_type == stat.S_IFLNK:
                raise BootstrapError(f"Symlinks are not allowed in configuration packages: {member.filename}")
            total += member.file_size
            if total > max_bytes:
                raise BootstrapError(f"Expanded package exceeds {max_bytes} bytes")
            target = (destination / Path(*pure.parts)).resolve()
            if target != destination_root and destination_root not in target.parents:
                raise BootstrapError(f"Unsafe extraction target: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def bootstrap(args: argparse.Namespace) -> dict[str, Any]:
    target_root = Path(args.target).expanduser().resolve()
    versions_root = target_root / "versions"
    requested_manifest = args.manifest or os.environ.get("SUPERCUT_CONFIG_MANIFEST_URL")
    warnings: list[str] = []

    if requested_manifest and not args.offline:
        try:
            manifest = _load_json_source(requested_manifest, args.max_bytes, args.timeout)
            manifest_source = requested_manifest
        except Exception as exc:
            manifest = _load_json_source(str(LOCAL_MANIFEST), args.max_bytes, args.timeout)
            manifest_source = str(LOCAL_MANIFEST)
            warnings.append(f"remote manifest unavailable; used bundled manifest: {exc}")
    else:
        manifest = _load_json_source(str(LOCAL_MANIFEST), args.max_bytes, args.timeout)
        manifest_source = str(LOCAL_MANIFEST)

    _validate_manifest(manifest)
    payload, package_source, package_warnings = _read_package(
        manifest,
        offline=args.offline,
        max_bytes=args.max_bytes,
        timeout=args.timeout,
    )
    warnings.extend(package_warnings)
    digest = _verify_digest(payload, manifest["package"]["sha256"])
    version = manifest["version"]
    active_path = versions_root / version
    current_path = target_root / "current.json"

    if active_path.is_dir() and current_path.is_file() and not args.force:
        try:
            current = json.loads(current_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
        if current.get("sha256") == digest:
            return {
                "status": "ready",
                "changed": False,
                "version": version,
                "active_path": str(active_path),
                "manifest_source": manifest_source,
                "package_source": package_source,
                "sha256": digest,
                "warnings": warnings,
            }

    versions_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{version}-", dir=str(versions_root)))
    backup: Path | None = None
    try:
        _safe_extract(payload, staging, args.max_bytes)
        required = [
            staging / "config" / "workflow.defaults.json",
            staging / "runtime" / "supercut.py",
            staging / "knowledge" / "effect_catalog.json",
        ]
        missing = [str(path.relative_to(staging)) for path in required if not path.is_file()]
        if missing:
            raise BootstrapError("Configuration package is incomplete: " + ", ".join(missing))
        if active_path.exists():
            suffix = time.strftime("%Y%m%d-%H%M%S")
            backup = versions_root / f"{version}.backup-{suffix}"
            active_path.replace(backup)
        staging.replace(active_path)
        current = {
            "schema": "supercut-active-config-v1",
            "version": version,
            "active_path": str(active_path),
            "sha256": digest,
            "manifest_source": manifest_source,
            "package_source": package_source,
            "installed_at_unix": int(time.time()),
        }
        _write_json_atomic(current_path, current)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup and backup.exists() and not active_path.exists():
            backup.replace(active_path)
        raise

    return {
        "status": "ready",
        "changed": True,
        "version": version,
        "active_path": str(active_path),
        "manifest_source": manifest_source,
        "package_source": package_source,
        "sha256": digest,
        "backup_path": str(backup) if backup else None,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ensure", action="store_true", help="Install if missing; otherwise verify current version")
    parser.add_argument("--manifest", help="HTTPS URL or local path to a version manifest")
    parser.add_argument("--target", default=str(_default_target()), help="Configuration home directory")
    parser.add_argument("--offline", action="store_true", help="Use only the bundled configuration package")
    parser.add_argument("--force", action="store_true", help="Reinstall the selected version and retain a backup")
    parser.add_argument("--timeout", type=float, default=20.0, help="Network timeout in seconds")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="Maximum download and expanded size")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output")
    args = parser.parse_args()
    try:
        result = bootstrap(args)
    except Exception as exc:
        failure = {"status": "error", "error": str(exc)}
        if args.json:
            print(json.dumps(failure, ensure_ascii=True, indent=2))
        else:
            print(f"ERROR: {exc}")
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
    else:
        print(f"Supercut workflow {result['version']} ready at {result['active_path']}")
        for warning in result["warnings"]:
            print(f"WARNING: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
