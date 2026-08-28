import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
import io


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "packages" / "supercut-chatcut-workflow"


class WorkflowPackageTests(unittest.TestCase):
    def _recursive_text(self, label, payload):
        output = []
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for name in archive.namelist():
                if name.endswith("/"):
                    continue
                data = archive.read(name)
                if name.lower().endswith(".zip"):
                    output.extend(self._recursive_text(f"{label}!{name}", data))
                elif Path(name).suffix.lower() in {".md", ".json", ".yaml", ".yml", ".py", ".ps1", ".txt"}:
                    output.append((f"{label}!{name}", data.decode("utf-8", errors="ignore")))
        return output

    def test_embedded_config_digest_and_inventory(self):
        manifest = json.loads((SKILL / "assets" / "config-source.json").read_text(encoding="utf-8"))
        package = SKILL / "assets" / manifest["package"]["embedded"]
        self.assertEqual(hashlib.sha256(package.read_bytes()).hexdigest(), manifest["package"]["sha256"])
        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            self.assertIn("runtime/supercut.py", names)
            self.assertIn("runtime/rhythm.py", names)
            self.assertIn("config/workflow.defaults.json", names)
            catalog = json.loads(archive.read("knowledge/effect_catalog.json").decode("utf-8"))
        self.assertEqual(catalog["summary"]["effect_count"], 38)
        self.assertEqual(catalog["summary"]["runtime_supported_count"], 38)

    def test_offline_bootstrap_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="supercut-bootstrap-test-") as temporary:
            command = [
                sys.executable,
                "-X",
                "utf8",
                str(SKILL / "scripts" / "bootstrap.py"),
                "--ensure",
                "--offline",
                "--target",
                temporary,
                "--json",
            ]
            first = json.loads(subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8").stdout)
            second = json.loads(subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8").stdout)
            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertTrue((Path(first["active_path"]) / "runtime" / "supercut.py").is_file())

    def test_distribution_does_not_embed_current_case_paths(self):
        version = (SKILL / "VERSION").read_text(encoding="utf-8").strip()
        release = ROOT / "dist" / f"supercut-chatcut-workflow-{version}" / f"supercut-chatcut-workflow-{version}.zip"
        forbidden = [
            "E" + ":/browser" + "download",
            "54df2317" + "-f9d6-4278-b501-b4af749d2081",
            "M500001" + "DZx7G3A0yck",
        ]
        searchable = self._recursive_text(release.name, release.read_bytes())
        merged = "\n".join(text for _, text in searchable)
        for marker in forbidden:
            self.assertNotIn(marker, merged)
        self.assertNotRegex(merged, r"(?i)(?<![A-Za-z])[A-Z]:[\\/]")
        self.assertNotRegex(merged, r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b")


if __name__ == "__main__":
    unittest.main()
