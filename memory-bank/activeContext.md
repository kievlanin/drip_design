# Active context

**Last aligned with repo docs:** 2026-04-24 — see [PROJECT_STATE.md](../PROJECT_STATE.md) session notes.

**Last memory-bank review:** full pass over all six files on user doc/context refresh; compared to [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md) / `PROJECT_STATE.md` including 2026-04-24 session notes.

## Current focus

- **Emitter block equivalent (`K_eq`)**: `modules/hydraulic_module/emitter_block_equivalent.py` + integration in `hydraulics_core` (`calc_results["block_equivalent_emitter"]`), where `x` is taken from selected dripper model only (no `x` fitting fallback).
- **Block flow audit/UI:** `calc_results["lateral_flow_audit"]` with min/max/Qnom statuses; Block dialog now shows `K_eq`, `P_ref`, editable `Hвст.`, live `Q_total @ H`, `% to Q_nom`, and inverse `H for Q_nom`.
- **Block dialog no-op apply fix:** `OK` / `Apply` in `open_block_irrigation_scheme_dialog` no longer wipe existing hydro just by touching traced global `StringVar`; sync into `self.var_*` happens only when parameters actually require recalculation.
- **Canvas/map ПКМ routing:** right-click now uses a shared world-pick router (`trunk_node` / `trunk_seg` / `block`); consumer/valve nodes expose `Властивості…` to open `_open_trunk_consumer_schedule_dialog`.
- **Trunk + geo baseline remains:** bend-chain logical edges, dz-aware budget loop, SRTM providers/fallbacks, contour smoothing, off-map LMB pan.

## When starting a task

1. Read all files in `memory-bank/` (per workspace rule).
2. For operational detail, open `PROJECT_CONTEXT.md` and the latest sections of `PROJECT_STATE.md`.

## Next steps (from project roadmap — not necessarily in progress)

- Optional plans under `docs/plans/` (magistral profile audit, Earthdata bearer path, emitter-block K_eq).
- Longer-term: split `dripcad_legacy.py`, official JSON DTO schemas, deeper BOM/trunk integration.

## Latest completed in this session

- Added `Hвст.` and inverse `H for Q_nom` to block properties, plus an in-dialog `▶ РОЗРАХУНОК` action.
- Fixed block-properties `OK` / `Apply` so they do not reset hydraulic results unless a real hydro-affecting parameter changed.
- Added consumer-node context-menu entry `Властивості…` to open the existing Q/H dialog.
- Synced docs (`PROJECT_CONTEXT.md`, `PROJECT_STATE.md`) and memory bank with the 2026-04-24 UI/UX fixes.

## Decisions / constraints to remember

- Project root and paths: `main_app/paths.py` (`PROJECT_ROOT`, `DESIGNS_DIR`, `PIPES_DB_PATH`, `SRTM_DIR`); do not rely on `chdir`.
- Saves go under `designs/<project>/` with sanitized names.
- Full-file handoffs: no placeholder snippets when the user asks for complete files.
