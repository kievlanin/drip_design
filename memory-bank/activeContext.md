# Active context

**Last aligned with repo docs:** 2026-04-24 — see [PROJECT_STATE.md](../PROJECT_STATE.md) session notes.

**Last memory-bank review:** full pass over all memory-bank files on user doc/context refresh; compared to [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md) / `PROJECT_STATE.md` including the 2026-04-24 canvas-layer and block-dialog session notes.

## Current focus

- **Canvas layer manager:** `dripcad_legacy.py` now owns grouped canvas layers with `visible/selectable` state, a dedicated `Шари...` dialog, empty-canvas RMB entry point, and the enforced rule `selectable => visible`.
- **Granular block layers:** block rendering/picking is split into `block.boundaries`, `block.laterals`, `block.submain.base`, `block.valves`, and dynamic `block.submain.od:*`; trunk keeps graph/node layers plus dynamic `trunk.pipes.od:*`; labels stay under `cosmetic.labels`.
- **Valve label UX:** field-valve labels are multiline, left-aligned, and draggable with the two-click `SUB_LABEL` flow; positions persist in `consumer_schedule.field_valve_label_pos`.
- **Emitter block equivalent (`K_eq`) and audits:** `emitter_block_equivalent.py` + integration in `hydraulics_core` remain active, with `block_equivalent_emitter`, `lateral_flow_audit`, and per-submain-branch emitter Q extrema overlay.
- **Block dialog UX baseline:** `open_block_irrigation_scheme_dialog` shows `K_eq`, `P_ref`, editable `Hвст.`, live `Q_total @ H`, `% to Q_nom`, inverse `H for Q_nom`, and no longer wipes hydro on no-op `OK` / `Apply`.
- **Canvas/map ПКМ routing:** right-click still uses the shared world-pick router (`trunk_node` / `trunk_seg` / `block`); consumer/valve nodes expose `Властивості…` to open `_open_trunk_consumer_schedule_dialog`.

## When starting a task

1. Read all files in `memory-bank/` (per workspace rule).
2. For operational detail, open `PROJECT_CONTEXT.md` and the latest sections of `PROJECT_STATE.md`.

## Next steps (from project roadmap — not necessarily in progress)

- Optional plans under `docs/plans/` (magistral profile audit, Earthdata bearer path, emitter-block K_eq).
- Longer-term: split `dripcad_legacy.py`, official JSON DTO schemas, deeper BOM/trunk integration.

## Latest completed in this session

- Synced `PROJECT_CONTEXT.md`, `PROJECT_STATE.md`, `memory-bank/activeContext.md`, `memory-bank/progress.md`, and `memory-bank/systemPatterns.md` with the latest 2026-04-24 repo state.
- Captured the new canvas-layer hierarchy, dynamic submain `Ø` layers, valve-label drag persistence, and per-branch Qmin/Qmax overlay behavior.
- Preserved the earlier 2026-04-24 block-dialog notes (`Hвст.`, inverse `H for Q_nom`, no-op `OK` / `Apply`, consumer-node `Властивості…`) as still-current baseline.

## Decisions / constraints to remember

- Project root and paths: `main_app/paths.py` (`PROJECT_ROOT`, `DESIGNS_DIR`, `PIPES_DB_PATH`, `SRTM_DIR`); do not rely on `chdir`.
- Saves go under `designs/<project>/` with sanitized names.
- Full-file handoffs: no placeholder snippets when the user asks for complete files.
