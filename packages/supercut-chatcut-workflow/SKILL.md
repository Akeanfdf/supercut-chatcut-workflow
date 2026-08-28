---
name: supercut-chatcut-workflow
description: "Execute a reusable effect-aware video editing workflow for reference-driven or original-planning edits, including real Beat/Onset analysis, narrative shot design, a 38-effect catalog, typography, two quality gates, editable ChatCut review, and final verification. Use when the user asks to run, import, or package the 超级剪辑终版工作流; imitate a reference video's editing, transitions, rhythm, or cinematography; create an original edit from script and footage; beat-sync an AMV, MAD, game, drama, or livestream edit; or continue a ChatCut project through final delivery."
---

# Supercut ChatCut Workflow

Run the complete workflow; do not jump directly from input files to timeline cuts.

## First invocation

1. Run `python scripts/bootstrap.py --ensure --json` from this skill directory.
2. Use the returned `active_path` as the versioned runtime/config root.
3. If a remote manifest is configured, let the bootstrapper download and verify it. If it falls back to the bundled pack, continue normally and report the fallback only when it affects the user's request.
4. Read `references/WORKFLOW.md` completely before planning or mutating a project.
5. Read `references/CONFIGURATION.md` when installing, publishing, updating, or diagnosing this workflow.

The bootstrapper must never execute downloaded code during installation. A downloaded pack is data and local runtime source only; execution happens later under the user's current task authorization.

When an input is a Bilibili URL, acquire it through the bundled runtime before analysis. Accept desktop/mobile `bilibili.com/video/BV…` links and public `b23.tv` redirects. Require an explicit media-rights confirmation before downloading; without it, emit `acquisition_manifest.json` with `needs_input` and stop acquisition. Never read browser cookies or bypass login, payment, regional, or other access controls. A failed public download must fall back to a request for local legally held media, not weaker authorization.

## Select the entry mode

- Use **reference-driven** when a reference video is supplied. Extract reusable visual grammar, not protected characters, brands, dialogue, or story content.
- Use **original-planning** when only a brief/script and source media are supplied. Design the visual grammar and identify missing shots.
- Both modes enter the same asset-coverage check, quality gates, effect planning, ChatCut review, approval, and verification stages.

Infer discoverable values from attached files, the active task, and the active ChatCut project. Ask only for missing information that would materially change the edit or authorize an external/state-changing action.

## Execute the workflow

1. Normalize the request into `edit_brief.json` using `assets/defaults/request-template.json` as the schema example.
2. Analyze a reference into a gap-free shot timeline or design an original `visual_grammar.json`.
3. Analyze the actual BGM waveform with the bundled `runtime/rhythm.py`. Run `python runtime/supercut.py analyze-rhythm --music <path> --out <beat_map.json>` when a reusable map is needed, or use `all --rhythm-mode required` for an enforced end-to-end run. Keep waveform-observed Onsets separate from inferred Beat/downbeat grids. Use only `strong_anchors` whose `source` is `observed_onset` as automatic cut anchors; never present a guessed BPM grid as a measured drum hit.
4. Produce `asset_coverage.json`, then `shot_blueprint.json` and any required generation prompt pack.
5. Run quality gate 1. For a requested beat-synced result, require `beat_map.json`, observed Onsets, strong anchors, at least 70% primary-cut alignment, and no more than 120ms p95 alignment error. A score below the configured threshold or any hard failure yields `needs_input`; do not generate, sync, or claim completion.
6. Produce `supercut-edit-plan-v2` with source in/out points, duration, speed curves, story stage, beat anchors, micro-events, text, sound, effects, reasons, and fallbacks.
7. Select effects catalog-first. Apply an effect only when its source conditions and confidence threshold pass; otherwise use an editorial hard cut, action match, composition match, sound bridge, or simple transition.
8. If ChatCut is available, preserve editable original clip relationships and use it as the visual review layer. Load and follow the active ChatCut basic/import/verification skills. Use generation, shader, motion-graphics, voice, music, transcription, or export skills only when the user request requires them.
9. Pull user edits explicitly. Detect local/ChatCut conflicts rather than silently overwriting either side.
10. Run quality gate 2 with structural inspection, composited-pixel checks, and an audible full-playback review.
11. Require user approval before final render/export. Verify decode, duration, frame rate, sync, black frames, audio gaps, visual continuity, effect frame positions, and text safety before reporting completion.

## Editing invariants

- Narrative and action continuity can override mechanical beat cutting.
- Strong beats carry primary cuts; secondary onsets may drive within-shot events, typography, exposure pulses, or restrained accents.
- An `observed_onset` is waveform evidence. `inferred_beats` and downbeats are structural estimates and must retain their confidence and inference labels.
- Under `balanced`, average no more than one obvious effect per six seconds and keep strong effects at least six seconds apart unless the user's explicit style target requires denser treatment.
- Confidence below `0.7` falls back to a basic editorial transition.
- Text must have a verified source, sufficient reading time, and frame-aware subject avoidance.
- Generated or pre-rendered effect proxies cannot replace the editable source timeline.
- Do not import media the user does not own or have permission to use. Do not download a referenced film, song, or video merely because a URL was supplied.

## Standard triggers

Recognize these phrases as full-workflow triggers:

- `执行超级剪辑终版工作流`
- `按终版工作流剪辑`
- `参考片复刻终版`
- `原创规划终版`
- `$supercut-chatcut-workflow`

The minimal link-mode command is:

```text
python runtime/supercut.py all --reference <B站URL> --source <B站URL> --music <本地音频> --confirm-media-rights
```

`--source-dir` remains supported for existing local workflows.

Use `assets/defaults/request-template.json` for a structured trigger. Preserve the state machine and the two quality gates even when the user uses a shorter phrase.

## Completion contract

Report completion only if the state is neither `needs_input` nor `conflict_reported`, all intended timeline ranges are covered, real Beat/Onset and action landing points were checked, effects have valid source conditions, text is verified and readable, composited pixels and audible playback were reviewed, and ChatCut retains editable source relationships.
