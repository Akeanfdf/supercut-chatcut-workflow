from __future__ import annotations

import json
import unittest
from pathlib import Path

from supercut import recommend_effects


ROOT = Path(__file__).resolve().parents[1]


class EffectKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = json.loads((ROOT / "knowledge" / "effect_catalog.json").read_text(encoding="utf-8"))

    def test_catalog_size_and_balanced_coverage(self) -> None:
        effects = self.catalog["effects"]
        self.assertEqual(len(effects), 38)
        self.assertEqual(self.catalog["summary"]["runtime_supported_count"], 38)
        self.assertEqual(self.catalog["summary"]["generic_runtime_count"], 23)
        coverage = self.catalog["summary"]["content_type_coverage"]
        for content_type in ("game", "anime", "short_drama", "live_stream"):
            self.assertGreaterEqual(coverage[content_type], 3)

    def test_prompts_and_implemented_paths(self) -> None:
        for effect in self.catalog["effects"]:
            prompt = effect["usage_profile"]["selection_prompt"]
            self.assertTrue(prompt.startswith("当素材属于"), effect["effect_id"])
            self.assertTrue(prompt.endswith("。"), effect["effect_id"])
            implementation = effect["implementation"]
            if implementation["status"] == "implemented":
                self.assertTrue((ROOT / implementation["template_path"]).is_file(), effect["effect_id"])
                self.assertTrue((ROOT / implementation["renderer_path"]).is_file(), effect["effect_id"])

    def test_recommendations_are_scenario_specific(self) -> None:
        scenarios = [
            ("game", ["kill_confirm"]),
            ("anime", ["eye_open"]),
            ("short_drama", ["emotion_turn"]),
            ("live_stream", ["camera_change"]),
        ]
        top_ids = []
        for content_type, events in scenarios:
            results = recommend_effects(self.catalog, content_type, events, 5)
            self.assertTrue(results)
            self.assertIn(events[0], results[0]["matched_events"])
            top_ids.append(results[0]["effect_id"])
        self.assertGreaterEqual(len(set(top_ids)), 3)


if __name__ == "__main__":
    unittest.main()
