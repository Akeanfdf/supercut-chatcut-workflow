"""Pure-data bridge between SuperCut plans and the ChatCut connector surface.

The Python workflow does not call ChatCut's AI or guess private API routes.  It
produces an idempotent manifest for the Codex ChatCut plugin and can reconcile
an exported timeline snapshot back into compatible SuperCut fields.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


OWNED_TRACKS = [
    "SC_STORYBOARD",
    "SC_MAIN_A",
    "SC_MAIN_B",
    "SC_VFX_PREVIEW",
    "SC_TITLES",
    "SC_AUDIO",
    "SC_SFX",
    "SC_NOTES",
]


def _digest(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


def _asset_id(path: str) -> str:
    return f"asset-{hashlib.sha1(path.casefold().encode('utf-8')).hexdigest()[:12]}"


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    normalized = {
        "project_id": snapshot.get("project_id"),
        "timeline_id": snapshot.get("timeline_id"),
        "items": sorted(
            [
                {
                    "shot_id": item.get("shot_id") or item.get("metadata", {}).get("supercut_shot_id"),
                    "track": item.get("track"),
                    "start": item.get("start"),
                    "duration": item.get("duration"),
                    "source_start": item.get("source_start"),
                    "speed": item.get("speed", 1.0),
                }
                for item in snapshot.get("items", [])
                if str(item.get("track", "")).startswith("SC_")
            ],
            key=lambda row: (str(row["track"]), float(row["start"] or 0.0), str(row["shot_id"])),
        ),
    }
    return _digest(normalized)


def build_chatcut_manifest(
    plan: dict[str, Any],
    project_name: str | None = None,
    proxy_root: str = "output/chatcut/proxies",
    effect_proxy_root: str = "output/chatcut/effects",
) -> dict[str, Any]:
    if plan.get("schema") != "supercut-edit-plan-v2":
        raise ValueError("ChatCut synchronization requires supercut-edit-plan-v2")
    output = plan["output"]
    project_name = project_name or plan.get("brief", {}).get("title") or "SuperCut Review"
    assets: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    cursor = 0.0
    effects_by_shot = {effect["after_shot_id"]: effect for effect in plan.get("effects", [])}

    for index, shot in enumerate(plan.get("shots", [])):
        source = str(shot.get("source") or "")
        duration = float(shot.get("duration", 0.0))
        effect = effects_by_shot.get(shot["id"])
        overlap = float(effect.get("duration", 0.0)) if effect else 0.0
        if source:
            asset_id = _asset_id(source)
            suffix = Path(source).suffix or ".mp4"
            assets.setdefault(
                asset_id,
                {
                    "id": asset_id,
                    "kind": "source_proxy",
                    "original_path": source,
                    "proxy_path": str(Path(proxy_root) / f"{asset_id}{suffix}"),
                    "proxy_policy": {
                        "max_width": 1920,
                        "max_height": 1080,
                        "fps": 30,
                        "preserve_duration_and_timecode": True,
                    },
                },
            )
            items.append(
                {
                    "id": f"cc-{shot['id']}",
                    "track": "SC_MAIN_A" if index % 2 == 0 else "SC_MAIN_B",
                    "asset_id": asset_id,
                    "timeline_start": round(cursor, 6),
                    "duration": duration,
                    "source_start": float(shot.get("source_start", 0.0)),
                    "speed": float(shot.get("speed", 1.0)),
                    "fit": "cover",
                    "metadata": {
                        "supercut_shot_id": shot["id"],
                        "story_beat": shot.get("story_beat"),
                        "plan_revision": plan["plan_revision"],
                    },
                }
            )
        else:
            items.append(
                {
                    "id": f"story-{shot['id']}",
                    "track": "SC_STORYBOARD",
                    "kind": "placeholder_card",
                    "timeline_start": round(cursor, 6),
                    "duration": duration,
                    "text": shot.get("story_beat") or "待补素材",
                    "metadata": {"supercut_shot_id": shot["id"], "missing_source": True},
                }
            )
        if effect:
            effect_proxy = str(Path(effect_proxy_root) / f"{effect['id']}.mp4")
            effect_asset_id = _asset_id(effect_proxy)
            assets[effect_asset_id] = {
                "id": effect_asset_id,
                "kind": "exact_effect_proxy",
                "proxy_path": effect_proxy,
                "effect_id": effect["effect_id"],
                "principle": effect["principle"],
            }
            items.append(
                {
                    "id": f"cc-{effect['id']}",
                    "track": "SC_VFX_PREVIEW",
                    "asset_id": effect_asset_id,
                    "timeline_start": round(cursor + duration - overlap, 6),
                    "duration": overlap,
                    "source_start": 0.0,
                    "speed": 1.0,
                    "metadata": {
                        "supercut_effect_id": effect["id"],
                        "effect_id": effect["effect_id"],
                        "selection_reason": effect.get("selection_reason"),
                        "editable_fields": ["timeline_start", "duration"],
                    },
                }
            )
            items.append(
                {
                    "id": f"note-{effect['id']}",
                    "track": "SC_NOTES",
                    "kind": "marker",
                    "timeline_start": round(cursor + duration - overlap, 6),
                    "duration": overlap,
                    "text": f"{effect['effect_id']} · {effect.get('role', '')}",
                    "metadata": {"supercut_effect_id": effect["id"]},
                }
            )
        cursor += duration - overlap

    manifest = {
        "schema": "supercut-chatcut-manifest-v1",
        "project": {
            "name": project_name,
            "existing_project_id": plan.get("sync", {}).get("project_id"),
            "existing_timeline_id": plan.get("sync", {}).get("timeline_id"),
            "canvas": {
                "width": int(output["width"]),
                "height": int(output["height"]),
                "fps": float(output["fps"]),
            },
        },
        "policy": {
            "chatcut_ai_allowed": False,
            "allowed_operations": [
                "create_or_target_project",
                "import_proxy_media",
                "create_tracks",
                "place_and_trim_items",
                "read_timeline",
                "open_live_editor",
            ],
            "forbidden_operations": [
                "agent_chat",
                "video_generation",
                "image_generation",
                "voice_generation",
                "music_generation",
            ],
            "owned_tracks": list(OWNED_TRACKS),
            "delete_unowned_items": False,
            "external_upload_requires_user_authorization": True,
        },
        "assets": list(assets.values()),
        "tracks": [
            {"name": name, "kind": "audio" if name in {"SC_AUDIO", "SC_SFX"} else "video"}
            for name in OWNED_TRACKS
        ],
        "items": items,
        "connector_handoff": {
            "executor": "codex_chatcut_plugin",
            "sequence": [
                "verify_chatcut_tools_are_connected",
                "create_or_target_manifest_project",
                "surface_the_returned_live_editor_url",
                "import_every_ready_proxy_asset",
                "create_or_reuse_owned_tracks",
                "upsert_manifest_items_by_stable_id",
                "read_back_owned_tracks_and_record_snapshot_hash",
                "open_or_reuse_the_exact_returned_editor_url",
            ],
            "stop_conditions": [
                "chatcut_connector_not_installed_or_not_authenticated",
                "external_proxy_upload_not_authorized",
                "proxy_report_not_ready",
                "project_access_or_revision_conflict",
            ],
            "completion_evidence": [
                "project_id",
                "timeline_id",
                "live_editor_url",
                "remote_snapshot_hash",
            ],
        },
        "mapping": {
            "base_plan_revision": plan["plan_revision"],
            "remote_snapshot_hash": plan.get("sync", {}).get("remote_snapshot_hash"),
            "shot_item_ids": {
                item["metadata"]["supercut_shot_id"]: item["id"]
                for item in items
                if item.get("metadata", {}).get("supercut_shot_id") and item["track"].startswith("SC_MAIN")
            },
            "effect_item_ids": {
                item["metadata"]["supercut_effect_id"]: item["id"]
                for item in items
                if item.get("metadata", {}).get("supercut_effect_id") and item["track"] == "SC_VFX_PREVIEW"
            },
        },
        "review": {
            "required_before_final_render": True,
            "current_status": plan.get("review_status"),
            "quality_score": plan.get("artifacts", {}).get("quality_gate", {}).get("score"),
        },
    }
    manifest["manifest_hash"] = _digest(manifest)
    return manifest


def pull_compatible_edits(
    plan: dict[str, Any],
    snapshot: dict[str, Any],
    mapping: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply supported ChatCut edits or return a non-destructive conflict report."""
    if plan.get("schema") != "supercut-edit-plan-v2":
        raise ValueError("ChatCut pull requires supercut-edit-plan-v2")
    current_remote_hash = snapshot_hash(snapshot)
    base_plan_revision = mapping.get("base_plan_revision")
    base_remote_hash = mapping.get("remote_snapshot_hash")
    local_changed = bool(base_plan_revision and plan.get("plan_revision") != base_plan_revision)
    remote_changed = bool(base_remote_hash and current_remote_hash != base_remote_hash)
    conflicts: list[dict[str, Any]] = []
    if local_changed and remote_changed:
        conflicts.append(
            {
                "type": "concurrent_local_and_remote_changes",
                "message": "本地计划与ChatCut时间线均已变化，拒绝自动覆盖。",
                "local_revision": plan.get("plan_revision"),
                "base_revision": base_plan_revision,
                "remote_snapshot_hash": current_remote_hash,
                "base_remote_snapshot_hash": base_remote_hash,
            }
        )
        return copy.deepcopy(plan), {
            "schema": "supercut-chatcut-pull-report-v1",
            "status": "conflict",
            "conflicts": conflicts,
            "ignored": [],
        }

    remote_items = []
    remote_effect_items = []
    ignored: list[dict[str, Any]] = []
    for item in snapshot.get("items", []):
        track = str(item.get("track", ""))
        metadata = item.get("metadata", {})
        shot_id = item.get("shot_id") or metadata.get("supercut_shot_id")
        if track.startswith("SC_MAIN") and shot_id:
            remote_items.append({**item, "shot_id": shot_id})
        elif track == "SC_VFX_PREVIEW" and metadata.get("supercut_effect_id"):
            remote_effect_items.append({**item, "effect_id": metadata["supercut_effect_id"]})
        elif track.startswith("SC_") and track not in {"SC_VFX_PREVIEW", "SC_NOTES"}:
            ignored.append({"item_id": item.get("id"), "track": track, "reason": "unsupported_pull_item"})
    remote_items.sort(key=lambda item: (float(item.get("start", item.get("timeline_start", 0.0))), str(item["shot_id"])))
    local_by_id = {shot["id"]: shot for shot in plan.get("shots", [])}
    updated_shots: list[dict[str, Any]] = []
    for item in remote_items:
        shot = local_by_id.get(item["shot_id"])
        if shot is None:
            ignored.append({"item_id": item.get("id"), "reason": "unknown_supercut_shot_id"})
            continue
        updated = copy.deepcopy(shot)
        updated["source_start"] = float(item.get("source_start", updated.get("source_start", 0.0)))
        updated["duration"] = max(0.04, float(item.get("duration", updated.get("duration", 0.04))))
        updated["speed"] = max(0.1, float(item.get("speed", updated.get("speed", 1.0))))
        updated_shots.append(updated)
    seen = {shot["id"] for shot in updated_shots}
    updated_shots.extend(copy.deepcopy(shot) for shot in plan.get("shots", []) if shot["id"] not in seen)
    for index, shot in enumerate(updated_shots, start=1):
        shot["index"] = index

    result = copy.deepcopy(plan)
    result["shots"] = updated_shots
    effects_by_id = {effect["id"]: effect for effect in result.get("effects", [])}
    updated_effect_count = 0
    for item in remote_effect_items:
        effect = effects_by_id.get(item["effect_id"])
        if effect is None:
            ignored.append({"item_id": item.get("id"), "reason": "unknown_supercut_effect_id"})
            continue
        effect["timeline_start"] = max(0.0, float(item.get("start", item.get("timeline_start", effect["timeline_start"]))))
        effect["duration"] = max(0.04, float(item.get("duration", effect["duration"])))
        updated_effect_count += 1
    result["review_status"] = "awaiting_user_review"
    result["sync"]["project_id"] = snapshot.get("project_id")
    result["sync"]["timeline_id"] = snapshot.get("timeline_id")
    result["sync"]["remote_snapshot_hash"] = current_remote_hash
    revision_source = {"brief": result.get("brief"), "shots": updated_shots, "effects": result.get("effects", [])}
    result["plan_revision"] = _digest(revision_source)
    return result, {
        "schema": "supercut-chatcut-pull-report-v1",
        "status": "updated",
        "updated_shot_count": len(remote_items),
        "updated_effect_count": updated_effect_count,
        "new_plan_revision": result["plan_revision"],
        "remote_snapshot_hash": current_remote_hash,
        "conflicts": conflicts,
        "ignored": ignored,
    }
