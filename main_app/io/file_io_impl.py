import copy
import os
import json
import math
import re
import tempfile
from tkinter import filedialog

from main_app.ui.silent_messagebox import silent_showerror, silent_showinfo, silent_showwarning
from shapely.geometry import LineString, Polygon, Point
from shapely.ops import unary_union

from main_app.paths import DESIGNS_DIR, PIPES_DB_PATH
from modules.geo_module import srtm_tiles
from modules.hydraulic_module.trunk_map_graph import (
    ensure_trunk_node_ids,
    normalize_legacy_trunk_valve_kinds,
)


def _json_dict_key_str(k) -> str:
    """Ключі JSON-об'єкта мають бути рядками (tuple тощо — у стабільний рядок)."""
    if isinstance(k, str):
        return k
    if isinstance(k, bool):
        return "true" if k else "false"
    if isinstance(k, int):
        return str(int(k))
    if isinstance(k, float):
        if math.isnan(k) or math.isinf(k):
            return "_invalid_numeric_key_"
        if abs(k - round(k)) < 1e-9:
            return str(int(round(k)))
        return str(k)
    if isinstance(k, tuple) and len(k) == 2:
        return f"{str(k[0]).strip()}->{str(k[1]).strip()}"
    return str(k)


def _sanitize_for_json_export(obj, *, _depth: int = 0):
    """
    Рекурсивно приводить структуру до типів, безпечних для json.dumps(allow_nan=False).
    Усі ключі словників — str (tuple-ключі з кешів тощо не ламають серіалізацію).
    """
    if _depth > 120:
        return None
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return float(obj)
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:
            return str(obj)
    if isinstance(obj, dict):
        out: dict = {}
        for k, v in obj.items():
            out[_json_dict_key_str(k)] = _sanitize_for_json_export(v, _depth=_depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json_export(x, _depth=_depth + 1) for x in obj]
    if isinstance(obj, (set, frozenset)):
        return [_sanitize_for_json_export(x, _depth=_depth + 1) for x in obj]
    return str(obj)


def _atomic_write_text(filepath: str, text: str) -> None:
    """Атомарний запис UTF-8 (temp у тій самій теці + os.replace), щоб не залишати обірваний JSON."""
    filepath = os.path.abspath(filepath)
    d = os.path.dirname(filepath)
    os.makedirs(d, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".drdproj_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as wf:
            wf.write(text)
        os.replace(tmp_path, filepath)
    except Exception:
        try:
            if os.path.isfile(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _format_json_decode_error(filepath: str, err: json.JSONDecodeError) -> str:
    """Контекст рядка з файлу для діагностики пошкодженого JSON."""
    fp = os.path.abspath(filepath)
    lines: list = []
    try:
        with open(fp, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return f"{fp}\n{err}"
    ln = int(getattr(err, "lineno", 1) or 1)
    col = int(getattr(err, "colno", 1) or 1)
    i0 = max(0, ln - 4)
    i1 = min(len(lines), ln + 3)
    parts = [f"Файл: {fp}", f"Помилка JSON: {err}", f"Рядок {ln}, колонка {col}:", ""]
    for i in range(i0, i1):
        prefix = ">" if i + 1 == ln else " "
        parts.append(f"{prefix} {i + 1:5d}: {lines[i]}")
    return "\n".join(parts)


def _normalize_trunk_irrigation_hydro_cache_from_json(cache: dict) -> dict:
    """Після json.load ключі слотів/ребер стають str — відновлюємо int-ключі сегментів у seg_dominant_slot."""
    if not isinstance(cache, dict):
        return cache
    out = copy.deepcopy(cache)
    m = out.get("seg_dominant_slot")
    if isinstance(m, dict):
        new_m: dict = {}
        for k, v in m.items():
            try:
                ik = int(k)
            except (TypeError, ValueError):
                continue
            if v is None:
                new_m[ik] = None
            else:
                try:
                    new_m[ik] = int(v)
                except (TypeError, ValueError):
                    try:
                        new_m[ik] = int(float(v))
                    except (TypeError, ValueError):
                        new_m[ik] = v
        out["seg_dominant_slot"] = new_m
    return out


def _trim_orchestrator_after_persist(app) -> None:
    orch = getattr(app, "orchestrator", None)
    trim = getattr(orch, "trim_auxiliary_results_after_persist", None) if orch is not None else None
    if callable(trim):
        try:
            trim()
        except Exception:
            pass


def _normalize_consumer_schedule_payload(raw) -> dict:
    """Розклад: групи (legacy) + 48 слотів поливу (irrigation_slots[i] = id вузлів)."""
    out_groups = []
    if isinstance(raw, dict):
        groups = raw.get("groups")
        if isinstance(groups, list):
            for g in groups:
                if not isinstance(g, dict):
                    continue
                title = str(g.get("title", "")).strip() or "Група"
                ids = g.get("node_ids")
                if ids is None:
                    ids = g.get("nodes")
                if not isinstance(ids, list):
                    ids = []
                clean = []
                for x in ids:
                    s = str(x).strip()
                    if s and s not in clean:
                        clean.append(s)
                out_groups.append({"title": title, "node_ids": clean})

    slots: list = []
    sraw = None
    if isinstance(raw, dict):
        sraw = raw.get("irrigation_slots")
    if isinstance(sraw, list):
        for i in range(48):
            cell: list = []
            if i < len(sraw) and isinstance(sraw[i], list):
                for x in sraw[i]:
                    s = str(x).strip()
                    if s and s not in cell:
                        cell.append(s)
            slots.append(cell)
    else:
        slots = [[] for _ in range(48)]

    out: dict = {"groups": out_groups, "irrigation_slots": slots}
    if isinstance(raw, dict):
        try:
            v = raw.get("max_pump_head_m")
            if v is not None and str(v).strip() != "":
                fv = float(v)
                if fv >= 0.0:
                    out["max_pump_head_m"] = max(0.0, min(400.0, float(fv)))
        except (TypeError, ValueError):
            pass
        try:
            vm = raw.get("trunk_schedule_v_max_mps")
            if vm is not None and str(vm).strip() != "":
                fvm = float(vm)
                if fvm >= 0.0:
                    out["trunk_schedule_v_max_mps"] = max(0.0, min(8.0, float(fvm)))
        except (TypeError, ValueError):
            pass
        try:
            tq = raw.get("trunk_schedule_test_q_m3h")
            if tq is not None and str(tq).strip() != "":
                ftq = float(tq)
                if ftq >= 0.0:
                    out["trunk_schedule_test_q_m3h"] = max(0.0, min(10000.0, float(ftq)))
        except (TypeError, ValueError):
            pass
        try:
            th = raw.get("trunk_schedule_test_h_m")
            if th is not None and str(th).strip() != "":
                fth = float(th)
                if fth >= 0.0:
                    out["trunk_schedule_test_h_m"] = max(0.0, min(400.0, float(fth)))
        except (TypeError, ValueError):
            pass
        try:
            ms = raw.get("trunk_schedule_max_sections_per_edge")
            if ms is not None and str(ms).strip() != "":
                fms = int(float(ms))
                out["trunk_schedule_max_sections_per_edge"] = max(1, min(4, int(fms)))
        except (TypeError, ValueError):
            pass
        try:
            vw = raw.get("trunk_display_velocity_warn_mps")
            if vw is not None and str(vw).strip() != "":
                fvw = float(vw)
                if fvw >= 0.0:
                    out["trunk_display_velocity_warn_mps"] = max(0.0, min(8.0, float(fvw)))
        except (TypeError, ValueError):
            pass
        goal = str(raw.get("trunk_schedule_opt_goal", "weight")).strip().lower()
        if goal not in ("weight", "money", "cost_index"):
            goal = "weight"
        if goal == "cost_index":
            goal = "money"
        out["trunk_schedule_opt_goal"] = goal
        out["trunk_pipes_selected"] = bool(raw.get("trunk_pipes_selected", False))
        src_mode = str(raw.get("srtm_source_mode", "auto")).strip().lower()
        if src_mode not in ("auto", "skadi_local", "open_elevation", "earthdata"):
            src_mode = "auto"
        out["srtm_source_mode"] = src_mode
    return out


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
    cp = getattr(app, "control_panel", None)
    if cp is not None and hasattr(cp, "_flush_schedule_max_pump_head_to_app"):
        try:
            cp._flush_schedule_max_pump_head_to_app()
        except Exception:
            pass
    if cp is not None and hasattr(cp, "_flush_schedule_trunk_v_max_to_app"):
        try:
            cp._flush_schedule_trunk_v_max_to_app()
        except Exception:
            pass
    if cp is not None and hasattr(cp, "_flush_schedule_trunk_min_seg_to_app"):
        try:
            cp._flush_schedule_trunk_min_seg_to_app()
        except Exception:
            pass
    if cp is not None and hasattr(cp, "_flush_schedule_trunk_max_sections_to_app"):
        try:
            cp._flush_schedule_trunk_max_sections_to_app()
        except Exception:
            pass
    if cp is not None and hasattr(cp, "_flush_schedule_trunk_opt_goal_to_app"):
        try:
            cp._flush_schedule_trunk_opt_goal_to_app()
        except Exception:
            pass
    if cp is not None and hasattr(cp, "_flush_schedule_trunk_pipe_mode_to_app"):
        try:
            cp._flush_schedule_trunk_pipe_mode_to_app()
        except Exception:
            pass
    if cp is not None and hasattr(cp, "_flush_schedule_test_qh_to_app"):
        try:
            cp._flush_schedule_test_qh_to_app()
        except Exception:
            pass
    # Магістраль у JSON: спочатку trunk_tree → сегменти (результат оптимізації/телескоп у відрізках),
    # потім синхронізація дерева з карти, знову дерево → сегменти — щоб довжини/секції узгодились перед записом.
    if hasattr(app, "_sync_trunk_segment_hydraulic_props_from_tree"):
        try:
            app._sync_trunk_segment_hydraulic_props_from_tree()
        except Exception:
            pass
    if hasattr(app, "sync_trunk_tree_data_from_trunk_map"):
        try:
            app.sync_trunk_tree_data_from_trunk_map()
        except Exception:
            pass
    if hasattr(app, "_sync_trunk_segment_hydraulic_props_from_tree"):
        try:
            app._sync_trunk_segment_hydraulic_props_from_tree()
        except Exception:
            pass
    trunk_nodes = getattr(app, "trunk_map_nodes", None) or []
    if trunk_nodes:
        ensure_trunk_node_ids(trunk_nodes)
    trunk_segments = []
    for seg in list(getattr(app, "trunk_map_segments", []) or []):
        if not isinstance(seg, dict):
            continue
        rec = dict(seg)
        raw_secs = rec.get("sections")
        if not isinstance(raw_secs, list):
            raw_secs = rec.get("telescoped_sections")
        if isinstance(raw_secs, list):
            rec["sections"] = copy.deepcopy(raw_secs)
            rec["telescoped_sections"] = copy.deepcopy(raw_secs)
        trunk_segments.append(rec)
    _tap = getattr(app, "trunk_allowed_pipes", None)
    if not isinstance(_tap, dict):
        _tap = {}
    _thc = getattr(app, "trunk_irrigation_hydro_cache", None)
    trunk_hydro_json = copy.deepcopy(_thc) if isinstance(_thc, dict) else None
    trunk_payload = {
        "nodes": list(trunk_nodes),
        "segments": trunk_segments,
        "allowed_pipes": copy.deepcopy(_tap),
    }

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
        "trunk": trunk_payload,
        "trunk_map_nodes": list(trunk_nodes),
        "trunk_map_segments": trunk_segments,
        "consumer_schedule": copy.deepcopy(
            _normalize_consumer_schedule_payload(getattr(app, "consumer_schedule", None))
        ),
        "trunk_irrigation_hydro_cache": trunk_hydro_json,
        "project_zone_bounds_local": (
            list(app.project_zone_bounds_local)
            if getattr(app, "project_zone_bounds_local", None) is not None
            else None
        ),
        "project_zone_ring_local": (
            [list(p) for p in (getattr(app, "project_zone_ring_local", None) or [])]
            if getattr(app, "project_zone_ring_local", None)
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
    clean = _sanitize_for_json_export(data)
    text = json.dumps(
        clean,
        indent=4,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )
    _atomic_write_text(filepath, text)
    project_db_path = os.path.join(proj_dir, "pipes_db.json")
    clean_pipes = _sanitize_for_json_export(app.pipe_db)
    text_pipes = json.dumps(
        clean_pipes,
        indent=4,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )
    _atomic_write_text(project_db_path, text_pipes)


def persist_project_snapshot(app, *, silent: bool = True) -> bool:
    """
    Записати поточний стан у JSON проєкту (як «Зберегти»), без діалогу вибору файлу.
    Використовує останній шлях відкриття/збереження або designs/<ім'я>/<ім'я>.json.
    """
    try:
        proj_dir = ensure_project_dir(app)
        fp = getattr(app, "_project_json_filepath", None)
        if fp:
            fp = os.path.abspath(fp)
        else:
            name = (app.var_proj_name.get() or "").strip() or "Untitled_Project"
            fp = os.path.join(proj_dir, f"{name}.json")
        data = _collect_project_data(app, force_georeferenced=False)
        _write_project_to_disk(app, fp, data)
        app._project_json_filepath = fp
        if not silent:
            silent_showinfo(app.root, "Збережено", f"Проект успішно збережено в:\n{fp}")
        _trim_orchestrator_after_persist(app)
        return True
    except Exception as e:
        if silent:
            try:
                silent_showwarning(
                    app.root,
                    "Автозбереження проєкту",
                    f"Не вдалося записати JSON (результати магістралі лишились лише в пам’яті):\n{e}",
                )
            except Exception:
                pass
        else:
            silent_showerror(app.root, "Помилка", f"Не вдалося зберегти проект:\n{e}")
        return False


def save_project(app, force_georeferenced=False):
    if force_georeferenced and not getattr(app, "geo_ref", None):
        silent_showwarning(app.root, 
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
        app._project_json_filepath = os.path.abspath(filepath)
        silent_showinfo(app.root, "Збережено", f"Проект успішно збережено в:\n{filepath}")
        _trim_orchestrator_after_persist(app)
    except Exception as e:
        silent_showerror(app.root, "Помилка", f"Не вдалося зберегти проект:\n{e}")


def save_project_as(app, force_georeferenced=False):
    if force_georeferenced and not getattr(app, "geo_ref", None):
        silent_showwarning(app.root, 
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
        app._project_json_filepath = os.path.abspath(filepath)
        silent_showinfo(app.root, 
            "Збережено",
            f"Проект збережено:\n{filepath}\n\nТека проєкту:\n{proj_dir}",
        )
        _trim_orchestrator_after_persist(app)
    except Exception as e:
        silent_showerror(app.root, "Помилка", f"Не вдалося зберегти проект:\n{e}")


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
        pzr = data.get("project_zone_ring_local")
        app.project_zone_ring_local = None
        if isinstance(pzr, list) and len(pzr) >= 3:
            ring_pz = []
            for p in pzr:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    try:
                        ring_pz.append((float(p[0]), float(p[1])))
                    except (TypeError, ValueError):
                        continue
            if len(ring_pz) >= 3:
                app.project_zone_ring_local = ring_pz
        szr = data.get("srtm_zone_ring_local")
        if isinstance(szr, list) and len(szr) >= 3:
            ring = []
            for p in szr:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    ring.append((float(p[0]), float(p[1])))
            if len(ring) >= 3:
                app.topo.srtm_boundary_pts_local = ring
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
        tr_pkg = data.get("trunk")
        if not isinstance(tr_pkg, dict):
            tr_pkg = {}
        _node_rows = tr_pkg.get("nodes")
        if not isinstance(_node_rows, list):
            _node_rows = data.get("trunk_map_nodes") or []
        _seg_rows = tr_pkg.get("segments")
        if not isinstance(_seg_rows, list):
            _seg_rows = data.get("trunk_map_segments") or []
        app.trunk_map_nodes = []
        for row in _node_rows:
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
            slab = row.get("schedule_label")
            if slab is not None:
                s = str(slab).strip()
                if s:
                    rec["schedule_label"] = s
            qm = row.get("q_demand_m3s")
            if qm is not None:
                try:
                    rec["q_demand_m3s"] = float(qm)
                except (TypeError, ValueError):
                    pass
            for _sq in ("trunk_schedule_q_m3h", "trunk_schedule_h_m"):
                if _sq not in row:
                    continue
                try:
                    fv = float(row[_sq])
                    if math.isfinite(fv):
                        rec[_sq] = fv
                except (TypeError, ValueError):
                    pass
            app.trunk_map_nodes.append(rec)
        ensure_trunk_node_ids(app.trunk_map_nodes)
        app.trunk_map_segments = []
        app._trunk_route_last_node_idx = None
        for seg in _seg_rows:
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
            seg_rec: dict = {"node_indices": idxs, "path_local": path}
            for dk in ("d_inner_mm", "c_hw"):
                if dk not in seg:
                    continue
                try:
                    fv = float(seg[dk])
                    if math.isfinite(fv):
                        seg_rec[dk] = fv
                except (TypeError, ValueError):
                    pass
            for sk in ("pipe_material", "pipe_pn", "pipe_od"):
                if sk not in seg or seg[sk] is None:
                    continue
                s = str(seg[sk]).strip()
                if s:
                    seg_rec[sk] = s
            # Телескоп магістралі: сумісність ключів sections / telescoped_sections.
            raw_secs = seg.get("sections")
            if not isinstance(raw_secs, list):
                raw_secs = seg.get("telescoped_sections")
            if isinstance(raw_secs, list):
                secs = []
                for row in raw_secs:
                    if not isinstance(row, dict):
                        continue
                    try:
                        sl = float(row.get("length_m", 0.0))
                        sd = float(row.get("d_inner_mm", 0.0))
                        sc = float(row.get("c_hw", 140.0))
                    except (TypeError, ValueError):
                        continue
                    if sl <= 1e-9 or sd <= 1e-9:
                        continue
                    sec_row = {
                        "length_m": float(sl),
                        "d_inner_mm": float(sd),
                        "c_hw": float(sc),
                    }
                    for ek in ("d_nom_mm", "material", "pn", "head_loss_m", "weight_kg", "objective_cost"):
                        if ek not in row:
                            continue
                        try:
                            sec_row[ek] = float(row[ek]) if ek in ("d_nom_mm", "head_loss_m", "weight_kg", "objective_cost") else str(row[ek])
                        except (TypeError, ValueError):
                            if ek in ("material", "pn"):
                                sec_row[ek] = str(row[ek])
                    secs.append(sec_row)
                if secs:
                    seg_rec["sections"] = secs
                    seg_rec["telescoped_sections"] = copy.deepcopy(secs)
            if "bom_length_zero" in seg:
                seg_rec["bom_length_zero"] = bool(seg.get("bom_length_zero"))
            app.trunk_map_segments.append(seg_rec)
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
        try:
            normalize_legacy_trunk_valve_kinds(app.trunk_map_nodes, app.trunk_map_segments)
        except Exception:
            pass
        if app.trunk_map_segments:
            li = app.trunk_map_segments[-1].get("node_indices") or []
            if li:
                try:
                    app._trunk_route_last_node_idx = int(li[-1])
                except (TypeError, ValueError):
                    pass

        if hasattr(app, "sync_trunk_tree_data_from_trunk_map"):
            try:
                app.sync_trunk_tree_data_from_trunk_map()
            except Exception:
                pass

        if hasattr(app, "consumer_schedule"):
            app.consumer_schedule = _normalize_consumer_schedule_payload(
                data.get("consumer_schedule")
            )
        if hasattr(app, "normalize_consumer_schedule"):
            try:
                app.normalize_consumer_schedule()
            except Exception:
                pass
        cp = getattr(app, "control_panel", None)
        if cp is not None and hasattr(cp, "_sync_schedule_max_pump_head_ui"):
            try:
                cp._sync_schedule_max_pump_head_ui()
            except Exception:
                pass
        if cp is not None and hasattr(cp, "_sync_schedule_trunk_v_max_ui"):
            try:
                cp._sync_schedule_trunk_v_max_ui()
            except Exception:
                pass
        if cp is not None and hasattr(cp, "_sync_schedule_trunk_min_seg_ui"):
            try:
                cp._sync_schedule_trunk_min_seg_ui()
            except Exception:
                pass
        if cp is not None and hasattr(cp, "_sync_schedule_trunk_opt_goal_ui"):
            try:
                cp._sync_schedule_trunk_opt_goal_ui()
            except Exception:
                pass
        if cp is not None and hasattr(cp, "_sync_schedule_trunk_max_sections_ui"):
            try:
                cp._sync_schedule_trunk_max_sections_ui()
            except Exception:
                pass
        if cp is not None and hasattr(cp, "_sync_schedule_trunk_pipe_mode_ui"):
            try:
                cp._sync_schedule_trunk_pipe_mode_ui()
            except Exception:
                pass
        if hasattr(app, "sync_trunk_display_velocity_warn_var_from_schedule"):
            try:
                app.sync_trunk_display_velocity_warn_var_from_schedule()
            except Exception:
                pass
        if hasattr(app, "sync_srtm_source_mode_var_from_schedule"):
            try:
                app.sync_srtm_source_mode_var_from_schedule()
            except Exception:
                pass
        if hasattr(app, "sync_srtm_source_mode_widgets"):
            try:
                app.sync_srtm_source_mode_widgets()
            except Exception:
                pass
        if cp is not None and hasattr(cp, "_sync_schedule_test_qh_ui"):
            try:
                cp._sync_schedule_test_qh_ui()
            except Exception:
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

        app.trunk_allowed_pipes = {}
        for mat, pns in app.pipe_db.items():
            app.trunk_allowed_pipes[mat] = {}
            for pn, ods in pns.items():
                app.trunk_allowed_pipes[mat][pn] = list(ods.keys())
        trunk_ap_loaded = {}
        if isinstance(tr_pkg.get("allowed_pipes"), dict):
            trunk_ap_loaded = tr_pkg["allowed_pipes"]
        elif isinstance(data.get("trunk_allowed_pipes"), dict):
            trunk_ap_loaded = data["trunk_allowed_pipes"]
        if trunk_ap_loaded:
            for mat in trunk_ap_loaded:
                if mat not in app.trunk_allowed_pipes:
                    continue
                src = trunk_ap_loaded[mat]
                if not isinstance(src, dict):
                    continue
                for pn_raw, ods in src.items():
                    pn_key = _resolve_pn_key(app.trunk_allowed_pipes[mat], pn_raw)
                    if pn_key is None:
                        continue
                    allowed_list = app.trunk_allowed_pipes[mat][pn_key]
                    allowed_od = {str(x).strip() for x in allowed_list}
                    if isinstance(ods, list):
                        app.trunk_allowed_pipes[mat][pn_key] = [
                            x for x in ods if str(x).strip() in allowed_od
                        ]
        else:
            app.trunk_allowed_pipes = copy.deepcopy(app.allowed_pipes)

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

        raw_thc = data.get("trunk_irrigation_hydro_cache")
        if isinstance(raw_thc, dict) and raw_thc:
            app.trunk_irrigation_hydro_cache = _normalize_trunk_irrigation_hydro_cache_from_json(
                raw_thc
            )
        else:
            app.trunk_irrigation_hydro_cache = None
        if hasattr(app, "notify_irrigation_schedule_ui"):
            try:
                app.notify_irrigation_schedule_ui()
            except Exception:
                pass

        app._project_json_filepath = os.path.abspath(filepath)

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
    except json.JSONDecodeError as e:
        silent_showerror(
            app.root,
            "Помилка JSON",
            _format_json_decode_error(filepath, e),
        )
    except Exception as e:
        silent_showerror(app.root, "Помилка", f"Не вдалося завантажити проект:\n{e}")

def import_kml(app):
    filepath = filedialog.askopenfilename(title="Імпорт KML", filetypes=[("KML Files", "*.kml")])
    if not filepath: return
        
    try:
        with open(filepath, "r", encoding="utf-8") as f: content = f.read()
            
        coords_match = re.search(r'<coordinates>(.*?)</coordinates>', content, re.DOTALL)
        if not coords_match:
            silent_showerror(app.root, "Помилка", "Координати не знайдені в KML!")
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
        silent_showinfo(app.root, 
            "Успіх",
            "Контур завантажено. За потреби намалюйте ще блоки (ПКМ — замкнути), потім «Завершити блоки → напрямок рядів» і два кліки напрямку.",
        )
    except Exception as e:
        silent_showerror(app.root, "Помилка", f"Не вдалося імпортувати KML:\n{e}")

def import_srtm_kml(app):
    filepath = filedialog.askopenfilename(title="Імпорт контуру для SRTM (KML)", filetypes=[("KML Files", "*.kml")])
    if not filepath: return
        
    try:
        with open(filepath, "r", encoding="utf-8") as f: content = f.read()
            
        coords_match = re.search(r'<coordinates>(.*?)</coordinates>', content, re.DOTALL)
        if not coords_match:
            silent_showerror(app.root, "Помилка", "Координати не знайдені в KML!")
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
        silent_showinfo(app.root, "Успіх", "Контур для SRTM успішно завантажено. Встановіть роздільну здатність та натисніть 'Завантажити з супутника'.")
    except Exception as e:
        silent_showerror(app.root, "Помилка", f"Не вдалося імпортувати KML для SRTM:\n{e}")

def export_dxf(app):
    """Лише ізолінії (локальні координати проєкту), DXF R12: POLYLINE + висота в групі 38."""
    cached = getattr(app, "cached_contours", None) or []
    if not cached:
        silent_showwarning(app.root, 
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
        n = write_contours_dxf(features, filepath, layer_name="c-1")
        silent_showinfo(app.root, "Експорт", f"Збережено {n} поліліній ізоліній (DXF R12):\n{filepath}")
    except Exception as e:
        silent_showerror(app.root, "Помилка", f"Не вдалося експортувати DXF:\n{e}")

def export_kml(app):
    if not app.geo_ref:
        silent_showwarning(app.root, "Помилка", "Проект не має гео-прив'язки!")
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
        silent_showinfo(app.root, "Експорт", f"KML збережено:\n{filepath}")
    except Exception as e: silent_showerror(app.root, "Помилка", f"Не вдалося експортувати KML:\n{e}")

def export_elevation_grid_kml(app):
    rings = [b["ring"] for b in getattr(app, "field_blocks", []) or []]
    if app.points and len(app.points) >= 3:
        rings.append(list(app.points))
    if not app.geo_ref or not rings:
        silent_showwarning(app.root, "Помилка", "Потрібен хоча б один замкнений контур поля з гео-прив'язкою!")
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
        silent_showinfo(app.root, "Експорт", f"Сітка (точок: {len(grid_points)}) збережена у KML!")
    except Exception as e: silent_showerror(app.root, "Помилка", f"Не вдалося створити сітку:\n{e}")

def export_pdf(app):
    if not hasattr(app, "last_report") or not app.last_report:
        silent_showwarning(app.root, "Увага", "Спочатку виконайте розрахунок для генерації звіту!")
        return
        
    try:
        from fpdf import FPDF
        from PIL import ImageGrab
    except ImportError:
        silent_showerror(app.root, "Помилка", "Не встановлено бібліотеку fpdf2 або Pillow.")
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
        silent_showwarning(app.root, "Увага", f"Не вдалося зробити скріншот креслення:\n{e}")
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
        silent_showinfo(app.root, "Експорт", f"PDF звіт успішно збережено:\n{filepath}")
        _trim_orchestrator_after_persist(app)
    except Exception as e: 
        silent_showerror(app.root, "Помилка", f"Не вдалося експортувати PDF:\n{e}")
    finally:
        if os.path.exists(temp_img_path):
            try: os.remove(temp_img_path)
            except: pass