from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import numpy as np

from chatcut_bridge import OWNED_TRACKS, build_chatcut_manifest, pull_compatible_edits, snapshot_hash
from effect_runtime import SUPPORTED_PRINCIPLES, build_effect_registry, render_effect_transition
from supercut import build_plan
from workflow import (
    approve_plan,
    build_edit_brief,
    build_script_template,
    build_unassigned_base_plan,
    compose_v2,
)


ROOT = Path(__file__).resolve().parents[1]


def source_catalog() -> dict:
    return {
        "schema": "supercut-catalog-v1",
        "assets": [
            {
                "id": f"asset-{index}",
                "path": str(ROOT / f"fixtures/source-{index}.mp4"),
                "duration": 30.0,
                "motion": float(index),
                "luma": 100.0 + index,
                "priority": 1,
            }
            for index in range(1, 4)
        ],
    }


class EffectRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = json.loads((ROOT / "knowledge/effect_catalog.json").read_text(encoding="utf-8"))

    def test_every_catalog_effect_has_an_executor(self) -> None:
        registry = build_effect_registry(self.catalog)
        self.assertEqual(len(registry), 38)
        self.assertEqual({row["principle"] for row in registry.values()}, SUPPORTED_PRINCIPLES)

    def test_every_effect_renders_exact_frame_count(self) -> None:
        registry = build_effect_registry(self.catalog)
        before = [np.full((36, 64, 3), (20 + index, 50, 90), np.uint8) for index in range(6)]
        after = [np.full((36, 64, 3), (170, 100 + index, 30), np.uint8) for index in range(6)]
        for effect_id, metadata in registry.items():
            with self.subTest(effect_id=effect_id):
                frames = render_effect_transition(before, after, 4, effect_id, metadata["principle"])
                self.assertEqual(len(frames), 4)
                self.assertTrue(all(frame.shape == (36, 64, 3) for frame in frames))


class WorkflowV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.effects = json.loads((ROOT / "knowledge/effect_catalog.json").read_text(encoding="utf-8"))

    def make_script_plan(self) -> dict:
        brief = build_edit_brief(
            {
                "title": "击杀节奏测试",
                "objective": "把三段战斗素材剪成节奏清楚的高光短片",
                "script": "建立战场。发现目标并交战。完成击杀并收束。",
                "content_type": "game",
                "events": ["kill_confirm"],
                "target_duration": 12,
                "width": 640,
                "height": 360,
                "fps": 30,
            }
        )
        catalog = source_catalog()
        template = build_script_template(brief, catalog)
        base = build_plan(template, catalog, seed=3)
        annotations = {
            "shots": [
                {
                    "index": shot["index"],
                    "events": ["kill_confirm"],
                    "confidence": 0.92,
                    "start_frame": "主体进入战斗空间",
                    "action_trajectory": "主体向前移动并完成射击动作",
                    "camera_motion": "稳定跟随主体移动",
                    "end_frame": "动作落点清晰可见",
                    "environment_sound": "保留环境声和命中声",
                }
                for shot in template["shots"]
            ]
        }
        return compose_v2(brief, base, template, self.effects, annotations)

    def test_script_only_mode_passes_quality_gate_and_plans_effects(self) -> None:
        plan = self.make_script_plan()
        self.assertEqual(plan["schema"], "supercut-edit-plan-v2")
        self.assertEqual(plan["review_status"], "awaiting_user_review")
        self.assertGreaterEqual(plan["artifacts"]["quality_gate"]["score"], 85)
        self.assertTrue(plan["effects"])
        self.assertTrue(all(effect["execution_tier"] in {"generic", "specialized"} for effect in plan["effects"]))

    def test_reference_mode_requires_complete_semantic_review(self) -> None:
        brief = build_edit_brief(
            {
                "title": "参考片测试",
                "objective": "学习镜头逻辑",
                "reference": "reference.mp4",
                "content_type": "game",
                "target_duration": 8,
            }
        )
        catalog = source_catalog()
        template = build_script_template({**brief, "script": "开场。推进。收束。"}, catalog)
        template["reference"]["path"] = "reference.mp4"
        base = build_plan(template, catalog)
        plan = compose_v2(brief, base, template, self.effects, annotations=None)
        self.assertEqual(plan["review_status"], "needs_input")
        self.assertIn("reference_shot_semantics_missing", plan["artifacts"]["quality_gate"]["critical_failures"])
        with self.assertRaises(ValueError):
            approve_plan(plan)

    def test_script_without_assets_reaches_needs_input_and_storyboard(self) -> None:
        brief = build_edit_brief(
            {
                "title": "纯文案",
                "objective": "先设计三镜头故事板",
                "script": "建立主体。完成动作。展示结果。",
                "content_type": "short_drama",
                "target_duration": 9,
            }
        )
        empty_catalog = {"schema": "supercut-catalog-v1", "assets": []}
        template = build_script_template(brief, empty_catalog)
        plan = compose_v2(brief, build_unassigned_base_plan(template), template, self.effects)
        self.assertEqual(plan["review_status"], "needs_input")
        self.assertIn("source_coverage_missing", plan["artifacts"]["quality_gate"]["critical_failures"])
        manifest = build_chatcut_manifest(plan)
        self.assertTrue(manifest["items"])
        self.assertTrue(all(item["track"] == "SC_STORYBOARD" for item in manifest["items"]))

    def test_approval_is_explicit(self) -> None:
        approved = approve_plan(self.make_script_plan())
        self.assertEqual(approved["review_status"], "approved")
        self.assertEqual(approved["artifacts"]["quality_gate"]["status"], "approved")

    def test_chatcut_manifest_forbids_ai_and_uses_owned_tracks(self) -> None:
        plan = self.make_script_plan()
        manifest = build_chatcut_manifest(plan)
        self.assertFalse(manifest["policy"]["chatcut_ai_allowed"])
        self.assertIn("video_generation", manifest["policy"]["forbidden_operations"])
        self.assertEqual({track["name"] for track in manifest["tracks"]}, set(OWNED_TRACKS))
        self.assertTrue(any(item["track"] == "SC_VFX_PREVIEW" for item in manifest["items"]))
        self.assertEqual(manifest["connector_handoff"]["executor"], "codex_chatcut_plugin")
        self.assertIn("live_editor_url", manifest["connector_handoff"]["completion_evidence"])

    def test_chatcut_pull_updates_supported_fields_and_detects_conflicts(self) -> None:
        plan = self.make_script_plan()
        manifest = build_chatcut_manifest(plan)
        shot = plan["shots"][0]
        snapshot = {
            "project_id": "project-1",
            "timeline_id": "timeline-1",
            "items": [
                {
                    "id": "remote-1",
                    "track": "SC_MAIN_A",
                    "start": 0.0,
                    "duration": 2.25,
                    "source_start": 1.5,
                    "speed": 1.2,
                    "metadata": {"supercut_shot_id": shot["id"]},
                }
            ],
        }
        if plan["effects"]:
            snapshot["items"].append(
                {
                    "id": "remote-effect-1",
                    "track": "SC_VFX_PREVIEW",
                    "start": 3.5,
                    "duration": 0.4,
                    "metadata": {"supercut_effect_id": plan["effects"][0]["id"]},
                }
            )
        mapping = copy.deepcopy(manifest["mapping"])
        mapping["remote_snapshot_hash"] = snapshot_hash(snapshot)
        updated_snapshot = copy.deepcopy(snapshot)
        updated_snapshot["items"][0]["duration"] = 2.0
        updated, report = pull_compatible_edits(plan, updated_snapshot, mapping)
        self.assertEqual(report["status"], "updated")
        self.assertEqual(updated["shots"][0]["duration"], 2.0)
        self.assertEqual(updated["shots"][0]["source_start"], 1.5)
        self.assertEqual(updated["effects"][0]["duration"], 0.4)
        self.assertEqual(report["updated_effect_count"], 1)
        self.assertEqual(updated["review_status"], "awaiting_user_review")

        locally_changed = copy.deepcopy(plan)
        locally_changed["plan_revision"] = "different-local-revision"
        _, conflict = pull_compatible_edits(locally_changed, updated_snapshot, mapping)
        self.assertEqual(conflict["status"], "conflict")


if __name__ == "__main__":
    unittest.main()
