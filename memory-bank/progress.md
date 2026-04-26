# Progress

## Working (high level)

- Multi-block field CAD with submains, auto/manual laterals, hydraulic solve, results tab, BOM module.
- Map tab with zone, tiles, drawing/trunk panels, geo ref, scale overlay, tooltips.
- Trunk as validated tree: drawing, selection, info/highlight, irrigation slots, telescope optimization (incl. bend-chain logical edge), pressure/profile dialogs, velocity indication, `ensure_trunk_node_ids`.
- Relief: SRTM tiles (Skadi / custom URL / `earthaccess`), elevation provider dropdown + fallbacks, contours IDW/kriging, Z grid smoothing before contours, **Zoom visible**, relief layer toggles persisted in project JSON.
- Off-map canvas: LMB-drag pan in VIEW/PAN without stealing select/trunk/zoom-box gestures.
- Block tab: emitter flow / masks / isoline UI decoupled from main canvas redraw for performance.
- Block hydraulics metadata: `lateral_flow_audit` and `block_equivalent_emitter` stored in `calc_results` with block-local strip/remap merge paths.
- Block properties dialog: shows `K_eq`, `P_ref`, editable `Hвст.`, live `Q_total @ H`, `% to Q_nom`, inverse `H for Q_nom`, and now preserves existing hydro on no-op `OK` / `Apply`.
- Selected-block properties flow: context menu/dropdown handles all selected block scope, including parent blocks from selected submains/laterals; the dropdown starts with `Всі вибрані: ...` and clears canvas `selected` highlighting when the properties dialog closes.
- Canvas layer system: grouped layer dialog + empty-canvas RMB access, persisted `canvas_layers`, dynamic `trunk.pipes.od:*` and `block.submain.od:*`, with `visible/selectable` enforcement.
- Block canvas UX: valve labels are multiline and draggable in `SUB_LABEL`, per-branch emitter Q extrema can be rendered for each visible submain branch, and render/pick paths respect current layer visibility/selectability.
- Canvas right-click over block area (not only boundary) opens block context menu with property/edit/clear/delete actions.
- Canvas/map right-click now supports overlap disambiguation: when multiple objects are under cursor, user chooses exact target (`node/edge/block`) before actions menu opens.
- Trunk consumer/valve context menu exposes `Властивості…` for the existing per-node Q/H schedule dialog.
- Trunk snap uses fixed world-meter radii (no zoom-based growth), and near-node snap radius is visually highlighted on canvas and embedded map.
- Refactor seams: typed orchestrator snapshots, split I/O helpers (`project_serialization`, `project_normalizers`, `project_blocks`, `project_trunk`), and hydraulic UI façade (`modules/hydraulic_module/api.py`) are in place.
- JSON compatibility rule is active: old project payloads should keep loading through normalizers/fallbacks, while saves write the current canonical format.
- Lateral graphs: adaptive layout, debounced resize, hover annotations, aligned L1/L2 tap pressure.
- Exports: KML/DXF/PDF; silent dialogs on Windows.

## Known documentation anchors

- Active issues and wishlist items are not duplicated here; see **“Що логічно доробити далі”** and session notes in [PROJECT_STATE.md](../PROJECT_STATE.md).
- Open design plans live under `docs/plans/` and root `*_plan.md` files where present.

## Memory bank maintenance

- On **Update** / **update memory bank**: review **every** file in `memory-bank/`, then adjust `activeContext.md` and `progress.md` at minimum; keep others in sync if project facts changed.
- After significant code or doc changes in the repo, refresh this folder or extend `PROJECT_STATE.md` first, then mirror here.
- Keep this layer **short**; defer depth to `PROJECT_CONTEXT.md` / `PROJECT_STATE.md` to avoid drift.
- **2026-04-26:** docs and memory-bank updated after selected-block properties UX fix (`dripcad_legacy.py`): full selected block scope in dropdown (`Всі вибрані: ...`) and cleared canvas selection on dialog close.
- **2026-04-26:** `activeContext` / `progress` dates bumped; `systemPatterns` / `techContext` / `projectbrief` / `productContext` reviewed — no structural change required beyond what is already described for contracts, I/O helpers, and `api.py`.
