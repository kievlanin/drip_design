import os
import json
import math
import re
from tkinter import filedialog, messagebox
from shapely.geometry import LineString, Polygon, Point
from shapely.ops import unary_union

from main_app.paths import DESIGNS_DIR, PIPES_DB_PATH
from modules.geo_module import srtm_tiles
from modules.hydraulic_module.trunk_map_graph import ensure_trunk_node_ids


def _field_block_from_dict(item):
    """Restore one field block from JSON (field_blocks_data)."""
    ring = [tuple(p[:2]) if len(p) >= 2 else tuple(p) for p in item.get("ring", [])]
    ea = item.get("edge_angle")
    if ea is not None:
        ea = float(ea)
    sub = [list(s) for s in item.get("submain", [])]
    auto = [LineString(c) for c in item.get("auto", []) if len(c) > 1]
    manual = [LineString(c) for c in item.get("manual", []) if len(c) > 1]
    bp = item.get("params")
    if not isinstance(bp, dict):
        bp = {}
    ssp = item.get("submain_segment_plan")
    if not isinstance(ssp, dict):
        ssp = {}
    return {
        "ring": ring,
        "edge_angle": ea,
        "submain_lines": sub,
        "auto_laterals": auto,
        "manual_laterals": manual,
        "params": dict(bp),
        "submain_segment_plan": ssp,
    }


def field_blocks_to_save_payload(app):
    """Serialize field_blocks for JSON."""
    blocks = getattr(app, "field_blocks", None)
    if not blocks:
        return [], []
    payload = []
    rings_only = []
    for b in blocks:
        rings_only.append(b["ring"])
        bp = b.get("params")
        if not isinstance(bp, dict):
            bp = {}
        ssp = b.get("submain_segment_plan")
        if not isinstance(ssp, dict):
            ssp = {}
        payload.append(
            {
                "ring": b["ring"],
                "edge_angle": b.get("edge_angle"),
                "submain": b["submain_lines"],
                "auto": [list(lat.coords) for lat in b["auto_laterals"]],
                "manual": [list(lat.coords) for lat in b["manual_laterals"]],
                "params": dict(bp),
                "submain_segment_plan": ssp,
            }
        )
    return payload, rings_only


def _sanitize_project_dir_name(name: str) -> str:
    """Ім'я теки/файлу проєкту без символів, заборонених у Windows."""
    s = (name or "").strip() or "Untitled_Project"
    for ch in '<>:"/\\|?*':
        s = s.replace(ch, "_")
    s = s.strip(" .") or "Untitled_Project"
    return s


def ensure_project_dir(app):
    """Тека проєкту завжди: designs/<ім'я_проєкту>/"""
    base_dir = DESIGNS_DIR
    base_dir.mkdir(parents=True, exist_ok=True)
    proj_name = app.var_proj_name.get().strip()
    if not proj_name:
        proj_name = "Untitled_Project"
        app.var_proj_name.set(proj_name)
    safe = _sanitize_project_dir_name(proj_name)
    if safe != proj_name:
        app.var_proj_name.set(safe)
    proj_dir = base_dir / safe
    proj_dir.mkdir(parents=True, exist_ok=True)
    return str(proj_dir)


def _collect_project_data(app, force_georeferenced=False):
    trunk_nodes = getattr(app, "trunk_map_nodes", None) or []
    if trunk_nodes:
        ensure_trunk_node_ids(trunk_nodes)

    fb_data, fb_rings = field_blocks_to_save_payload(app)
    fbs = getattr(app, "field_blocks", []) or []
    legacy_manual = [list(lat.coords) for b in fbs for lat in b["manual_laterals"]]
    legacy_sub = app._all_submain_lines() if hasattr(app, "_all_submain_lines") else []
    legacy_ea = 0
    if len(fbs) == 1 and fbs[0].get("edge_angle") is not None:
        legacy_ea = fbs[0]["edge_angle"]

    return {
        "proj_name": app.var_proj_name.get(),
        "geo_ref": app.geo_ref,
        "is_georeferenced": bool(force_georeferenced or bool(getattr(app, "geo_ref", None))),
        "field_blocks_data": fb_data,
        "field_blocks": fb_rings,
        "points": app.points,
        "submain": legacy_sub,
        "edge_angle": legacy_ea if legacy_ea is not None else 0,
        "manual": legacy_manual,
        "is_closed": app.is_closed,
        "allowed_pipes": app.allowed_pipes,
        "topo_points": app.topo.elevation_points,
        "calc_results": app.calc_results,
        "trunk_tree": getattr(app, "trunk_tree_data", {}),
        "trunk_tree_results": getattr(app, "trunk_tree_results", {}),
        "scene_lines": list(getattr(app, "scene_lines", []) or []),
        "trunk_map_nodes": list(getattr(app, "trunk_map_nodes", []) or []),
        "trunk_map_segments": list(getattr(app, "trunk_map_segments", []) or []),
        "project_zone_bounds_local": (
            list(app.project_zone_bounds_local)
            if getattr(app, "project_zone_bounds_local", None) is not None
            else None
        ),
        "srtm_zone_ring_local": list(getattr(app.topo, "srtm_boundary_pts_local", []) or []),
        "params": {
            "lat": app.var_lat_step.get(),
            "emit": app.var_emit_step.get(),
            "flow": app.var_emit_flow.get(),
            "emit_model": getattr(app, "var_emit_model", None) and app.var_emit_model.get() or "",
            "emit_nominal_flow": getattr(app, "var_emit_nominal_flow", None)
            and app.var_emit_nominal_flow.get()
            or "",
            "emit_k_coeff": getattr(app, "var_emit_k_coeff", None) and app.var_emit_k_coeff.get() or "",
            "emit_x_exp": getattr(app, "var_emit_x_exp", None) and app.var_emit_x_exp.get() or "",
            "emit_kd_coeff": getattr(app, "var_emit_kd_coeff", None) and app.var_emit_kd_coeff.get() or "1.0",
            "max_len": app.var_max_lat_len.get(),
            "blocks": app.var_lat_block_count.get(),
            "fixed_sec": app.var_fixed_sec.get(),
            "num_sec": app.var_num_sec.get(),
            "v_min": app.var_v_min.get(),
            "v_max": app.var_v_max.get(),
            "valve_h_max_m": getattr(app, "var_valve_h_max_m", None) and app.var_valve_h_max_m.get() or "0",
            "valve_h_max_optimize": getattr(app, "var_valve_h_max_optimize", None)
            and app.var_valve_h_max_optimize.get(),
            "show_emitter_flow_on_map": getattr(app, "var_show_emitter_flow", None)
            and app.var_show_emitter_flow.get(),
            "mat": app.pipe_material.get(),
            "pn": app.pipe_pn.get(),
            "lateral_solver_mode": getattr(app, "var_lateral_solver_mode", None)
            and app.var_lateral_solver_mode.get()
            or "bisection",
            "emitter_compensated": (
                app._emitter_compensated_effective()
                if hasattr(app, "_emitter_compensated_effective")
                else False
            ),
            "emitter_h_min_m": getattr(app, "var_emit_h_min", None) and app.var_emit_h_min.get() or "1.0",
            "emitter_h_ref_m": getattr(app, "var_emit_h_ref", None) and app.var_emit_h_ref.get() or "10.0",
            "lateral_inner_d_mm": getattr(app, "var_lat_inner_d_mm", None)
            and app.var_lat_inner_d_mm.get()
            or "13.6",
            "lateral_model": getattr(app, "var_lateral_model", None)
            and app.var_lateral_model.get()
            or "",
            "emitter_h_press_min_m": getattr(app, "var_emit_h_press_min", None)
            and app.var_emit_h_press_min.get()
            or "0",
            "emitter_h_press_max_m": getattr(app, "var_emit_h_press_max", None)
            and app.var_emit_h_press_max.get()
            or "0",
            "submain_topo_in_headloss": bool(getattr(app, "_submain_topo_in_headloss", True)),
            **(
                {
                    "lat_disp_step": app.var_lat_disp_step.get(),
                    "lat_disp_n_start": app.var_lat_disp_n_start.get(),
                    "lat_disp_n_end": app.var_lat_disp_n_end.get(),
                    "lat_disp_use_step": app.var_lat_disp_use_step.get(),
                    "lat_disp_use_start": app.var_lat_disp_use_start.get(),
                    "lat_disp_use_end": app.var_lat_disp_use_end.get(),
                }
                if hasattr(app, "var_lat_disp_step")
                else {}
            ),
        },
    }


def _write_project_to_disk(app, filepath, data):
    filepath = os.path.abspath(filepath)
    proj_dir = os.path.dirname(filepath)
    os.makedirs(proj_dir, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    project_db_path = os.path.join(proj_dir, "pipes_db.json")
    with open(project_db_path, "w", encoding="utf-8") as f:
        json.dump(app.pipe_db, f, indent=4)


def save_project(app, force_georeferenced=False):
    if force_georeferenced and not getattr(app, "geo_ref", None):
        messagebox.showwarning(
            "Геоприв'язка",
            "Неможливо зберегти як геоприв'язаний: у проєкті немає geo_ref.",
        )
        return
    proj_dir = ensure_project_dir(app)
    filename = f"{app.var_proj_name.get().strip()}.json"
    filepath = os.path.join(proj_dir, filename)
    data = _collect_project_data(app, force_georeferenced)
    try:
        _write_project_to_disk(app, filepath, data)
        messagebox.showinfo("Збережено", f"Проект успішно збережено в:\n{filepath}")
    except Exception as e:
        messagebox.showerror("Помилка", f"Не вдалося зберегти проект:\n{e}")


def save_project_as(app, force_georeferenced=False):
    if force_georeferenced and not getattr(app, "geo_ref", None):
        messagebox.showwarning(
            "Геоприв'язка",
            "Неможливо зберегти як геоприв'язаний: у проєкті немає geo_ref.",
        )
        return
    DESIGNS_DIR.mkdir(parents=True, exist_ok=True)
    pname = (app.var_proj_name.get() or "").strip() or "Untitled_Project"
    initialfile = f"{pname}.json"
    picked = filedialog.asksaveasfilename(
        title="Зберегти проект як (завжди: designs / ім'я / ім'я.json)",
        initialdir=str(DESIGNS_DIR),
        initialfile=initialfile,
        defaultextension=".json",
        filetypes=[("JSON проєкт", "*.json"), ("Усі файли", "*.*")],
    )
    if not picked:
        return
    if not picked.lower().endswith(".json"):
        picked += ".json"
    stem_raw = os.path.splitext(os.path.basename(picked))[0]
    stem = _sanitize_project_dir_name(stem_raw)
    app.var_proj_name.set(stem)
    designs_root = str(DESIGNS_DIR)
    os.makedirs(designs_root, exist_ok=True)
    proj_dir = os.path.join(designs_root, stem)
    os.makedirs(proj_dir, exist_ok=True)
    filepath = os.path.join(proj_dir, f"{stem}.json")
    data = _collect_project_data(app, force_georeferenced)
    data["proj_name"] = app.var_proj_name.get()
    try:
        _write_project_to_disk(app, filepath, data)
        messagebox.showinfo(
            "Збережено",
            f"Проект збережено:\n{filepath}\n\nТека проєкту:\n{proj_dir}",
        )
    except Exception as e:
        messagebox.showerror("Помилка", f"Не вдалося зберегти проект:\n{e}")


def save_project_georeferenced(app):
    save_project(app, force_georeferenced=True)

def load_project(app):
    base_dir = DESIGNS_DIR
    base_dir.mkdir(parents=True, exist_ok=True)
        
    filepath = filedialog.askopenfilename(initialdir=str(base_dir), title="Відкрити проект", filetypes=[("JSON Files", "*.json")])
    if not filepath: return
        
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        app.clear_all()
        
        # ЗАВАНТАЖУЄМО БАЗУ ТРУБ ПРОЕКТУ (якщо вона є)
        proj_dir = os.path.dirname(filepath)
        project_db_path = os.path.join(proj_dir, "pipes_db.json")
        
        if os.path.exists(project_db_path):
            try:
                with open(project_db_path, "r", encoding="utf-8") as f:
                    app.pipe_db = json.load(f)
            except: pass
        else:
            # Якщо немає, завантажуємо глобальну
            try:
                with open(PIPES_DB_PATH, "r", encoding="utf-8") as f:
                    app.pipe_db = json.load(f)
            except: pass

        app.var_proj_name.set(data.get("proj_name", "Untitled_Project"))
        app.geo_ref = data.get("geo_ref")
        app.is_georeferenced = bool(data.get("is_georeferenced", bool(app.geo_ref)))
        v2 = data.get("field_blocks_data")
        if v2:
            app.field_blocks = [_field_block_from_dict(x) for x in v2]
        else:
            rings = [list(r) for r in data.get("field_blocks", []) if len(r) >= 3]
            sm = data.get("submain") or []
            manual_cs = data.get("manual") or []
            ea = data.get("edge_angle")
            app.field_blocks = []
            for i, ring in enumerate(rings):
                app.field_blocks.append(
                    {
                        "ring": ring,
                        "edge_angle": float(ea) if i == 0 and ea is not None else None,
                        "submain_lines": [list(s) for s in sm] if i == 0 else [],
                        "auto_laterals": [],
                        "manual_laterals": [
                            LineString(c) for c in manual_cs if len(c) > 1
                        ]
                        if i == 0
                        else [],
                        "params": {},
                        "submain_segment_plan": {},
                    }
                )
        app.points = data.get("points", [])
        app.is_closed = data.get("is_closed", False)
        if not app.field_blocks and app.is_closed and len(app.points) >= 3:
            app.field_blocks = [
                {
                    "ring": list(app.points),
                    "edge_angle": None,
                    "submain_lines": [],
                    "auto_laterals": [],
                    "manual_laterals": [],
                    "params": {},
                    "submain_segment_plan": {},
                }
            ]
            app.points = []
            app.is_closed = False
        app.topo.elevation_points = data.get("topo_points", [])
        pzb = data.get("project_zone_bounds_local")
        if isinstance(pzb, (list, tuple)) and len(pzb) == 4:
            try:
                app.project_zone_bounds_local = tuple(float(x) for x in pzb)
            except (TypeError, ValueError):
                app.project_zone_bounds_local = None
        else:
            app.project_zone_bounds_local = None
        szr = data.get("srtm_zone_ring_local")
        if isinstance(szr, list) and len(szr) >= 3:
            ring = []
            for p in szr:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    ring.append((float(p[0]), float(p[1])))
            if len(ring) >= 3:
                app.topo.srtm_boundary_pts_local = ring
        elif getattr(app, "project_zone_bounds_local", None) is not None:
            minx, miny, maxx, maxy = app.project_zone_bounds_local
            app.topo.srtm_boundary_pts_local = [
                (minx, miny),
                (maxx, miny),
                (maxx, maxy),
                (minx, maxy),
            ]
        if hasattr(app, "_normalize_trunk_tree_payload"):
            app.trunk_tree_data = app._normalize_trunk_tree_payload(
                data.get("trunk_tree", getattr(app, "trunk_tree_data", {}))
            )
        else:
            app.trunk_tree_data = data.get("trunk_tree", {})
        app.trunk_tree_results = data.get("trunk_tree_results", {})
        _sl = data.get("scene_lines") or []
        app.scene_lines = []
        for seg in _sl:
            if not isinstance(seg, list) or len(seg) < 2:
                continue
            line = []
            for p in seg:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    line.append((float(p[0]), float(p[1])))
            if len(line) >= 2:
                app.scene_lines.append(line)
        app.trunk_map_nodes = []
        for row in data.get("trunk_map_nodes") or []:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("kind", "")).strip().lower()
            if kind not in ("source", "valve", "bend", "junction", "consumption"):
                continue
            try:
                lat = float(row.get("lat"))
                lon = float(row.get("lon"))
            except (TypeError, ValueError):
                continue
            x = y = None
            try:
                x = float(row.get("x"))
                y = float(row.get("y"))
            except (TypeError, ValueError):
                pass
            if x is None or y is None:
                gr = getattr(app, "geo_ref", None)
                if gr and len(gr) >= 2:
                    try:
                        ref_lon, ref_lat = float(gr[0]), float(gr[1])
                        x, y = srtm_tiles.lat_lon_to_local_xy(lat, lon, ref_lon, ref_lat)
                    except Exception:
                        x, y = 0.0, 0.0
                else:
                    x, y = 0.0, 0.0
            rec: dict = {"kind": kind, "lat": lat, "lon": lon, "x": float(x), "y": float(y)}
            tid = str(row.get("id", "")).strip()
            if tid:
                rec["id"] = tid
            app.trunk_map_nodes.append(rec)
        ensure_trunk_node_ids(app.trunk_map_nodes)
        app.trunk_map_segments = []
        app._trunk_route_last_node_idx = None
        for seg in data.get("trunk_map_segments") or []:
            if not isinstance(seg, dict):
                continue
            ni = seg.get("node_indices")
            pl = seg.get("path_local")
            if not isinstance(ni, list) or len(ni) < 2:
                continue
            idxs = []
            for x in ni:
                try:
                    idxs.append(int(x))
                except (TypeError, ValueError):
                    idxs = []
                    break
            if len(idxs) < 2:
                continue
            path = []
            if isinstance(pl, list):
                for p in pl:
                    if isinstance(p, (list, tuple)) and len(p) >= 2:
                        try:
                            path.append((float(p[0]), float(p[1])))
                        except (TypeError, ValueError):
                            path = []
                            break
            if len(path) < 2:
                continue
            app.trunk_map_segments.append({"node_indices": idxs, "path_local": path})
        if hasattr(app, "normalize_trunk_segments_to_graph_edges"):
            try:
                app.normalize_trunk_segments_to_graph_edges()
            except Exception:
                pass
        elif hasattr(app, "sync_trunk_segment_paths_from_nodes"):
            try:
                app.sync_trunk_segment_paths_from_nodes()
            except Exception:
                pass
        if app.trunk_map_segments:
            li = app.trunk_map_segments[-1].get("node_indices") or []
            if li:
                try:
                    app._trunk_route_last_node_idx = int(li[-1])
                except (TypeError, ValueError):
                    pass

        loaded_allowed = data.get("allowed_pipes", {})
        app.allowed_pipes = {}
        for mat, pns in app.pipe_db.items():
            app.allowed_pipes[mat] = {}
            for pn, ods in pns.items():
                app.allowed_pipes[mat][pn] = list(ods.keys())

        def _resolve_pn_key(pn_map: dict, pn_raw):
            if pn_raw in pn_map:
                return pn_raw
            s = str(pn_raw).strip()
            if s in pn_map:
                return s
            for k in pn_map:
                if str(k).strip() == s:
                    return k
            return None

        for mat in loaded_allowed:
            if mat not in app.allowed_pipes:
                continue
            src = loaded_allowed[mat]
            if not isinstance(src, dict):
                continue
            for pn_raw, ods in src.items():
                pn_key = _resolve_pn_key(app.allowed_pipes[mat], pn_raw)
                if pn_key is None:
                    continue
                allowed_list = app.allowed_pipes[mat][pn_key]
                allowed_od = {str(x).strip() for x in allowed_list}
                if isinstance(ods, list):
                    app.allowed_pipes[mat][pn_key] = [
                        x for x in ods if str(x).strip() in allowed_od
                    ]
        
        p = data.get("params", {})
        app.var_lat_step.set(p.get("lat", "0.9"))
        app.var_emit_step.set(p.get("emit", "0.3"))
        app.var_emit_flow.set(p.get("flow", "1.05"))
        if hasattr(app, "var_emit_model"):
            app.var_emit_model.set(str(p.get("emit_model", "")))
        if hasattr(app, "var_emit_nominal_flow"):
            app.var_emit_nominal_flow.set(str(p.get("emit_nominal_flow", "")))
        if hasattr(app, "var_emit_k_coeff"):
            app.var_emit_k_coeff.set(str(p.get("emit_k_coeff", "")))
        if hasattr(app, "var_emit_x_exp"):
            app.var_emit_x_exp.set(str(p.get("emit_x_exp", "")))
            if bool(p.get("emitter_compensated", False)):
                try:
                    xv = float(str(app.var_emit_x_exp.get()).replace(",", "."))
                except ValueError:
                    xv = 0.5
                if abs(xv) > 1e-12:
                    app.var_emit_x_exp.set("0")
        if hasattr(app, "var_emit_kd_coeff"):
            app.var_emit_kd_coeff.set(str(p.get("emit_kd_coeff", "1.0")))
        app.var_max_lat_len.set(p.get("max_len", "0"))
        app.var_lat_block_count.set(p.get("blocks", "0"))
        app.var_fixed_sec.set(p.get("fixed_sec", True))
        app.var_num_sec.set(p.get("num_sec", "3"))
        app.var_v_min.set(p.get("v_min", "0.5"))
        app.var_v_max.set(p.get("v_max", "1.5"))
        if hasattr(app, "var_valve_h_max_m"):
            app.var_valve_h_max_m.set(str(p.get("valve_h_max_m", "0")))
        if hasattr(app, "var_valve_h_max_optimize"):
            app.var_valve_h_max_optimize.set(bool(p.get("valve_h_max_optimize", True)))
        if hasattr(app, "var_show_emitter_flow"):
            app.var_show_emitter_flow.set(bool(p.get("show_emitter_flow_on_map", True)))
        
        # ОНОВЛЮЄМО МЕНЮШКИ ПІСЛЯ ЗАВАНТАЖЕННЯ БАЗИ ТРУБ
        avail = list(app.pipe_db.keys())
        if hasattr(app, 'cb_mat'): app.cb_mat.config(values=avail)
        app.pipe_material.set(p.get("mat", "PVC"))
        app.update_pn_dropdown(skip_reset=True)
        app.pipe_pn.set(p.get("pn", "6"))
        if hasattr(app, "sync_hydro_pipe_summary"):
            app.sync_hydro_pipe_summary()

        if hasattr(app, "var_lateral_solver_mode"):
            m = str(p.get("lateral_solver_mode", "bisection")).strip().lower()
            if m not in ("compare", "bisection", "newton"):
                m = "bisection"
            app.var_lateral_solver_mode.set(m)

        if hasattr(app, "var_emit_h_min"):
            app.var_emit_h_min.set(str(p.get("emitter_h_min_m", "1.0")))
        if hasattr(app, "var_emit_h_ref"):
            app.var_emit_h_ref.set(str(p.get("emitter_h_ref_m", "10.0")))
        if hasattr(app, "var_lat_inner_d_mm"):
            app.var_lat_inner_d_mm.set(str(p.get("lateral_inner_d_mm", "13.6")))
        if hasattr(app, "var_lateral_model"):
            app.var_lateral_model.set(str(p.get("lateral_model", "")))
        if hasattr(app, "var_emit_h_press_min"):
            app.var_emit_h_press_min.set(str(p.get("emitter_h_press_min_m", "0")))
        if hasattr(app, "var_emit_h_press_max"):
            app.var_emit_h_press_max.set(str(p.get("emitter_h_press_max_m", "0")))
        app._submain_topo_in_headloss = bool(p.get("submain_topo_in_headloss", True))

        if hasattr(app, "var_lat_disp_step"):
            app.var_lat_disp_step.set(p.get("lat_disp_step", "1"))
            app.var_lat_disp_n_start.set(p.get("lat_disp_n_start", ""))
            app.var_lat_disp_n_end.set(p.get("lat_disp_n_end", ""))
            app.var_lat_disp_use_step.set(bool(p.get("lat_disp_use_step", True)))
            app.var_lat_disp_use_start.set(bool(p.get("lat_disp_use_start", False)))
            app.var_lat_disp_use_end.set(bool(p.get("lat_disp_use_end", False)))
        
        app.regenerate_grid()
        
        if "calc_results" in data:
            app.calc_results = data["calc_results"]
        
        if hasattr(app, "zoom_to_fit"):
            app.zoom_to_fit()
        app.redraw()
        if hasattr(app, "_refresh_active_block_combo"):
            app._refresh_active_block_combo()
        if hasattr(app, "sync_hydro_pipe_summary"):
            app.sync_hydro_pipe_summary()
        if hasattr(app, "sync_srtm_model_status"):
            app.sync_srtm_model_status()
        if hasattr(app, "refresh_map_after_project_load"):
            app.refresh_map_after_project_load()
    except Exception as e:
        messagebox.showerror("Помилка", f"Не вдалося завантажити проект:\n{e}")

def import_kml(app):
    filepath = filedialog.askopenfilename(title="Імпорт KML", filetypes=[("KML Files", "*.kml")])
    if not filepath: return
        
    try:
        with open(filepath, "r", encoding="utf-8") as f: content = f.read()
            
        coords_match = re.search(r'<coordinates>(.*?)</coordinates>', content, re.DOTALL)
        if not coords_match:
            messagebox.showerror("Помилка", "Координати не знайдені в KML!")
            return
            
        raw_coords = coords_match.group(1).strip().split()
        geo_points = []
        for pt in raw_coords:
            parts = pt.split(',')
            if len(parts) >= 2: geo_points.append((float(parts[0]), float(parts[1])))
                
        if not geo_points: return
            
        app.geo_ref = geo_points[0]
        ref_lon, ref_lat = app.geo_ref
        
        app.clear_all()
        app.geo_ref = geo_points[0]
        
        R = 6378137
        ring = []
        for lon, lat in geo_points:
            dx = math.radians(lon - ref_lon) * R * math.cos(math.radians(ref_lat))
            dy = -math.radians(lat - ref_lat) * R
            ring.append((dx, dy))
        snap = (
            app._snapshot_block_params()
            if hasattr(app, "_snapshot_block_params")
            else {}
        )
        app.field_blocks = [
            {
                "ring": ring,
                "edge_angle": None,
                "submain_lines": [],
                "auto_laterals": [],
                "manual_laterals": [],
                "params": snap,
            }
        ]
        app.points = []
        app.is_closed = False
        app.mode.set("SET_DIR")
        if hasattr(app, "zoom_to_fit"):
            app.zoom_to_fit()
        app.redraw()
        messagebox.showinfo(
            "Успіх",
            "Контур завантажено. За потреби намалюйте ще блоки (ПКМ — замкнути), потім «Завершити блоки → напрямок рядів» і два кліки напрямку.",
        )
    except Exception as e:
        messagebox.showerror("Помилка", f"Не вдалося імпортувати KML:\n{e}")

def import_srtm_kml(app):
    filepath = filedialog.askopenfilename(title="Імпорт контуру для SRTM (KML)", filetypes=[("KML Files", "*.kml")])
    if not filepath: return
        
    try:
        with open(filepath, "r", encoding="utf-8") as f: content = f.read()
            
        coords_match = re.search(r'<coordinates>(.*?)</coordinates>', content, re.DOTALL)
        if not coords_match:
            messagebox.showerror("Помилка", "Координати не знайдені в KML!")
            return
            
        raw_coords = coords_match.group(1).strip().split()
        geo_points = []
        for pt in raw_coords:
            parts = pt.split(',')
            if len(parts) >= 2: geo_points.append((float(parts[0]), float(parts[1])))
                
        if not geo_points: return
            
        app.topo.clear_srtm_boundary()
        
        if getattr(app, "geo_ref", None) is None:
            app.geo_ref = geo_points[0]
            
        ref_lon, ref_lat = app.geo_ref
        
        R = 6378137.0 
        for lon, lat in geo_points:
            dx = math.radians(lon - ref_lon) * R * math.cos(math.radians(ref_lat))
            dy = -math.radians(lat - ref_lat) * R 
            app.topo.srtm_boundary_pts_local.append((dx, dy))
            
        if hasattr(app, "zoom_to_fit"):
            app.zoom_to_fit()
        app.redraw()
        if hasattr(app, "sync_srtm_model_status"):
            app.sync_srtm_model_status()
        messagebox.showinfo("Успіх", "Контур для SRTM успішно завантажено. Встановіть роздільну здатність та натисніть 'Завантажити з супутника'.")
    except Exception as e:
        messagebox.showerror("Помилка", f"Не вдалося імпортувати KML для SRTM:\n{e}")

def export_dxf(app):
    """Лише ізолінії (локальні координати проєкту), DXF R12: POLYLINE + висота в групі 38."""
    cached = getattr(app, "cached_contours", None) or []
    if not cached:
        messagebox.showwarning(
            "Увага",
            "Немає ізоліній для експорту.\n"
            "У блоці рельєфу натисніть «Побудувати ізолінії» — гідравлічний розрахунок не потрібен.",
        )
        return

    proj_dir = ensure_project_dir(app)
    default_name = f"{app.var_proj_name.get().strip()}_isolines.dxf"

    filepath = filedialog.asksaveasfilename(
        initialdir=proj_dir,
        initialfile=default_name,
        defaultextension=".dxf",
        filetypes=[("DXF Files", "*.dxf")],
    )
    if not filepath:
        return

    try:
        from modules.geo_module.dem_contours.dxf_export import write_contours_dxf

        features = [(item["geom"], float(item["z"])) for item in cached]
        n = write_contours_dxf(features, filepath, layer_name="ISOLINES")
        messagebox.showinfo("Експорт", f"Збережено {n} поліліній ізоліній (DXF R12):\n{filepath}")
    except Exception as e:
        messagebox.showerror("Помилка", f"Не вдалося експортувати DXF:\n{e}")

def export_kml(app):
    if not app.geo_ref:
        messagebox.showwarning("Помилка", "Проект не має гео-прив'язки!")
        return
        
    proj_dir = ensure_project_dir(app)
    default_name = f"{app.var_proj_name.get().strip()}_Earth.kml"
    
    filepath = filedialog.asksaveasfilename(initialdir=proj_dir, initialfile=default_name, defaultextension=".kml", filetypes=[("KML Files", "*.kml")])
    if not filepath: return
        
    try:
        ref_lon, ref_lat = app.geo_ref
        R = 6378137
        
        def to_geo(x, y):
            lat = ref_lat + math.degrees(-y / R)
            lon = ref_lon + math.degrees(x / (R * math.cos(math.radians(ref_lat))))
            return lon, lat

        kml = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2">', '<Document>']
        
        kml.append('<Style id="border"><LineStyle><color>ff00ffff</color><width>3</width></LineStyle></Style>')
        kml.append('<Style id="drip"><LineStyle><color>ff00aa00</color><width>1</width></LineStyle></Style>')
        kml.append('<Style id="pipe"><LineStyle><color>ff0000ff</color><width>4</width></LineStyle></Style>')

        for bi, ring in enumerate(b["ring"] for b in getattr(app, "field_blocks", []) or []):
            if len(ring) >= 2:
                kml.append(f'<Placemark><name>Field_{bi+1}</name><styleUrl>#border</styleUrl><LineString><coordinates>')
                pts = ring + [ring[0]]
                kml.append(" ".join([f"{to_geo(p[0], p[1])[0]},{to_geo(p[0], p[1])[1]},0" for p in pts]))
                kml.append('</coordinates></LineString></Placemark>')
        if app.points and len(app.points) > 1:
            pts = app.points + ([app.points[0]] if getattr(app, "is_closed", False) else [])
            kml.append('<Placemark><name>Field_draft</name><styleUrl>#border</styleUrl><LineString><coordinates>')
            kml.append(" ".join([f"{to_geo(p[0], p[1])[0]},{to_geo(p[0], p[1])[1]},0" for p in pts]))
            kml.append('</coordinates></LineString></Placemark>')

        step = max(1, app.export_lat_step_kml.get())
        lats = app._flatten_all_lats() if hasattr(app, "_flatten_all_lats") else []
        for idx, lat in enumerate(lats):
            if idx % step == 0:
                kml.append(f'<Placemark><name>Lat {idx}</name><styleUrl>#drip</styleUrl><LineString><coordinates>')
                kml.append(" ".join([f"{to_geo(p[0], p[1])[0]},{to_geo(p[0], p[1])[1]},0" for p in lat.coords]))
                kml.append('</coordinates></LineString></Placemark>')

        if app.calc_results.get("sections"):
            for idx, sec in enumerate(app.calc_results["sections"]):
                name = f"Pipe d{sec['d']} L={sec['L']:.1f}m"
                kml.append(f'<Placemark><name>{name}</name><styleUrl>#pipe</styleUrl><LineString><coordinates>')
                kml.append(" ".join([f"{to_geo(p[0], p[1])[0]},{to_geo(p[0], p[1])[1]},0" for p in sec["coords"]]))
                kml.append('</coordinates></LineString></Placemark>')

        kml.extend(['</Document>', '</kml>'])
        
        with open(filepath, "w", encoding="utf-8") as f: f.write("\n".join(kml))
        messagebox.showinfo("Експорт", f"KML збережено:\n{filepath}")
    except Exception as e: messagebox.showerror("Помилка", f"Не вдалося експортувати KML:\n{e}")

def export_elevation_grid_kml(app):
    rings = [b["ring"] for b in getattr(app, "field_blocks", []) or []]
    if app.points and len(app.points) >= 3:
        rings.append(list(app.points))
    if not app.geo_ref or not rings:
        messagebox.showwarning("Помилка", "Потрібен хоча б один замкнений контур поля з гео-прив'язкою!")
        return
        
    proj_dir = ensure_project_dir(app)
    default_name = f"{app.var_proj_name.get().strip()}_Grid_10x10.kml"
    
    filepath = filedialog.asksaveasfilename(initialdir=proj_dir, initialfile=default_name, defaultextension=".kml", filetypes=[("KML Files", "*.kml")])
    if not filepath: return
        
    try:
        polys = []
        for ring in rings:
            if len(ring) >= 3:
                g = Polygon(ring)
                if not g.is_valid:
                    g = g.buffer(0)
                if not g.is_empty:
                    polys.append(g)
        if not polys:
            raise ValueError("немає валідних полігонів")
        poly = unary_union(polys)
        if poly.is_empty:
            raise ValueError("порожня геометрія")
            
        minx, miny, maxx, maxy = poly.bounds
        grid_points = []
        
        x = minx
        while x <= maxx:
            y = miny
            while y <= maxy:
                if poly.covers(Point(x, y)):
                    grid_points.append((x, y))
                y += 10.0
            x += 10.0

        ref_lon, ref_lat = app.geo_ref
        R = 6378137
        
        kml = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2">', '<Document>']
        for idx, (x, y) in enumerate(grid_points):
            lat = ref_lat + math.degrees(-y / R)
            lon = ref_lon + math.degrees(x / (R * math.cos(math.radians(ref_lat))))
            kml.append(f'<Placemark><name>P{idx}</name><Point><coordinates>{lon},{lat},0</coordinates></Point></Placemark>')
            
        kml.extend(['</Document>', '</kml>'])
        
        with open(filepath, "w", encoding="utf-8") as f: f.write("\n".join(kml))
        messagebox.showinfo("Експорт", f"Сітка (точок: {len(grid_points)}) збережена у KML!")
    except Exception as e: messagebox.showerror("Помилка", f"Не вдалося створити сітку:\n{e}")

def export_pdf(app):
    if not hasattr(app, "last_report") or not app.last_report:
        messagebox.showwarning("Увага", "Спочатку виконайте розрахунок для генерації звіту!", parent=app.root)
        return
        
    try:
        from fpdf import FPDF
        from PIL import ImageGrab
    except ImportError:
        messagebox.showerror("Помилка", "Не встановлено бібліотеку fpdf2 або Pillow.", parent=app.root)
        return

    proj_dir = ensure_project_dir(app)
    
    app.root.update_idletasks()
    x = app.canvas.winfo_rootx()
    y = app.canvas.winfo_rooty()
    w = app.canvas.winfo_width()
    h = app.canvas.winfo_height()
    
    temp_img_path = os.path.join(proj_dir, "temp_canvas.png")
    try:
        ImageGrab.grab(bbox=(x, y, x+w, y+h)).save(temp_img_path)
    except Exception as e:
        messagebox.showwarning("Увага", f"Не вдалося зробити скріншот креслення:\n{e}", parent=app.root)
        pass 
        
    default_name = f"{app.var_proj_name.get().strip()}_Report.pdf"
    filepath = filedialog.asksaveasfilename(parent=app.root, initialdir=proj_dir, initialfile=default_name, defaultextension=".pdf", filetypes=[("PDF Files", "*.pdf")])
    if not filepath:
        if os.path.exists(temp_img_path):
            try: os.remove(temp_img_path)
            except: pass
        return
        
    try:
        pdf = FPDF()
        pdf.add_page()
        
        font_path = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts', 'arial.ttf')
        if os.path.exists(font_path):
            pdf.add_font("Arial", "", font_path)
            pdf.set_font("Arial", size=11)
        else:
            pdf.set_font("Helvetica", size=11)
            
        pdf.set_font_size(14)
        pdf.cell(0, 10, f"Гідравлічний звіт: {app.var_proj_name.get().strip()}", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(5)
        
        if os.path.exists(temp_img_path):
            pdf_w = 190
            pdf_h = pdf_w * (h / w) if w > 0 else 100
            if pdf_h > 240:
                pdf_h = 240
                pdf_w = pdf_h * (w / h)
            pdf.image(temp_img_path, x="C", w=pdf_w, h=pdf_h)
            pdf.ln(10)
        
        pdf.set_font_size(11)
        safe_text = app.last_report.replace("⚠️", "(!)").replace("➤", ">").replace("\r", "")
        for line in safe_text.split('\n'):
            pdf.multi_cell(0, 6, text=line, new_x="LMARGIN", new_y="NEXT")
            
        pdf.output(filepath)
        messagebox.showinfo("Експорт", f"PDF звіт успішно збережено:\n{filepath}", parent=app.root)
    except Exception as e: 
        messagebox.showerror("Помилка", f"Не вдалося експортувати PDF:\n{e}", parent=app.root)
    finally:
        if os.path.exists(temp_img_path):
            try: os.remove(temp_img_path)
            except: pass