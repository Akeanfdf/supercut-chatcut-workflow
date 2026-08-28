# Configuration and distribution

## What is portable

The release contains the Codex skill plus an embedded, versioned configuration pack. The pack includes:

- the local Supercut runtime and Python requirements;
- the complete 38-effect catalog and effect knowledge files;
- effect templates and local rendering helpers;
- workflow defaults, trigger schema, quality-gate policy, and ChatCut track policy;
- the full workflow reference.

Personal media, downloaded reference videos, project output, local drive paths, ChatCut project IDs, credentials, and FFmpeg binaries are intentionally excluded.

Version 1.1 adds a rights-gated Bilibili acquisition layer. It accepts public BV links, mobile links, and public `b23.tv` redirects, records URL/BV/part/format/duration/SHA-256 in `acquisition_manifest.json`, and keeps downloads in a private cache. It never imports browser cookies or bypasses access controls.

Version 1.2 adds the packaged `runtime/rhythm.py`, `supercut-beat-map-v2`, observed-Onset cut anchoring, strict rhythm quality metrics, BGM offset handling, and editable `SC_AUDIO` placement. FFmpeg remains an external dependency because waveform decoding and final rendering must use the same deterministic media toolchain.

## First-run behavior

`scripts/bootstrap.py --ensure --json` reads `assets/config-source.json` and selects the configured stable version. If `package.url` is a valid HTTPS URL, it downloads that ZIP. It verifies SHA-256, enforces a size limit, rejects symlinks and unsafe paths, and then installs into a versioned directory. If the remote manifest or package is unavailable and fallback is enabled, it activates the bundled ZIP.

Default configuration home:

- `SUPERCUT_WORKFLOW_HOME`, when set;
- otherwise `~/.supercut-chatcut`.

The active version is recorded in `current.json`. Re-running bootstrap is idempotent. `--force` retains the replaced version as a timestamped backup.

## Publish a remote configuration update

1. Update the runtime/config files and increment `VERSION`.
2. Run `python scripts/build_release.py` from the skill directory.
3. Upload the generated configuration ZIP to an HTTPS endpoint.
4. Set `package.url`, `package.sha256`, and `package.size_bytes` in `assets/config-source.json`.
5. Re-run `python scripts/build_release.py` so the skill ZIP carries the published manifest and bundled fallback.
6. Optionally host a separate manifest and invoke bootstrap with `--manifest https://example/manifest.json`, or set `SUPERCUT_CONFIG_MANIFEST_URL`.

Do not publish a URL before the file exists. Never omit or weaken the SHA-256 check for remote packages.

## Runtime dependencies

The bootstrapper installs configuration and runtime source but does not silently install system software or Python packages. Before rendering, verify Python 3.10+, the requirements in `runtime/requirements.txt`, and FFmpeg/FFprobe. Prefer an explicit FFmpeg path or `PATH`; portable configuration must not contain a publisher's local drive path.

ChatCut remains an editable review layer. The ChatCut connector/plugin must be installed and authenticated separately because account authorization cannot be bundled safely.

The verified downloader dependency for this release is `yt-dlp==2026.8.19`. FFmpeg/FFprobe remain external tools discovered through the command line, environment, project configuration, or `PATH`.

## Version compatibility

- Skill package and bundled config use matching semantic versions.
- An updated remote manifest may point to a newer config version when the schema stays `supercut-config-source-v1`.
- Change the manifest schema for incompatible installer behavior.
- Keep older versions in the configuration home until the new version passes both quality gates on a real edit.
