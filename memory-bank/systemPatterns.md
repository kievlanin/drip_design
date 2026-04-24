# System patterns

## Application shell

- Entry: `main_app/main.py` → `DripCADUI` in `main_app/ui/app_ui.py`; orchestration in `main_app/orchestrator.py`.
- Core canvas / `DripCAD`: `main_app/ui/dripcad_legacy.py` (large; central to drawing and much hydraulic UI).
- Control panel: `main_app/ui/control_panel_impl.py` (`ControlPanel`).
- Map shell / left column: `main_app/ui/map_viewer_tk_window.py`; drawing + trunk widgets: `main_app/ui/map_left_draw_widgets.py`.
- File I/O: `main_app/io/file_io_impl.py` (JSON project load/save, exports, normalization of trunk segments and schedules).

## Hydraulic architecture

- Network engine: `modules/hydraulic_module/hydraulics_core.py` (`HydraulicEngine`).
- Laterals: shooting / Newton on tip H (`lateral_drip_core`, `lateral_solver`); multi-node trickle NR (`trickle_line_nr_solver`, UI mode `trickle_nr`); field API in `lateral_field_compute.py`.
- Trunk graph: `trunk_map_graph.py` (tree validation, pair edges, `ensure_trunk_node_ids` for unique IDs).
- Trunk steady / schedule: `trunk_tree_compute.py`, `trunk_irrigation_schedule_hydro.py`; telescope optimization `pipe_weight_optimizer.py`.
- Emitter block equivalent and audit: `emitter_block_equivalent.py` + `hydraulics_core.py` publish `block_equivalent_emitter` and `lateral_flow_audit` to `calc_results`; `x` is consumed from selected emitter model, while only `K_eq` is derived.
- Lazy hydraulic package exports: `modules/hydraulic_module/__init__.py`.

## UI interaction patterns

- Block properties dialog (`open_block_irrigation_scheme_dialog`) mixes editable block params with read-only computed fields from `calc_results` (`K_eq`, `P_ref`, `Hвст.`, pressure-driven `Q_total`, `% to nominal`, inverse `H for Q_nom`).
- The block-properties dialog must not invalidate existing hydraulic results on no-op `OK` / `Apply`; sync back into traced global `var_*` only when parameters actually changed and recalculation is needed.
- Canvas visibility/selection is modeled as a small hierarchy: group nodes plus leaf layers, with dynamic diameter-based leaves for trunk and submain pipes; the invariant is `selectable => visible`, and disabling visibility also disables selectability.
- Rendering and picking must go through layer helpers (`is_canvas_layer_visible`, `is_canvas_layer_selectable`, dynamic layer-id resolvers) so new canvas objects inherit layer behavior consistently.
- Valve label movement follows the same two-click `SUB_LABEL` interaction used for section/telescope labels, but persists separately in `consumer_schedule.field_valve_label_pos`.
- Context menus are mode-aware and world-pick driven: one hit opens target menu directly; multiple overlapping hits show a choose-target menu before opening specific actions.
- Block context menu supports explicit target index (used by overlap disambiguation), while trunk context menu supports explicit `(cat, payload, label)` target forwarding; consumption/valve nodes expose a `Властивості…` action that opens the consumer schedule/properties dialog.
- Snap radii for trunk node/valve interactions are fixed in world meters; near-node snap radius is rendered as a dashed hint ring on both canvas and embedded map.

## Geo / relief

- `modules/geo_module/topography_core.py` (`TopoEngine`): contours (IDW and optional kriging), SRTM grid fetch with multiple providers, smoothing before marching squares.
- `srtm_tiles.py`: tile download (Skadi, Earthdata / `earthaccess`), GUI bridge for credentials.

## Data model (JSON)

- Rich project: `field_blocks_data`, `trunk_map_nodes` / `trunk_map_segments`, `consumer_schedule`, `calc_results`, `params`, `allowed_pipes`, optional `scene_lines`, etc.
- Trunk segments normalized to one graph edge per segment; `path_local` polylines in meters.

## Testing

- `tests/` includes trunk graph, trunk irrigation schedule, pipe weight optimizer, etc. Use `python -m unittest` targeting relevant modules on Windows (`py` launcher if needed).
