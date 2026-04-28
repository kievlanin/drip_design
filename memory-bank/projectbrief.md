# Project brief — DripCAD / Drip Designer Pro

## Purpose

Desktop CAD for agricultural drip irrigation: field blocks, submains, auto/manual laterals, hydraulic calculation (Hazen–Williams and lateral solvers), BOM, relief/contours, embedded map, export (KML/DXF/PDF). User-facing UI and reports are Ukrainian.

## Canonical project docs

- **[PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md)** — architecture, modules, hydraulic notes, file layout.
- **[PROJECT_STATE.md](../PROJECT_STATE.md)** — run instructions, JSON model, UI behavior, session log.

*Memory-bank / project docs last aligned: 2026-04-27 (see `PROJECT_STATE` session note 2026-04-27, `PROJECT_CONTEXT` §7 snapshot 2026-04-27).*

## Planned / reference specs (not necessarily implemented)

- [docs/plans/profil-magistral-lateral-q-audit.md](../docs/plans/profil-magistral-lateral-q-audit.md) — trunk profile along `path_local`, lateral emitter Q audit, Block tab.
- [docs/plans/earthdata-bearer-token-srtm.md](../docs/plans/earthdata-bearer-token-srtm.md) — optional Bearer + `EARTHDATA_TOKEN` tile path.
- [docs/plans/emitter-block-equivalent-k.md](../docs/plans/emitter-block-equivalent-k.md) — equivalent K for grouped emitters.

## Scope boundaries

- **In scope:** Tkinter app under `main_app/`, geo under `modules/geo_module/`, hydraulics under `modules/hydraulic_module/`, BOM under `modules/bom_module/`, designs under `designs/<name>/`.
- **Out of scope for casual work:** `legacy/` (not imported by the app).

## Success criteria (for changes)

- Match existing patterns (paths via `main_app/paths.py`, I/O in `file_io_impl`, hydraulics out of pure UI glue).
- Ukrainian strings for user-visible text.
- Prefer focused diffs; avoid unrelated refactors.
