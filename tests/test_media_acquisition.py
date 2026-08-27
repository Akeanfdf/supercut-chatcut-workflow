import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import media_acquisition as acquisition
from supercut import build_parser


class _RedirectResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return "https://m.bilibili.com/video/BV17a8CzLEgZ/?p=3"


class MediaAcquisitionTests(unittest.TestCase):
    def test_parses_desktop_mobile_and_part(self):
        desktop = acquisition.parse_bilibili_url(
            "https://www.bilibili.com/video/BV17a8CzLEgZ/?p=2", resolve_short=False
        )
        mobile = acquisition.parse_bilibili_url(
            "https://m.bilibili.com/video/BV17a8CzLEgZ", resolve_short=False
        )
        self.assertEqual((desktop.bvid, desktop.part), ("BV17a8CzLEgZ", 2))
        self.assertEqual((mobile.bvid, mobile.part), ("BV17a8CzLEgZ", 1))

    def test_rejects_non_bilibili_domain(self):
        with self.assertRaises(ValueError):
            acquisition.parse_bilibili_url("https://example.com/video/BV17a8CzLEgZ")

    @mock.patch("media_acquisition.urlopen", return_value=_RedirectResponse())
    def test_resolves_b23_public_redirect(self, patched):
        result = acquisition.parse_bilibili_url("https://b23.tv/example")
        self.assertEqual((result.bvid, result.part), ("BV17a8CzLEgZ", 3))
        self.assertTrue(patched.called)

    def test_rights_gate_creates_requirement_shape(self):
        report = acquisition.acquisition_requirement_report(
            "https://www.bilibili.com/video/BV17a8CzLEgZ/", None
        )
        self.assertEqual(report["status"], "needs_input")
        self.assertFalse(report["download_attempted"])

    def test_local_file_manifest_and_cache_shape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "owned.mp4"
            source.write_bytes(b"owned-test-media")
            resolved, record = acquisition.resolve_input(
                str(source), root / "cache", confirm_media_rights=False
            )
            manifest_path = root / "acquisition_manifest.json"
            acquisition.write_manifest(manifest_path, [{**record, "role": "source"}])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(resolved, source.resolve())
            self.assertEqual(manifest["status"], "acquired")
            self.assertEqual(len(manifest["items"][0]["sha256"]), 64)
            self.assertFalse(manifest["privacy"]["browser_cookies_read"])

    @mock.patch("media_acquisition._yt_dlp_command", return_value=["yt-dlp"])
    @mock.patch("media_acquisition.subprocess.run")
    def test_download_failure_becomes_needs_input_error(self, run, _command):
        run.return_value = mock.Mock(returncode=1, stderr="restricted", stdout="")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(acquisition.AcquisitionError):
                acquisition.acquire_bilibili(
                    "https://www.bilibili.com/video/BV17a8CzLEgZ/",
                    Path(temporary),
                    confirm_media_rights=True,
                )

    def test_legacy_source_dir_and_new_source_flags(self):
        parser = build_parser()
        legacy = parser.parse_args(["all", "--reference", "ref.mp4", "--source-dir", "clips"])
        current = parser.parse_args(["all", "--reference", "ref.mp4", "--source", "clip.mp4"])
        self.assertEqual(legacy.source_dir, "clips")
        self.assertEqual(current.source, "clip.mp4")


if __name__ == "__main__":
    unittest.main()
