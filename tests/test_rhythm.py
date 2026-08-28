from __future__ import annotations

import unittest

import numpy as np

from rhythm import RHYTHM_SCHEMA, align_plan_to_rhythm, analyze_samples
from workflow import evaluate_quality


class RhythmAnalysisTests(unittest.TestCase):
    def test_click_track_produces_observed_onsets_and_inferred_grid(self) -> None:
        sample_rate = 8000
        duration = 8.0
        samples = np.zeros(round(sample_rate * duration), dtype=np.float32)
        burst_length = round(sample_rate * 0.06)
        time = np.arange(burst_length) / sample_rate
        burst = (np.sin(2 * np.pi * 90 * time) * np.hanning(burst_length)).astype(np.float32)
        expected = np.arange(0.5, duration, 0.5)
        for second in expected:
            start = round(second * sample_rate)
            samples[start : start + burst_length] += burst

        result = analyze_samples(samples, sample_rate, timeline_offset_seconds=0.25)

        self.assertEqual(result["schema"], RHYTHM_SCHEMA)
        self.assertEqual(result["status"], "complete")
        self.assertGreaterEqual(len(result["observed_onsets"]), len(expected) - 2)
        self.assertTrue(result["inferred_beats"])
        self.assertTrue(result["strong_anchors"])
        first = result["observed_onsets"][0]
        self.assertTrue(first["observed"])
        self.assertAlmostEqual(first["timeline_time"], first["time"] - 0.25, places=6)
        candidates = [row["bpm"] for row in result["analysis"]["tempo_candidates"]]
        self.assertTrue(any(abs(value - 120.0) < 5.0 for value in candidates))

    def test_plan_boundaries_snap_only_to_observed_anchors(self) -> None:
        plan = {
            "schema": "supercut-edit-plan-v1",
            "output": {"width": 1920, "height": 1080, "fps": 30},
            "shots": [
                {"index": 1, "duration": 1.85, "transition_after": "hard_cut", "transition_duration": 0.0},
                {"index": 2, "duration": 2.15, "transition_after": "hard_cut", "transition_duration": 0.0},
            ],
        }
        rhythm_map = {
            "schema": RHYTHM_SCHEMA,
            "status": "complete",
            "strong_anchors": [
                {
                    "id": "anchor-1",
                    "time": 2.0,
                    "timeline_time": 2.0,
                    "source_event_id": "onset-1",
                    "source": "observed_onset",
                    "type": "kick",
                    "strength": 0.95,
                    "confidence": 0.94,
                    "downbeat_candidate": True,
                    "downbeat_confidence": 0.8,
                }
            ],
        }
        aligned = align_plan_to_rhythm(plan, rhythm_map, max_snap_ms=200)
        self.assertAlmostEqual(aligned["shots"][0]["final_boundary"], 2.0, places=3)
        self.assertEqual(aligned["shots"][0]["beat_anchor"]["source"], "observed_onset")
        self.assertAlmostEqual(sum(row["duration"] for row in aligned["shots"]), 4.0, places=6)
        self.assertEqual(aligned["rhythm_sync"]["primary_cut_alignment_ratio"], 1.0)

    def test_required_rhythm_is_a_hard_quality_gate(self) -> None:
        report = evaluate_quality(
            {"objective": "卡点测试", "content_type": "anime", "input_mode": "script_assets", "target": {"duration": 4, "width": 1920, "height": 1080}},
            None,
            {"summary": {"missing": 0, "total": 1}},
            {"shots": [{"duration": 4, "start_frame": "a", "end_frame": "b", "action": "c", "camera": "d", "subject_anchor": "e", "transition_to_next": "hard_cut", "sound": "music"}]},
            {"global_negative": ["x"], "shots": [{}]},
            rhythm_required=True,
        )
        self.assertEqual(report["status"], "needs_input")
        self.assertIn("rhythm_analysis_missing_or_unreliable", report["critical_failures"])


if __name__ == "__main__":
    unittest.main()
