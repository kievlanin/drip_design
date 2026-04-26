# Active context

**Last aligned with repo docs:** 2026-04-26 — see [PROJECT_STATE.md](../PROJECT_STATE.md) (session note 2026-04-26) and [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md) §7 snapshot 2026-04-26.

**Last memory-bank review:** 2026-04-26 — refreshed `activeContext.md` and `progress.md` with doc-sync date; reconfirmed alignment with [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md) / [PROJECT_STATE.md](../PROJECT_STATE.md) (reference snapshots 2026-04-23–2026-04-24 for functional detail).

## Current focus

- **Canvas layer manager:** `dripcad_legacy.py` now owns grouped canvas layers with `visible/selectable` state, a dedicated `Шари...` dialog, empty-canvas RMB entry point, and the enforced rule `selectable => visible`.
- **Granular block layers:** block rendering/picking is split into `block.boundaries`, `block.laterals`, `block.submain.base`, `block.valves`, and dynamic `block.submain.od:*`; trunk keeps graph/node layers plus dynamic `trunk.pipes.od:*`; labels stay under `cosmetic.labels`.
- **Valve label UX:** field-valve labels are multiline, left-aligned, and draggable with the two-click `SUB_LABEL` flow; positions persist in `consumer_schedule.field_valve_label_pos`.
- **Emitter block equivalent (`K_eq`) and audits:** `emitter_block_equivalent.py` + integration in `hydraulics_core` remain active, with `block_equivalent_emitter`, `lateral_flow_audit`, and per-submain-branch emitter Q extrema overlay.
- **Block dialog UX baseline:** `open_block_irrigation_scheme_dialog` shows `K_eq`, `P_ref`, editable `Hвст.`, live `Q_total @ H`, `% to Q_nom`, inverse `H for Q_nom`, and no longer wipes hydro on no-op `OK` / `Apply`.
- **Selected-block dialog UX:** multi-block selection now derives block scope from selected `block`, `submain`, and `lateral` hits; the block-properties dropdown starts with `Всі вибрані: ...`, includes the full selected scope, and canvas selection is cleared when the dialog closes.
- **Canvas/map ПКМ routing:** right-click still uses the shared world-pick router (`trunk_node` / `trunk_seg` / `block`); consumer/valve nodes expose `Властивості…` to open `_open_trunk_consumer_schedule_dialog`.
- **Refactor seams:** `main_app/contracts/orchestrator_models.py` owns orchestrator snapshots; `main_app/io/project_serialization.py`, `project_normalizers.py`, `project_blocks.py`, and `project_trunk.py` split technical I/O helpers out of `file_io_impl.py`; `modules/hydraulic_module/api.py` is the UI-facing hydraulic helper façade.

## When starting a task

1. Read all files in `memory-bank/` (per workspace rule).
2. For operational detail, open `PROJECT_CONTEXT.md` and the latest sections of `PROJECT_STATE.md`.

## Next steps (from project roadmap — not necessarily in progress)

- Optional plans under `docs/plans/` (magistral profile audit, Earthdata bearer path, emitter-block K_eq).
- Longer-term: split `dripcad_legacy.py`, broaden official JSON DTO schemas, deeper BOM/trunk integration.

## Latest completed in this session

- 2026-04-26: fixed selected-block properties UX in `dripcad_legacy.py`: selected block scope now includes parent blocks for selected submains/laterals, the dialog dropdown starts as `Всі вибрані: ...` with all selected blocks available, and canvas `selected` state is cleared on dialog close. Verified with lints and `py_compile`; PROJECT docs + memory-bank were updated.
- 2026-04-26: user-requested **documentation sync** — updated [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md) (new §7 snapshot 2026-04-26), [PROJECT_STATE.md](../PROJECT_STATE.md) (session note + footer), and this memory-bank set (`activeContext`, `progress`) so the next session can anchor on the same “last aligned” date.
- Prior work (2026-04-24 era, still the functional baseline in §7): typed orchestrator snapshots; I/O helper split; `modules/hydraulic_module/api.py`; `ensure_trunk_node_ids` import fix; focused pytest runs noted in `PROJECT_STATE` session 2026-04-24.

## Decisions / constraints to remember

- Project root and paths: `main_app/paths.py` (`PROJECT_ROOT`, `DESIGNS_DIR`, `PIPES_DB_PATH`, `SRTM_DIR`); do not rely on `chdir`.
- Saves go under `designs/<project>/` with sanitized names.
- JSON changes must keep old project payloads loadable; save may write the current canonical format. Treat `designs/` projects as regression fixtures, not disposable throwaways.
- Full-file handoffs: no placeholder snippets when the user asks for complete files.
