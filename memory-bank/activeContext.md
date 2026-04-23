# Active context

**Last aligned with repo docs:** 2026-04-23 — see [PROJECT_STATE.md](../PROJECT_STATE.md) session notes.

**Last memory-bank review:** full pass over all six files on user **Update**; compared to [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md) / `PROJECT_STATE.md` including 2026-04-23 session notes.

## Current focus

- **Emitter block equivalent (`K_eq`)**: `modules/hydraulic_module/emitter_block_equivalent.py` + integration in `hydraulics_core` (`calc_results["block_equivalent_emitter"]`), where `x` is taken from selected dripper model only (no `x` fitting fallback).
- **Block flow audit/UI:** `calc_results["lateral_flow_audit"]` with min/max/Qnom statuses; Block dialog now shows `K_eq`, `P_ref`, editable pressure for preview, `Q_total @ H`, and `% to Q_nom` live.
- **Canvas/map ПКМ routing:** right-click now uses a shared world-pick router (`trunk_node` / `trunk_seg` / `block`); single hit opens the target menu directly, multiple overlaps show a disambiguation list first, then open the selected object's menu.
- **Snap behavior (trunk graph):** node snap is now hard/fixed in world meters (no zoom expansion), valve snap is also fixed-radius, and snap-radius hint circle is shown when cursor approaches a trunk node (canvas + embedded map).
- **Trunk + geo baseline remains:** bend-chain logical edges, dz-aware budget loop, SRTM providers/fallbacks, contour smoothing, off-map LMB pan.

## When starting a task

1. Read all files in `memory-bank/` (per workspace rule).
2. For operational detail, open `PROJECT_CONTEXT.md` and the latest sections of `PROJECT_STATE.md`.

## Next steps (from project roadmap — not necessarily in progress)

- Optional plans under `docs/plans/` (magistral profile audit, Earthdata bearer path, emitter-block K_eq).
- Longer-term: split `dripcad_legacy.py`, official JSON DTO schemas, deeper BOM/trunk integration.

## Latest completed in this session

- Tuned trunk-node snap zone down to a smaller fixed radius and kept visual hint enabled.
- Removed noisy small technical labels from trunk rendering; retained final optimization labels.
- Synced docs (`PROJECT_CONTEXT.md`, `PROJECT_STATE.md`) and pushed commit `2ae6411` to `origin/master`.

## Decisions / constraints to remember

- Project root and paths: `main_app/paths.py` (`PROJECT_ROOT`, `DESIGNS_DIR`, `PIPES_DB_PATH`, `SRTM_DIR`); do not rely on `chdir`.
- Saves go under `designs/<project>/` with sanitized names.
- Full-file handoffs: no placeholder snippets when the user asks for complete files.
