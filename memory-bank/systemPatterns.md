# System patterns

## Application shell

- Entry: `main_app/main.py` → `DripCADUI` in `main_app/ui/app_ui.py`; orchestration in `main_app/orchestrator.py`.
- Core canvas / `DripCAD`: `main_app/ui/dripcad_legacy.py` (large; central to drawing and much hydraulic UI).
- Control panel: `main_app/ui/control_panel_impl.py` (`ControlPanel`).
- Map shell / left column: `main_app/ui/map_viewer_tk_window.py`; drawing + trunk widgets: `main_app/ui/map_left_draw_widgets.py`.
- File I/O: `main_app/io/file_io_impl.py` (load/save/export entrypoint) plus extracted helpers: `project_serialization.py` (JSON-safe sanitize, atomic write, decode errors, trunk hydro cache key restore), `project_normalizers.py` (`consumer_schedule` compatibility normalization), `project_blocks.py` (`field_blocks_data` runtime/JSON conversion), `project_trunk.py` (trunk save payload).
- Contracts: `main_app/contracts/orchestrator_models.py` provides typed snapshots for orchestrator cached results while preserving dict-shaped UI contracts.

## Hydraulic architecture

- Network engine: `modules/hydraulic_module/hydraulics_core.py` (`HydraulicEngine`).
- UI-facing hydraulic helpers should be imported through `modules/hydraulic_module/api.py` rather than private helpers in `hydraulics_core.py`.
- Laterals: shooting / Newton on tip H (`lateral_drip_core`, `lateral_solver`); multi-node trickle NR (`trickle_line_nr_solver`, UI mode `trickle_nr`); field API in `lateral_field_compute.py`.
- Trunk graph: `trunk_map_graph.py` (tree validation, pair edges, `ensure_trunk_node_ids` for unique IDs).
- Trunk steady / schedule: `trunk_tree_compute.py`, `trunk_irrigation_schedule_hydro.py`; telescope optimization `pipe_weight_optimizer.py`.
- Trunk HW display after auto-sizing may use `use_required_source_head_per_slot` for hover/overlays: pipe feasibility is still checked against configured pump head, then the visible cache can be recomputed at each slot's `min_required_source_head_m`.
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
- Trunk pipe hover/pick labels should use `_format_pipe_signature` and the compact catalog form `ABBR ØOD/PN Lм`.
- Trunk route clicks in free space are geometry-only draft points, not implicit `bend` nodes; pickets are created only by the `trunk_picket` tool or graph-driven insertion.
- For two-node trunk edges, `path_local` is the canonical physical polyline. `sync_trunk_segment_paths_from_nodes` may update endpoints to node XY but must not collapse a valid polyline to the chord.
- Pressure graph actions are split by scope: edge graph uses the selected segment's `path_local`, branch graph uses the pump-to-branch chain, and RMB on the graph can insert a picket by converting chart distance `s` to segment-local distance.

## Geo / relief

- `modules/geo_module/topography_core.py` (`TopoEngine`): contours (IDW and optional kriging), SRTM grid fetch with multiple providers, smoothing before marching squares.
- `srtm_tiles.py`: tile download (Skadi, Earthdata / `earthaccess`), GUI bridge for credentials.

## Data model (JSON)

- Rich project: `field_blocks_data`, `trunk_map_nodes` / `trunk_map_segments`, `consumer_schedule`, `calc_results`, `params`, `allowed_pipes`, optional `scene_lines`, etc.
- Trunk segments normalized to one graph edge per segment; `path_local` polylines in meters.
- Compatibility invariant: loaders must accept legacy payloads through normalizers/fallbacks; saves write the current canonical structure. Existing `designs/` projects are regression fixtures.

## Testing

- `tests/` includes trunk graph, trunk irrigation schedule, pipe weight optimizer, and focused refactor tests for contracts / I/O helpers / hydraulic API. Use `python -m unittest` for unittest modules or `py -m pytest ...` for pytest-style focused tests on Windows.

## Algorithm reuse map (2026-04-28)

- **Hydraulic emitters / laterals:** reuse `lateral_drip_core.py` (`hazen_williams_hloss_m`, emitter law `q = k*H^x`, shooting + Newton) and `trickle_line_nr_solver.py` (multi-node NR with tridiagonal Thomas solver) before adding any new lateral solver branch.
- **Trunk graph / topology:** reuse `trunk_map_graph.py` (`expand_trunk_segments_to_pair_edges`, `build_oriented_edges`, `ensure_trunk_node_ids`) for all map/canvas trunk transforms and validation.
- **Trunk schedule hydraulics:** reuse `trunk_irrigation_schedule_hydro.py` for slot envelopes, target-head checks, edge/branch pressure logic, telescope refinement, and min pump-head estimation.
- **Pipe optimization:** reuse `pipe_weight_optimizer.py` (`optimize_fixed_topology_by_weight`, `optimize_single_line_allocation_by_weight`, telescoping allocation primitives) instead of local greedy sizing in UI.
- **Equivalent block emitter / audits:** reuse `emitter_block_equivalent.py` + `hydraulics_core` outputs (`block_equivalent_emitter`, `lateral_flow_audit`) for block-level diagnostics, not ad-hoc formulas in dialogs.
- **Geo/relief interpolation and contouring:** reuse `topography_core.py` (IDW/kriging grid, Z smoothing, contour generation, provider fallbacks) and `srtm_tiles.py` for tile acquisition/auth flow.
- **I/O compatibility and canonical save:** reuse `project_normalizers.py`, `project_blocks.py`, `project_trunk.py`, `project_serialization.py`; avoid duplicating payload migration logic in UI handlers.
- **UI pick/snap/layer behavior:** reuse shared hit/pick routing and layer visibility/selectability rules in `dripcad_legacy.py` (`_collect_world_pick_hits`, `_open_context_menu_for_world_pick`, layer helpers) instead of adding per-tool local pick code.
- **Allowed pipes canonicalization (shapely-free):** reuse `allowed_pipes_common.py` (`normalize_allowed_pipes_map_common`, `pn_sort_tuple_common`, `allowed_pipe_candidates_sorted_common`) from both `hydraulics_core` and trunk schedule paths; avoid local copies of map normalization/sorting/candidate flattening.

### Duplication risks to avoid

- Allowed-pipes normalization / candidate flattening moved to shared shapely-free `allowed_pipes_common.py`; keep future behavior changes centralized there to prevent drift between hydraulic core and trunk schedule flows.
- Pressure/profile polyline traversal logic should continue to flow through trunk path helpers (`_trunk_segment_world_path`, profile piece builders) to prevent divergent edge-vs-branch math.
