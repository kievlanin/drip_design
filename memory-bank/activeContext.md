# Active context

**Last aligned with repo docs:** 2026-04-27 — see [PROJECT_STATE.md](../PROJECT_STATE.md) (session note 2026-04-27) and [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md) §7 snapshot 2026-04-27.

**Last memory-bank review:** 2026-04-27 — refreshed project docs and memory-bank after trunk polyline route / pressure-graph picket insertion / right-panel cleanup.

## Current focus

- **Canvas layer manager:** `dripcad_legacy.py` now owns grouped canvas layers with `visible/selectable` state, a dedicated `Шари...` dialog, empty-canvas RMB entry point, and the enforced rule `selectable => visible`.
- **Granular block layers:** block rendering/picking is split into `block.boundaries`, `block.laterals`, `block.submain.base`, `block.valves`, and dynamic `block.submain.od:*`; trunk keeps graph/node layers plus dynamic `trunk.pipes.od:*`; labels stay under `cosmetic.labels`.
- **Valve label UX:** field-valve labels are multiline, left-aligned, and draggable with the two-click `SUB_LABEL` flow; positions persist in `consumer_schedule.field_valve_label_pos`.
- **Emitter block equivalent (`K_eq`) and audits:** `emitter_block_equivalent.py` + integration in `hydraulics_core` remain active, with `block_equivalent_emitter`, `lateral_flow_audit`, and per-submain-branch emitter Q extrema overlay.
- **Block dialog UX baseline:** `open_block_irrigation_scheme_dialog` shows `K_eq`, `P_ref`, editable `Hвст.`, live `Q_total @ H`, `% to Q_nom`, inverse `H for Q_nom`, and no longer wipes hydro on no-op `OK` / `Apply`.
- **Selected-block dialog UX:** multi-block selection now derives block scope from selected `block`, `submain`, and `lateral` hits; the block-properties dropdown starts with `Всі вибрані: ...`, includes the full selected scope, and canvas selection is cleared when the dialog closes.
- **Canvas/map ПКМ routing:** right-click still uses the shared world-pick router (`trunk_node` / `trunk_seg` / `block`); consumer/valve nodes expose `Властивості…` to open `_open_trunk_consumer_schedule_dialog`.
- **Trunk polyline route UX:** `trunk_route` no longer creates implicit pickets on free-space LMB; free-space clicks become draft `path_local` points, while `bend` nodes are explicit via the picket tool or graph insertion.
- **Trunk pressure graph UX:** edge and branch pressure graphs are separate context-menu actions; RMB on the graph inserts a picket at the cursor's distance `s` along the edge/branch.
- **Right panel cleanup:** the old `H, м` / `Пікет @ H` / `Vmax≥` block was removed from the `Магістраль (HW)` control panel; picket placement now lives on the pressure graph.
- **Refactor seams:** `main_app/contracts/orchestrator_models.py` owns orchestrator snapshots; `main_app/io/project_serialization.py`, `project_normalizers.py`, `project_blocks.py`, and `project_trunk.py` split technical I/O helpers out of `file_io_impl.py`; `modules/hydraulic_module/api.py` is the UI-facing hydraulic helper façade.

## When starting a task

1. Read all files in `memory-bank/` (per workspace rule).
2. For operational detail, open `PROJECT_CONTEXT.md` and the latest sections of `PROJECT_STATE.md`.

## Next steps (from project roadmap — not necessarily in progress)

- Optional plans under `docs/plans/` (magistral profile audit, Earthdata bearer path, emitter-block K_eq).
- Longer-term: split `dripcad_legacy.py`, broaden official JSON DTO schemas, deeper BOM/trunk integration.

## Latest completed in this session

- 2026-04-28: extracted shared shapely-free allowed-pipes helper `modules/hydraulic_module/allowed_pipes_common.py` with `normalize_allowed_pipes_map_common`, `pn_sort_tuple_common`, and `allowed_pipe_candidates_sorted_common`.
- 2026-04-28: rewired both `hydraulics_core` and `trunk_irrigation_schedule_hydro` to call the shared helper (kept existing local function names as wrappers where needed), removing duplicated normalization/sorting/flattening logic.
- 2026-04-28: verification after extraction: `py -m pytest tests/test_hydraulic_api.py tests/test_trunk_irrigation_schedule_hydro.py` -> 17 passed; `ReadLints` on modified files -> no issues.
- 2026-04-28: completed a focused code review for duplication/algorithm reuse preparation. Noted a concrete optimizer risk in `pipe_weight_optimizer._snap_allocations_to_length_step` where recalculated rounded allocations use `DEFAULT_HAZEN_WILLIAMS_C` instead of per-option `c_hw`, which can bias `head_loss_m` validation when catalogs mix materials/C values.
- 2026-04-28: prepared and stored an algorithm reuse inventory (hydraulics / trunk graph / geo-relief / I/O normalization / UI picking-snap layers) in memory-bank for future tasks to avoid re-implementing existing logic.
- 2026-04-28: identified maintainability duplication hotspot: allowed-pipes normalization and candidate flattening are implemented in both `hydraulics_core` and `trunk_irrigation_schedule_hydro` (the latter is intentionally shapely-light). Recommendation recorded: extract a small shared helper module that stays shapely-free and reuse it from both call sites.
- 2026-04-27: updated trunk route editing so free-space LMB creates polyline draft points without auto-adding `bend`; `path_local` survives normalization for two-node edges; edge vs branch pressure graphs are separate; RMB on pressure graph inserts a picket by chart distance `s`; removed old `H, м` / `Пікет @ H` / `Vmax≥` UI block from `Магістраль (HW)`. Verified with lints and `py_compile`.
- 2026-04-27: user-requested **documentation sync** — updated [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md), [PROJECT_STATE.md](../PROJECT_STATE.md), and memory-bank to reflect trunk polyline/picket graph UX.
- 2026-04-26: standardized trunk pipe hover/pick labels to `ABBR ØOD/PN Lм` via `_format_pipe_signature` in `dripcad_legacy.py` (e.g. `ПВХ Ø150/6 629.2м`), replacing the old two-line `L ≈ ...` / `Ø ... мм` label. Verified with lints and `py_compile`.
- 2026-04-26: fixed trunk schedule auto-sizing display cache: after optimized pipe selection, UI hover/overlays can now use per-slot minimum required source head (`use_required_source_head_per_slot`) so a lightly loaded slot such as `atest` C2/T6 shows ~17 m at the consumer instead of the global worst-case pump head (~50.6 m). Verified with focused pytest and an `atest` recompute.
- 2026-04-26: user-requested **state/context save** — updated [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md), [PROJECT_STATE.md](../PROJECT_STATE.md), and memory-bank after the trunk HW display/label fixes.
- 2026-04-26: fixed selected-block properties UX in `dripcad_legacy.py`: selected block scope now includes parent blocks for selected submains/laterals, the dialog dropdown starts as `Всі вибрані: ...` with all selected blocks available, and canvas `selected` state is cleared on dialog close. Verified with lints and `py_compile`; PROJECT docs + memory-bank were updated.
- 2026-04-26: user-requested **documentation sync** — updated [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md) (new §7 snapshot 2026-04-26), [PROJECT_STATE.md](../PROJECT_STATE.md) (session note + footer), and this memory-bank set (`activeContext`, `progress`) so the next session can anchor on the same “last aligned” date.
- Prior work (2026-04-24 era, still the functional baseline in §7): typed orchestrator snapshots; I/O helper split; `modules/hydraulic_module/api.py`; `ensure_trunk_node_ids` import fix; focused pytest runs noted in `PROJECT_STATE` session 2026-04-24.

## Decisions / constraints to remember

- Project root and paths: `main_app/paths.py` (`PROJECT_ROOT`, `DESIGNS_DIR`, `PIPES_DB_PATH`, `SRTM_DIR`); do not rely on `chdir`.
- Saves go under `designs/<project>/` with sanitized names.
- JSON changes must keep old project payloads loadable; save may write the current canonical format. Treat `designs/` projects as regression fixtures, not disposable throwaways.
- Full-file handoffs: no placeholder snippets when the user asks for complete files.
