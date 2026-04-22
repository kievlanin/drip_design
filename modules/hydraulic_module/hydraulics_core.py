import copy
import math
import json
import os
import re
from shapely.geometry import LineString, Point, MultiLineString
from shapely.ops import substring, nearest_points

from main_app.paths import PIPES_DB_PATH

from . import lateral_solver as lat_sol
from . import trickle_line_nr_solver as trickle_nr
from .dripperline_catalog import load_dripperlines_catalog
from .hydraulics_constants import DEFAULT_HAZEN_WILLIAMS_C, hazen_c_from_pipe_entry


def _hw_c_for_zone(zone: dict) -> float:
    try:
        v = float(zone.get("c_hw", DEFAULT_HAZEN_WILLIAMS_C))
        return max(1.0, v)
    except (TypeError, ValueError):
        return DEFAULT_HAZEN_WILLIAMS_C


def normalize_allowed_pipes_map(ap: dict) -> dict:
    """
    Узгоджує mat / PN / зовнішній Ø з pipes_db після JSON.
    Усі ключі та значення Ø — рядки (числа в списках теж стають рядками), щоб
    «50» з бази збігалося з 50 у файлі проєкту.
    """
    out: dict = {}
    if not isinstance(ap, dict):
        return out
    for mat, pns in ap.items():
        if not isinstance(pns, dict):
            continue
        mkey = str(mat).strip()
        if not mkey:
            continue
        sub: dict = {}
        for pn, ods in pns.items():
            if not isinstance(ods, list):
                continue
            pk = str(pn).strip()
            olist = [str(o).strip() for o in ods if str(o).strip()]
            sub[pk] = olist
        if sub:
            out[mkey] = sub
    return out


def _pn_sort_tuple(pn_val):
    s = str(pn_val).replace(",", ".").strip()
    try:
        return (0, float(s))
    except ValueError:
        return (1, s)


def allowed_pipe_candidates_sorted(eff_allowed: dict, pipes_db: dict) -> list:
    """
    Плоский список позицій каталогу, що входять у робочий набір:
    лише ті (матеріал, PN, зовн. Ø), які явно дозволені в eff_allowed і є в pipes_db.
    Сортування: за внутрішнім діаметром, далі матеріал / PN (для стабільності).
    """
    out: list = []
    eff = normalize_allowed_pipes_map(eff_allowed) or {}
    for mat, pns in eff.items():
        mat_db = pipes_db.get(mat)
        if not isinstance(mat_db, dict):
            continue
        if not isinstance(pns, dict):
            continue
        for pn, ods in pns.items():
            if not isinstance(ods, list) or not ods:
                continue
            avail = mat_db.get(str(pn), {})
            if not avail:
                continue
            allowed_set = {str(o).strip() for o in ods if str(o).strip()}
            for d_nom, pipe_data in avail.items():
                if str(d_nom).strip() not in allowed_set:
                    continue
                d_inner = float(
                    pipe_data.get("id", float(d_nom))
                    if isinstance(pipe_data, dict)
                    else float(d_nom)
                )
                color = (
                    pipe_data.get("color", "#FFFFFF")
                    if isinstance(pipe_data, dict)
                    else "#FFFFFF"
                )
                out.append(
                    {
                        "mat": str(mat),
                        "pn": str(pn),
                        "d": int(float(d_nom)),
                        "inner": d_inner,
                        "color": color,
                        "c_hw": hazen_c_from_pipe_entry(pipe_data),
                    }
                )
    out.sort(
        key=lambda c: (c["inner"], c["mat"], _pn_sort_tuple(c["pn"]), c["d"])
    )
    return out


def pick_smallest_allowed_pipe_for_inner_req(candidates: list, req_d_inner: float):
    """
    Найменша дозволена труба з inner ≥ req; якщо такої немає — позиція з максимальним inner
    (лише в межах робочого набору, без виходу за allowed).
    """
    if not candidates:
        return None
    for c in candidates:
        if c["inner"] + 1e-9 >= req_d_inner:
            return {
                "mat": c["mat"],
                "pn": c["pn"],
                "d": c["d"],
                "inner": c["inner"],
                "color": c["color"],
                "c_hw": c["c_hw"],
            }
    c = max(candidates, key=lambda x: x["inner"])
    return {
        "mat": c["mat"],
        "pn": c["pn"],
        "d": c["d"],
        "inner": c["inner"],
        "color": c["color"],
        "c_hw": c["c_hw"],
    }


def _head_on_profile_at_s(profile, s_along: float, default: float = 10.0) -> float:
    if not profile:
        return default
    pairs = []
    for r in profile:
        try:
            pairs.append((float(r["s"]), float(r["h"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not pairs:
        return default
    pairs.sort(key=lambda x: x[0])
    if s_along <= pairs[0][0]:
        return pairs[0][1]
    if s_along >= pairs[-1][0]:
        return pairs[-1][1]
    for i in range(len(pairs) - 1):
        s0, h0 = pairs[i]
        s1, h1 = pairs[i + 1]
        if s0 <= s_along <= s1:
            if abs(s1 - s0) < 1e-12:
                return 0.5 * (h0 + h1)
            t = (s_along - s0) / (s1 - s0)
            return h0 + t * (h1 - h0)
    return pairs[-1][1]


def _pick_adjacent_nominal(mat_str, pn_str, d_nom, smaller: bool, pipes_db: dict, allowed_pipes: dict):
    avail = pipes_db.get(mat_str, {}).get(str(pn_str), {})
    if not avail:
        return None
    allowed = allowed_pipes.get(mat_str, {}).get(str(pn_str), list(avail.keys()))
    allowed_set = {str(x) for x in allowed}
    filt = {d: pd for d, pd in avail.items() if str(d) in allowed_set}
    if not filt:
        return None
    sorted_diams = sorted(filt.items(), key=lambda item: float(item[0]))
    keys = [int(float(k)) for k, _ in sorted_diams]
    d_int = int(d_nom)
    try:
        idx = keys.index(d_int)
    except ValueError:
        idx = min(range(len(keys)), key=lambda i: abs(keys[i] - d_int))
    j = idx - 1 if smaller else idx + 1
    if j < 0 or j >= len(sorted_diams):
        return None
    d_key, pipe_data = sorted_diams[j]
    d_inner = pipe_data.get("id", float(d_key)) if isinstance(pipe_data, dict) else float(d_key)
    color = pipe_data.get("color", "#FFFFFF") if isinstance(pipe_data, dict) else "#FFFFFF"
    return int(float(d_key)), float(d_inner), color, hazen_c_from_pipe_entry(pipe_data)


def _submain_polyline_length_m(sm_coords: list) -> float:
    if not sm_coords or len(sm_coords) < 2:
        return 0.0
    t = 0.0
    for i in range(len(sm_coords) - 1):
        t += math.hypot(
            sm_coords[i + 1][0] - sm_coords[i][0], sm_coords[i + 1][1] - sm_coords[i][1]
        )
    return t


def _refresh_math_zone_q_from_loads(
    zones: list, sm_idx: int, lateral_loads_for_sm: list, L_total: float, q_nom_m3s: float
) -> None:
    n = len(zones)
    if n < 1 or L_total <= 0:
        return
    L_sec = L_total / n
    for i, z in enumerate(zones):
        z_start = i * L_sec
        z_end = L_total if i == n - 1 else (i + 1) * L_sec
        s_mid = 0.5 * (z_start + z_end)
        current_q = sum(
            L["q_m3s"]
            for L in lateral_loads_for_sm
            if L["sm_idx"] == sm_idx and L["s"] > s_mid + 1e-6
        )
        if current_q < 1e-14:
            current_q = max(1e-12, q_nom_m3s * 0.05)
        z["q"] = current_q
        inn = float(z.get("inner") or 0)
        area = math.pi * ((inn / 1000.0) / 2) ** 2
        z["v"] = current_q / area if area > 0 else 0.0


def _enforce_submain_nonwidening(math_zones: list, pipes_db: dict, allowed_pipes: dict) -> None:
    """Від крана вздовж магістралі внутрішній діаметр не збільшується (тільки поступове звуження)."""
    safety = 0
    changed = True
    while changed and safety < len(math_zones) + 8:
        safety += 1
        changed = False
        for i in range(1, len(math_zones)):
            prev_in = float(math_zones[i - 1]["inner"])
            cur_in = float(math_zones[i]["inner"])
            if cur_in <= prev_in + 1e-6:
                continue
            mat_cur = str(math_zones[i].get("mat") or "")
            pn = str(math_zones[i]["pn"])
            avail = pipes_db.get(mat_cur, {}).get(pn, {})
            allowed = allowed_pipes.get(mat_cur, {}).get(pn, list(avail.keys()))
            allowed_set = {str(x) for x in allowed}
            filt = {d: pd for d, pd in avail.items() if str(d) in allowed_set}
            best = None
            best_in = -1.0
            for d_key, pd in filt.items():
                din = float(pd.get("id", float(d_key)) if isinstance(pd, dict) else float(d_key))
                if din <= prev_in + 1e-6 and din > best_in:
                    best_in = din
                    best = (int(float(d_key)), din, pd)
            zi = math_zones[i]
            if best is not None:
                d_int, din, pd = best[0], best[1], best[2]
                zi["d"] = d_int
                zi["inner"] = din
                zi["color"] = pd.get("color", "#FFFFFF") if isinstance(pd, dict) else "#FFFFFF"
                zi["mat"] = mat_cur
                zi["c_hw"] = hazen_c_from_pipe_entry(pd)
            else:
                zp = math_zones[i - 1]
                zi["d"] = zp["d"]
                zi["inner"] = zp["inner"]
                zi["color"] = zp["color"]
                zi["pn"] = zp["pn"]
                zi["mat"] = zp.get("mat", mat_cur)
                zi["c_hw"] = float(zp.get("c_hw", DEFAULT_HAZEN_WILLIAMS_C))
            q = float(zi["q"])
            area = math.pi * ((float(zi["inner"]) / 1000.0) / 2) ** 2
            zi["v"] = q / area if area > 0 else 0.0
            changed = True


def _recompute_submain_from_zones(
    sm_idx: int,
    sm_coords: list,
    math_zones: list,
    *,
    block_i: int,
    mat_str: str,
    topo,
    lateral_loads_for_sm: list,
    q_nom_m3s: float,
    topo_dz_active: bool = True,
):
    """Перерахунок відрізків, секцій і профілю однієї магістралі з готових math_zones."""
    if not sm_coords or len(sm_coords) < 2 or not math_zones:
        return None
    LMC_dists = [0.0]
    cur_d = 0.0
    for i in range(len(sm_coords) - 1):
        cur_d += math.hypot(
            sm_coords[i + 1][0] - sm_coords[i][0], sm_coords[i + 1][1] - sm_coords[i][1]
        )
        LMC_dists.append(cur_d)
    L_total = LMC_dists[-1]
    if L_total <= 0:
        return None
    sections_count = len(math_zones)
    if sections_count < 1:
        return None
    L_sec = L_total / sections_count
    sm_line = LineString(sm_coords)
    final_cuts = []
    Math_dists = [i * L_sec for i in range(1, sections_count)]
    for md in Math_dists:
        if all(abs(md - lmc) >= 10.0 for lmc in LMC_dists):
            final_cuts.append(md)
    final_cuts.extend(LMC_dists)
    final_cuts.sort()
    cleaned_cuts = [final_cuts[0]]
    for fc in final_cuts[1:]:
        if fc - cleaned_cuts[-1] > 0.5:
            cleaned_cuts.append(fc)
    if abs(cleaned_cuts[-1] - L_total) > 0.5:
        cleaned_cuts.append(L_total)

    total_h_loss = 0.0
    sm_segs = []
    sections_out = []
    for i in range(len(cleaned_cuts) - 1):
        d_start = cleaned_cuts[i]
        d_end = cleaned_cuts[i + 1]
        seg_len = d_end - d_start
        if seg_len < 0.1:
            continue
        mid = (d_start + d_end) / 2.0
        zone = math_zones[-1]
        for z in math_zones:
            if z["start"] - 0.1 <= mid <= z["end"] + 0.1:
                zone = z
                break
        segment = substring(sm_line, d_start, d_end)
        dz = 0.0
        if topo and topo_dz_active:
            pt_start, pt_end = Point(segment.coords[0]), Point(segment.coords[-1])
            dz = topo.get_z(pt_end.x, pt_end.y) - topo.get_z(pt_start.x, pt_start.y)
        hf = 0.0
        if zone["q"] > 0 and zone["inner"] > 0:
            chw = _hw_c_for_zone(zone)
            hf = (
                10.67
                * (zone["q"] ** 1.852)
                / (chw**1.852 * (zone["inner"] / 1000.0) ** 4.87)
                * seg_len
            )
        total_h_loss += hf + dz
        sm_segs.append(
            {"d_start": d_start, "d_end": d_end, "seg_len": seg_len, "zone": zone, "hf": hf, "dz": dz}
        )
        sec_i = len(sections_out)
        sections_out.append(
            {
                "coords": list(segment.coords),
                "d": zone["d"],
                "L": seg_len,
                "mat": str(zone.get("mat") or mat_str),
                "pn": zone["pn"],
                "color": zone["color"],
                "block_idx": block_i,
                "section_index": sec_i,
                "sm_idx": sm_idx,
            }
        )

    h_required = 10.0 + total_h_loss
    q_sum_m3s = sum(L["q_m3s"] for L in lateral_loads_for_sm if L["sm_idx"] == sm_idx)
    submain_q_m3h = q_sum_m3s * 3600 if q_sum_m3s > 1e-14 else q_nom_m3s * 3600

    profile = []
    H_run = float(h_required)
    for seg in sm_segs:
        d_start = seg["d_start"]
        p = sm_line.interpolate(d_start)
        z0 = topo.get_z(p.x, p.y) if topo else 0.0
        q_m3h = seg["zone"]["q"] * 3600
        profile.append(
            {
                "s": round(d_start, 3),
                "z": round(z0, 3),
                "h": round(H_run, 4),
                "q_m3h": round(q_m3h, 2),
            }
        )
        H_run -= seg["hf"] + seg["dz"]
    if sm_segs:
        d_end = sm_segs[-1]["d_end"]
        p = sm_line.interpolate(d_end)
        z1 = topo.get_z(p.x, p.y) if topo else 0.0
        profile.append(
            {"s": round(d_end, 3), "z": round(z1, 3), "h": round(H_run, 4), "q_m3h": 0.0}
        )
    elif L_total > 0:
        p0 = sm_line.interpolate(0)
        p1 = sm_line.interpolate(L_total)
        z0 = topo.get_z(p0.x, p0.y) if topo else 0.0
        z1 = topo.get_z(p1.x, p1.y) if topo else 0.0
        q0 = submain_q_m3h
        profile = [
            {"s": 0.0, "z": round(z0, 3), "h": round(float(h_required), 4), "q_m3h": round(q0, 2)},
            {"s": round(L_total, 3), "z": round(z1, 3), "h": round(float(h_required), 4), "q_m3h": 0.0},
        ]

    v_key = str((round(sm_coords[0][0], 2), round(sm_coords[0][1], 2)))
    return {
        "sections": sections_out,
        "profile": profile,
        "h_required": float(h_required),
        "submain_q_m3h": float(submain_q_m3h),
        "v_key": v_key,
    }


class HydraulicEngine:
    def __init__(self):
        self.db_file = PIPES_DB_PATH
        self.pipes_db = self.load_db()
        self.dripperlines_db = load_dripperlines_catalog()

    def load_db(self):
        def _inject_dripperlines_category(db: dict) -> dict:
            try:
                dr_cat = load_dripperlines_catalog()
            except Exception:
                dr_cat = []
            if not isinstance(db, dict):
                db = {}
            mat_name = "Крапельні лінії"
            if mat_name not in db or not isinstance(db.get(mat_name), dict):
                db[mat_name] = {}
            for s in dr_cat:
                series = str(s.get("series", "")).strip()
                if not series:
                    continue
                db[mat_name].setdefault(series, {})
                for it in s.get("technical_data", []) or []:
                    try:
                        od = float(it.get("outside_diameter_mm", 0.0))
                    except (TypeError, ValueError):
                        continue
                    if od <= 0:
                        continue
                    try:
                        id_mm = float(it.get("inside_diameter_mm", od))
                    except (TypeError, ValueError):
                        id_mm = od
                    od_key = f"{od:.2f}".rstrip("0").rstrip(".")
                    db[mat_name][series][od_key] = {
                        "id": id_mm,
                        "length": 100.0,
                        "color": "#00AA88",
                        "price": 0.0,
                        "manufacturer": "Netafim",
                        "model": str(it.get("model", "")).strip(),
                        "kd": float(it.get("kd", 1.0) or 1.0),
                        "max_working_pressure_bar": it.get("max_working_pressure_bar"),
                        "max_flushing_pressure_bar": it.get("max_flushing_pressure_bar"),
                        "wall_thickness_mm": it.get("wall_thickness_mm"),
                    }
            return db

        def _normalize_pipe_catalog(db: dict) -> dict:
            if not isinstance(db, dict):
                return {"PVC": {}, "PE": {}, "Layflat": {}}
            for _mat, _pns in list(db.items()):
                if not isinstance(_pns, dict):
                    db[_mat] = {}
                    continue
                for _pn, _ods in list(_pns.items()):
                    if not isinstance(_ods, dict):
                        _pns[_pn] = {}
                        continue
                    for _od, _pay in list(_ods.items()):
                        if isinstance(_pay, dict):
                            try:
                                _id = float(_pay.get("id", float(_od)))
                            except (TypeError, ValueError):
                                _id = float(_od)
                            try:
                                _ln = float(_pay.get("length", 6.0))
                            except (TypeError, ValueError):
                                _ln = 6.0
                            _cl = str(_pay.get("color", "#FFFFFF") or "#FFFFFF")
                            try:
                                _pr = float(_pay.get("price", 0.0))
                            except (TypeError, ValueError):
                                _pr = 0.0
                            _ods[_od] = {
                                "id": _id,
                                "length": _ln,
                                "color": _cl,
                                "price": max(0.0, _pr),
                            }
                        else:
                            try:
                                _id = float(_pay)
                            except (TypeError, ValueError):
                                _id = float(_od)
                            _ods[_od] = {
                                "id": _id,
                                "length": 6.0,
                                "color": "#FFFFFF",
                                "price": 0.0,
                            }
            for _mk in ("PVC", "PE", "Layflat"):
                if _mk not in db or not isinstance(db[_mk], dict):
                    db[_mk] = {}
            return db

        if self.db_file.exists():
            try:
                with open(self.db_file, "r", encoding="utf-8") as f:
                    loaded_db = json.load(f)
                    if "PVC" in loaded_db and "PE" in loaded_db and "Layflat" in loaded_db:
                        return _inject_dripperlines_category(_normalize_pipe_catalog(loaded_db))
            except: pass
        
        default_db = {"PVC": {}, "PE": {}, "Layflat": {}}
        
        pns_pe_pvc = ["4", "6", "8", "10", "12.5", "16"]
        for pn in pns_pe_pvc:
            default_db["PVC"][pn] = {
                "50": {"id": 46.4, "length": 6.0, "color": "#0066FF"},
                "63": {"id": 59.0, "length": 6.0, "color": "#33CC33"},
                "75": {"id": 70.6, "length": 6.0, "color": "#660099"},
                "90": {"id": 84.6, "length": 6.0, "color": "#556B2F"},
                "110": {"id": 103.6, "length": 6.0, "color": "#FF3366"},
                "160": {"id": 150.6, "length": 6.0, "color": "#4682B4"},
                "200": {"id": 188.6, "length": 6.0, "color": "#8A2BE2"},
                "225": {"id": 212.0, "length": 6.0, "color": "#FF8C00"}
            }
            default_db["PE"][pn] = {
                "32": {"id": 29.6, "length": 100.0, "color": "#AAAAAA"},
                "40": {"id": 37.0, "length": 100.0, "color": "#CC7722"},
                "50": {"id": 46.2, "length": 100.0, "color": "#0066FF"},
                "63": {"id": 58.2, "length": 100.0, "color": "#33CC33"},
                "75": {"id": 69.2, "length": 100.0, "color": "#660099"},
                "90": {"id": 83.0, "length": 100.0, "color": "#556B2F"},
                "110": {"id": 101.4, "length": 100.0, "color": "#FF3366"},
                "160": {"id": 147.6, "length": 12.0, "color": "#4682B4"},
                "200": {"id": 184.6, "length": 12.0, "color": "#8A2BE2"},
                "225": {"id": 207.0, "length": 12.0, "color": "#FF8C00"}
            }
            
        for pn in ["3", "4", "6"]:
            default_db["Layflat"][pn] = {
                "50": {"id": 51.5, "length": 100.0, "color": "#0066FF"},
                "65": {"id": 66.0, "length": 100.0, "color": "#33CC33"},
                "75": {"id": 77.0, "length": 100.0, "color": "#660099"},
                "100": {"id": 103.0, "length": 100.0, "color": "#556B2F"},
                "150": {"id": 154.0, "length": 100.0, "color": "#4682B4"},
                "200": {"id": 205.0, "length": 100.0, "color": "#D2691E"}
            }
        
        try:
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(default_db, f, indent=4)
        except: pass
            
        return _inject_dripperlines_category(_normalize_pipe_catalog(default_db))

    def parse_pn(self, pn_val):
        match = re.search(r'\d+(\.\d+)?', str(pn_val))
        return float(match.group()) if match else 0.0

    def calculate_network(self, data):
        # ДИНАМІЧНА БАЗА: Беремо базу труб з даних проекту, а не глобальну!
        pipes_db = data.get("pipes_db", self.pipes_db)
        
        e_step = data.get("e_step", 0.3)
        e_flow = data.get("e_flow", 1.05)
        v_max = data.get("v_max", 1.5)
        v_min = data.get("v_min", 0.5)
        submain_topo_in_headloss = bool(data.get("submain_topo_in_headloss", True))
        try:
            valve_h_max_m = float(str(data.get("valve_h_max_m", 0.0)).replace(",", "."))
        except Exception:
            valve_h_max_m = 0.0
        valve_h_max_m = max(0.0, valve_h_max_m)
        num_sec_req = data.get("num_sec", 3)
        fixed_sec = data.get("fixed_sec", True)
        # Для режиму без фіксованої кількості: не більше 5 секцій, кожна ≥ 6 м (якщо довжина магістралі дозволяє)
        auto_sec_max = int(data.get("auto_sec_max", 5))
        auto_sec_min_len = float(data.get("auto_sec_min_len", 6.0))
        mat_str = str(data.get("mat_str", "PVC"))
        all_lats = data.get("all_lats", [])
        submain_lines = data.get("submain_lines", [])
        submain_block_idx = data.get("submain_block_idx") or []
        submain_section_lengths_by_sm = data.get("submain_section_lengths_by_sm") or []
        allowed_pipes = normalize_allowed_pipes_map(data.get("allowed_pipes") or {})
        _apb_raw = data.get("allowed_pipes_blocks")
        if not isinstance(_apb_raw, list):
            allowed_pipes_blocks = []
        else:
            allowed_pipes_blocks = []
            for _x in _apb_raw:
                if isinstance(_x, dict):
                    _nx = normalize_allowed_pipes_map(_x)
                    allowed_pipes_blocks.append(_nx if _nx else None)
                else:
                    allowed_pipes_blocks.append(None)

        def _allowed_for_block_idx(block_i: int) -> dict:
            try:
                bi = int(block_i)
            except (TypeError, ValueError):
                bi = 0
            if 0 <= bi < len(allowed_pipes_blocks):
                ov = allowed_pipes_blocks[bi]
                if isinstance(ov, dict) and ov:
                    return ov
            return allowed_pipes

        topo = data.get("topo", None)

        lat_mode = str(data.get("lateral_solver_mode") or "bisection").strip().lower()
        if lat_mode not in ("compare", "bisection", "newton", "trickle_nr"):
            lat_mode = "bisection"
        lateral_collect_exact = bool(data.get("lateral_collect_exact", False))
        try:
            emitter_h_min = float(str(data.get("emitter_h_min_m", 1.0)).replace(",", "."))
        except Exception:
            emitter_h_min = 1.0
        try:
            emitter_k_coeff = float(str(data.get("emitter_k_coeff", 0.0)).replace(",", "."))
        except Exception:
            emitter_k_coeff = 0.0
        try:
            emitter_x_exp = float(str(data.get("emitter_x_exp", 0.0)).replace(",", "."))
        except Exception:
            emitter_x_exp = 0.0
        try:
            emitter_kd_coeff = float(str(data.get("emitter_kd_coeff", 1.0)).replace(",", "."))
        except Exception:
            emitter_kd_coeff = 1.0
        emitter_opts = {
            "compensated": bool(data.get("emitter_compensated", False)),
            "h_min_m": max(0.05, emitter_h_min),
            "k_coeff": emitter_k_coeff if emitter_k_coeff > 1e-12 else None,
            "x_exp": emitter_x_exp if emitter_x_exp > 1e-12 else None,
            "kd_coeff": emitter_kd_coeff if emitter_kd_coeff > 1e-12 else 1.0,
        }
        # Історичний параметр H_ref більше не використовується у фізичній моделі:
        # некомпенсовані емітери рахуються через k*x (q = k * H^x).
        h_ref_m = 10.0
        try:
            lateral_inner_d_mm = float(str(data.get("lateral_inner_d_mm", 13.6)).replace(",", "."))
        except Exception:
            lateral_inner_d_mm = 13.6
        lateral_inner_d_m = max(1e-6, lateral_inner_d_mm / 1000.0)
        try:
            submain_lateral_snap_m = float(
                str(
                    data.get("submain_lateral_snap_m", lat_sol.SUBMAIN_LATERAL_SNAP_M)
                ).replace(",", ".")
            )
        except Exception:
            submain_lateral_snap_m = float(lat_sol.SUBMAIN_LATERAL_SNAP_M)
        submain_lateral_snap_m = max(0.05, min(50.0, submain_lateral_snap_m))
        try:
            emitter_h_press_min = float(
                str(data.get("emitter_h_press_min_m", 0.0)).replace(",", ".")
            )
        except Exception:
            emitter_h_press_min = 0.0
        h_press_min_m = max(0.0, emitter_h_press_min)
        try:
            emitter_h_press_max = float(
                str(data.get("emitter_h_press_max_m", 0.0)).replace(",", ".")
            )
        except Exception:
            emitter_h_press_max = 0.0
        h_press_max_m = max(0.0, emitter_h_press_max)
        if h_press_min_m > 1e-9 and h_press_max_m > 1e-9 and h_press_max_m < h_press_min_m:
            h_press_min_m, h_press_max_m = h_press_max_m, h_press_min_m

        lateral_block_idx = data.get("lateral_block_idx") or []
        if not isinstance(lateral_block_idx, list) or len(lateral_block_idx) != len(all_lats):
            lateral_block_idx = [0] * len(all_lats)

        n_lat_all = len(all_lats)
        _rs = data.get("e_steps")
        _rf = data.get("e_flows")
        per_e_steps = None
        per_e_flows = None
        if (
            isinstance(_rs, list)
            and isinstance(_rf, list)
            and len(_rs) == n_lat_all
            and len(_rf) == n_lat_all
        ):
            per_e_steps = []
            per_e_flows = []
            for i in range(n_lat_all):
                try:
                    per_e_steps.append(
                        max(1e-9, float(str(_rs[i]).replace(",", ".")))
                    )
                except (TypeError, ValueError):
                    try:
                        per_e_steps.append(max(1e-9, float(e_step)))
                    except (TypeError, ValueError):
                        per_e_steps.append(0.3)
                try:
                    per_e_flows.append(float(str(_rf[i]).replace(",", ".")))
                except (TypeError, ValueError):
                    try:
                        per_e_flows.append(float(e_flow))
                    except (TypeError, ValueError):
                        per_e_flows.append(1.05)

        _ld_mm_list = data.get("lateral_inner_d_mm_list")
        per_d_in_m = None
        if (
            isinstance(_ld_mm_list, list)
            and len(_ld_mm_list) == n_lat_all
            and n_lat_all > 0
        ):
            per_d_in_m = []
            for i in range(n_lat_all):
                try:
                    dmm = float(str(_ld_mm_list[i]).replace(",", "."))
                except (TypeError, ValueError):
                    dmm = lateral_inner_d_mm
                dmm = max(0.5, min(200.0, dmm))
                per_d_in_m.append(max(1e-6, dmm / 1000.0))

        total_drip_len = sum([lat.length for lat in all_lats])
        if per_e_steps is not None:
            total_q_m3h = 0.0
            for lat_i, es, ef in zip(all_lats, per_e_steps, per_e_flows):
                if es > 1e-12:
                    total_q_m3h += (lat_i.length / es * ef) / 1000.0
        else:
            total_q_m3h = (
                (total_drip_len / e_step if e_step > 0 else 0) * e_flow
            ) / 1000.0
        total_q_m3s = total_q_m3h / 3600.0

        calc_results = {"sections": [], "valves": {}, "emitters": {}, "submain_profiles": {}}
        report_lines = []
        report_lines.append("=== ГІДРАВЛІЧНИЙ РОЗРАХУНОК ===")
        report_lines.append(
            "Сабмейн: діаметри з робочого набору — усі відмічені матеріали, PN та Ø (лише перетин із pipes_db)."
        )
        report_lines.append(
            f"Hazen–Williams: C = {DEFAULT_HAZEN_WILLIAMS_C} за замовчуванням; у pipes_db можна задати c_hw на трубу."
        )
        if emitter_opts["compensated"]:
            report_lines.append(
                f"Емітери: компенсовані — поле «Q» = номінал л/год при H ≥ {emitter_opts['h_min_m']:.2f} м вод. ст.; нижче — пропорційне зниження."
            )
        else:
            if emitter_opts.get("k_coeff") is not None and emitter_opts.get("x_exp") is not None:
                report_lines.append(
                    f"Емітери: некомпенсовані — закон q = k·H^x (k={float(emitter_opts['k_coeff']):.5g}, x={float(emitter_opts['x_exp']):.4g})."
                )
            else:
                report_lines.append(
                    "Емітери: некомпенсовані — закон q = k·H^x; k/x не задані явно, застосовано сумісний fallback (x=0.5, k з Qном@10м)."
                )
        if h_press_min_m > 1e-9 or h_press_max_m > 1e-9:
            lo_s = f"{h_press_min_m:.2f}" if h_press_min_m > 1e-9 else "—"
            hi_s = f"{h_press_max_m:.2f}" if h_press_max_m > 1e-9 else "—"
            report_lines.append(
                f"Цільовий діапазон тиску на крапельниці (м вод. ст.): [{lo_s} … {hi_s}]"
            )
        if not submain_topo_in_headloss:
            report_lines.append(
                "〔Сабмейн〕 ΔZ рельєфу не додається до втрат напору (лише тертя hf); "
                "тиск на крані відповідає «плоскому» трубопроводу в плані поля."
            )
        report_lines.append(f"Загальна витрата (номінальна зведена): {total_q_m3h:.2f} м³/год\n")

        if not submain_lines:
            report_lines.append("Не знайдено магістралей для розрахунку.")
            return "\n".join(report_lines), calc_results

        n_sm = len(submain_lines)
        _blocks_used = set()
        for _smi in range(n_sm):
            _bi = int(submain_block_idx[_smi]) if _smi < len(submain_block_idx) else 0
            _blocks_used.add(_bi)
        for _bi in sorted(_blocks_used):
            _eff = _allowed_for_block_idx(_bi)
            if not allowed_pipe_candidates_sorted(_eff, pipes_db):
                return (
                    f"ПОМИЛКА: для блоку {_bi + 1} немає жодної дозволеної труби в робочому наборі "
                    f"(відмітьте рядки в таблиці вибору труб і перевірте pipes_db).",
                    calc_results,
                )
        q_nom_m3s = total_q_m3s / n_sm
        lateral_loads_for_sm: list = []

        progress_cb = data.get("progress")
        n_lat = len(all_lats)
        # 2 проходи сабмейну (по n_sm) + збір латералів (n_lat) + фінальний профіль (2×n_lat) + аудит по n_lat.
        total_prog_steps = 2 * n_sm + (3 * n_lat if n_lat > 0 else 0) + max(0, n_lat)
        _band_for_submain_refine = (h_press_min_m > 1e-9) or (h_press_max_m > 1e-9)
        if bool(data.get("submain_head_refine", True)) and _band_for_submain_refine and n_lat > 0:
            total_prog_steps += 10
        if (
            bool(data.get("valve_h_max_optimize", True))
            and valve_h_max_m > 1e-9
            and n_sm > 0
        ):
            total_prog_steps += 100
        # _resync_submains_full() щоразу викликає iterate_laterals(..., True) → по n_lat викликів _tap;
        # без цього prog_done швидко перевищує total і смуга «впертається» у 100 %.
        _resync_collect_passes = 0
        if bool(data.get("submain_head_refine", True)) and _band_for_submain_refine and n_lat > 0:
            _resync_collect_passes += 1
        if (
            bool(data.get("valve_h_max_optimize", True))
            and valve_h_max_m > 1e-9
            and n_sm > 0
        ):
            _resync_collect_passes += 100
        total_prog_steps += n_lat * _resync_collect_passes
        if total_prog_steps < 1:
            total_prog_steps = 1
        prog_done = [0]
        # Підбір H_врізки (бісекція з повторним HW) — експоненційно важкий при сотнях латералів.
        _AUDIT_BISECT_MAX_LAT = 36

        def _tap(msg: str) -> None:
            prog_done[0] += 1
            if callable(progress_cb):
                try:
                    progress_cb(prog_done[0], total_prog_steps, msg)
                except Exception:
                    pass

        max_dH_tip = 0.0
        max_dQ_m3s = 0.0
        max_dQ_rel = 0.0
        wing_solves = 0
        sum_it_bi = 0
        sum_it_nr = 0
        sum_it_trickle = 0

        vs_geom = [coords for coords in submain_lines if len(coords) > 1]
        sm_multi_geom = MultiLineString(vs_geom) if vs_geom else None
        lat_geom = []
        for lat in all_lats:
            conn_dist = 0.0
            if sm_multi_geom:
                inter = lat.intersection(sm_multi_geom)
                if not inter.is_empty:
                    if inter.geom_type == "Point":
                        conn_dist = lat.project(inter)
                    elif inter.geom_type == "LineString":
                        conn_dist = lat.project(inter.interpolate(0.5, normalized=True))
                    elif hasattr(inter, "geoms") and len(inter.geoms) > 0:
                        g0 = inter.geoms[0]
                        if g0.geom_type == "Point":
                            conn_dist = lat.project(g0)
                        elif g0.geom_type == "LineString":
                            conn_dist = lat.project(g0.interpolate(0.5, normalized=True))
                else:
                    pt_lat, pt_sm = nearest_points(lat, sm_multi_geom)
                    if pt_lat.distance(pt_sm) < submain_lateral_snap_m:
                        conn_dist = lat.project(pt_lat)
            pt_conn_geom = lat.interpolate(conn_dist)
            cx, cy = float(pt_conn_geom.x), float(pt_conn_geom.y)
            sm_i, s_along = lat_sol.nearest_submain_chainage_any(cx, cy, submain_lines)
            lat_geom.append(
                {
                    "lat": lat,
                    "conn_dist": conn_dist,
                    "cx": cx,
                    "cy": cy,
                    "sm_i": int(sm_i),
                    "s_along": float(s_along),
                }
            )

        def iterate_laterals(profiles_src: dict, collect_loads: bool):
            """collect_loads=True: лише накопичити lateral_loads_for_sm; інакше — зібрати emitters."""
            nonlocal max_dH_tip, max_dQ_m3s, max_dQ_rel, wing_solves, sum_it_bi, sum_it_nr, sum_it_trickle
            out_emitters = {}
            c_hw = float(DEFAULT_HAZEN_WILLIAMS_C)

            for idx, gl in enumerate(lat_geom):
                if per_d_in_m is not None:
                    d_in = per_d_in_m[idx]
                else:
                    d_in = lateral_inner_d_m
                lat = gl["lat"]
                conn_dist = gl["conn_dist"]
                cx, cy = gl["cx"], gl["cy"]
                sm_i = gl["sm_i"]
                s_along = gl["s_along"]
                if per_e_steps is not None:
                    e_step_i = per_e_steps[idx]
                    e_flow_i = per_e_flows[idx]
                else:
                    try:
                        es0 = float(e_step)
                    except (TypeError, ValueError):
                        es0 = 0.3
                    e_step_i = max(1e-9, es0)
                    try:
                        e_flow_i = float(e_flow)
                    except (TypeError, ValueError):
                        e_flow_i = 1.05
                prof_here = profiles_src.get(str(sm_i), [])
                H_sub = _head_on_profile_at_s(prof_here, s_along)
                if not prof_here:
                    H_sub = lat_sol.interpolate_head_along_submain(
                        cx, cy, submain_lines, profiles_src, max_dist=1.0e6, default_h=10.0
                    )

                def calc_wing(length: float, is_l1: bool, _es=e_step_i, _ef=e_flow_i):
                    nonlocal max_dH_tip, max_dQ_m3s, max_dQ_rel, wing_solves, sum_it_bi, sum_it_nr, sum_it_trickle
                    if length < 0.1:
                        return [], 0.0

                    z_memo: dict = {}

                    def z_at_x(x_from_conn: float) -> float:
                        if not topo:
                            return 0.0
                        da = (conn_dist - x_from_conn) if is_l1 else (conn_dist + x_from_conn)
                        da = max(0.0, min(float(lat.length), float(da)))
                        key = round(da, 3)
                        if key in z_memo:
                            return z_memo[key]
                        pt = lat.interpolate(da)
                        zv = float(topo.get_z(pt.x, pt.y))
                        z_memo[key] = zv
                        return zv

                    # Збір Q(s): за замовчуванням швидка номінальна оцінка (без бісекції); exact — стара бісекція.
                    if collect_loads:
                        if (
                            lat_mode == "trickle_nr"
                            and not bool(emitter_opts.get("compensated"))
                        ):
                            if lateral_collect_exact:
                                _p, _q_tr, _, _ = trickle_nr.solve_wing_trickle_nr(
                                    H_sub,
                                    length,
                                    _es,
                                    _ef,
                                    z_at_x,
                                    d_in,
                                    c_hw,
                                    h_ref_m=h_ref_m,
                                    emitter_opts=emitter_opts,
                                )
                                return [], float(_q_tr)
                            q_wing_m3s = lat_sol.approx_wing_q_m3s_nominal(
                                length,
                                _es,
                                _ef,
                                H_sub,
                                h_ref_m=h_ref_m,
                                emitter_opts=emitter_opts,
                            )
                            return [], float(q_wing_m3s)
                        if lateral_collect_exact:
                            aff = lat_sol.try_compensated_affine_tip(
                                H_sub,
                                length,
                                _es,
                                _ef,
                                z_at_x,
                                d_in,
                                c_hw,
                                h_ref_m=h_ref_m,
                                emitter_opts=emitter_opts,
                                tol_m=0.06,
                            )
                            if aff is not None:
                                return [], float(aff[3])
                            H_use, _ = lat_sol.solve_lateral_shooting_bisection(
                                H_sub,
                                length,
                                _es,
                                _ef,
                                z_at_x,
                                d_in,
                                c_hw,
                                h_ref_m=h_ref_m,
                                tol_m=0.06,
                                max_iter=36,
                                emitter_opts=emitter_opts,
                            )
                            _, q_wing_m3s, _ = lat_sol.backwards_step_method(
                                length,
                                _es,
                                _ef,
                                H_use,
                                z_at_x,
                                d_in,
                                c_hw,
                                h_ref_m=h_ref_m,
                                emitter_opts=emitter_opts,
                            )
                            return [], float(q_wing_m3s)
                        q_wing_m3s = lat_sol.approx_wing_q_m3s_nominal(
                            length,
                            _es,
                            _ef,
                            H_sub,
                            h_ref_m=h_ref_m,
                            emitter_opts=emitter_opts,
                        )
                        return [], float(q_wing_m3s)

                    wing_solves += 1
                    it_bi = it_nr = 0
                    H_use = float(H_sub)
                    nodes_affine = None
                    q_affine = None

                    if (
                        lat_mode == "trickle_nr"
                        and not bool(emitter_opts.get("compensated"))
                    ):
                        prof_tr, q_wing_m3s, it_tr, _ = trickle_nr.solve_wing_trickle_nr(
                            H_sub,
                            length,
                            _es,
                            _ef,
                            z_at_x,
                            d_in,
                            c_hw,
                            h_ref_m=h_ref_m,
                            emitter_opts=emitter_opts,
                        )
                        sum_it_trickle += it_tr
                        return prof_tr, q_wing_m3s

                    if lat_mode == "compare":
                        H_bi, it_bi = lat_sol.solve_lateral_shooting_bisection(
                            H_sub,
                            length,
                            _es,
                            _ef,
                            z_at_x,
                            d_in,
                            c_hw,
                            h_ref_m=h_ref_m,
                            emitter_opts=emitter_opts,
                        )
                        H_nr, it_nr = lat_sol.solve_lateral_newton_raphson(
                            H_sub,
                            length,
                            _es,
                            _ef,
                            z_at_x,
                            d_in,
                            c_hw,
                            h_ref_m=h_ref_m,
                            emitter_opts=emitter_opts,
                        )
                        h0_bi, Q_bi, _ = lat_sol.backwards_step_method(
                            length,
                            _es,
                            _ef,
                            H_bi,
                            z_at_x,
                            d_in,
                            c_hw,
                            h_ref_m=h_ref_m,
                            emitter_opts=emitter_opts,
                        )
                        h0_nr, Q_nr, _ = lat_sol.backwards_step_method(
                            length,
                            _es,
                            _ef,
                            H_nr,
                            z_at_x,
                            d_in,
                            c_hw,
                            h_ref_m=h_ref_m,
                            emitter_opts=emitter_opts,
                        )
                        max_dH_tip = max(max_dH_tip, abs(H_bi - H_nr))
                        max_dQ_m3s = max(max_dQ_m3s, abs(Q_bi - Q_nr))
                        if Q_nr > 1e-9:
                            max_dQ_rel = max(max_dQ_rel, abs(Q_bi - Q_nr) / Q_nr)
                        r_bi = abs(h0_bi - H_sub)
                        r_nr = abs(h0_nr - H_sub)
                        H_use = H_nr if r_nr <= r_bi else H_bi
                    elif lat_mode == "bisection" or lat_mode == "trickle_nr":
                        aff = lat_sol.try_compensated_affine_tip(
                            H_sub,
                            length,
                            _es,
                            _ef,
                            z_at_x,
                            d_in,
                            c_hw,
                            h_ref_m=h_ref_m,
                            emitter_opts=emitter_opts,
                            tol_m=0.06,
                        )
                        if aff is not None:
                            H_use, it_bi, nodes_affine, q_affine = aff
                        else:
                            H_use, it_bi = lat_sol.solve_lateral_shooting_bisection(
                                H_sub,
                                length,
                                _es,
                                _ef,
                                z_at_x,
                                d_in,
                                c_hw,
                                h_ref_m=h_ref_m,
                                emitter_opts=emitter_opts,
                            )
                    else:
                        aff = lat_sol.try_compensated_affine_tip(
                            H_sub,
                            length,
                            _es,
                            _ef,
                            z_at_x,
                            d_in,
                            c_hw,
                            h_ref_m=h_ref_m,
                            emitter_opts=emitter_opts,
                            tol_m=0.04,
                        )
                        if aff is not None:
                            H_use, it_nr, nodes_affine, q_affine = aff
                        else:
                            H_use, it_nr = lat_sol.solve_lateral_newton_raphson(
                                H_sub,
                                length,
                                _es,
                                _ef,
                                z_at_x,
                                d_in,
                                c_hw,
                                h_ref_m=h_ref_m,
                                emitter_opts=emitter_opts,
                            )

                    sum_it_bi += it_bi
                    sum_it_nr += it_nr

                    if nodes_affine is not None:
                        nodes_rev = nodes_affine
                        q_wing_m3s = float(q_affine)
                    else:
                        _, q_wing_m3s, nodes_rev = lat_sol.backwards_step_method(
                            length,
                            _es,
                            _ef,
                            H_use,
                            z_at_x,
                            d_in,
                            c_hw,
                            h_ref_m=h_ref_m,
                            emitter_opts=emitter_opts,
                        )
                    data = lat_sol.wing_profile_from_backwards_nodes(nodes_rev)
                    return data, q_wing_m3s

                d1, q1 = calc_wing(conn_dist, True)
                if n_lat > 0 and not collect_loads:
                    _tap("Латералі: профіль (крило 1)…")
                d2, q2 = calc_wing(max(0, lat.length - conn_dist), False)
                # Фізично в обох крилах один і той самий вузол врізки в сабмейн:
                # стартовий напір на x=0 має бути спільним (H_sub).
                # Нормалізуємо лише відображуваний профіль, не змінюючи розрахунок витрат.
                try:
                    h_conn_vis = round(float(H_sub), 2)
                except (TypeError, ValueError):
                    h_conn_vis = None
                if h_conn_vis is not None:
                    if isinstance(d1, list) and d1:
                        try:
                            d1[0]["h"] = h_conn_vis
                        except Exception:
                            pass
                    if isinstance(d2, list) and d2:
                        try:
                            d2[0]["h"] = h_conn_vis
                        except Exception:
                            pass
                if collect_loads:
                    lateral_loads_for_sm.append(
                        {"sm_idx": int(sm_i), "s": float(s_along), "q_m3s": float(q1 + q2)}
                    )
                else:
                    out_emitters[f"lat_{idx}"] = {
                        "L1": d1,
                        "L2": d2,
                        "H_submain_conn_m": round(H_sub, 3),
                    }
                if n_lat > 0:
                    _tap(
                        "Збір витрат по латералях…"
                        if collect_loads
                        else "Латералі: профіль (крило 2)…"
                    )
            return out_emitters

        def _resync_submains_full() -> None:
            """Перезібрати Q латералів і перебудувати секції/профілі/крани з math_zones."""
            lateral_loads_for_sm.clear()
            iterate_laterals(dict(calc_results.get("submain_profiles") or {}), True)
            old_sections = list(calc_results.get("sections") or [])
            old_valves = dict(calc_results.get("valves") or {})
            calc_results["valves"] = {}
            rebuilt_sections: list = []
            updated_vk = set()
            for sm_ii in range(n_sm):
                sk = str(sm_ii)
                zones_r = calc_results.get("submain_math_zones", {}).get(sk)
                sm_cr = submain_lines[sm_ii]
                lt_r = _submain_polyline_length_m(sm_cr)
                if not zones_r or lt_r <= 0:
                    rebuilt_sections.extend(
                        [s for s in old_sections if s.get("sm_idx") == sm_ii]
                    )
                    continue
                _refresh_math_zone_q_from_loads(
                    zones_r, sm_ii, lateral_loads_for_sm, lt_r, q_nom_m3s
                )
                block_ir = int(submain_block_idx[sm_ii]) if sm_ii < len(submain_block_idx) else 0
                _enforce_submain_nonwidening(
                    zones_r, pipes_db, _allowed_for_block_idx(block_ir)
                )
                out_r = _recompute_submain_from_zones(
                    sm_ii,
                    sm_cr,
                    zones_r,
                    block_i=block_ir,
                    mat_str=mat_str,
                    topo=topo,
                    lateral_loads_for_sm=lateral_loads_for_sm,
                    q_nom_m3s=q_nom_m3s,
                    topo_dz_active=submain_topo_in_headloss,
                )
                if out_r is None:
                    rebuilt_sections.extend(
                        [s for s in old_sections if s.get("sm_idx") == sm_ii]
                    )
                    continue
                rebuilt_sections.extend(out_r["sections"])
                calc_results["submain_profiles"][str(sm_ii)] = out_r["profile"]
                vk_r = out_r["v_key"]
                updated_vk.add(vk_r)
                if vk_r in calc_results["valves"]:
                    calc_results["valves"][vk_r]["H"] = max(
                        calc_results["valves"][vk_r]["H"], out_r["h_required"]
                    )
                    calc_results["valves"][vk_r]["Q"] += out_r["submain_q_m3h"]
                else:
                    calc_results["valves"][vk_r] = {
                        "H": out_r["h_required"],
                        "Q": out_r["submain_q_m3h"],
                    }
            for vk_o, vr in old_valves.items():
                if vk_o not in updated_vk:
                    calc_results["valves"][vk_o] = dict(vr)
            for k, s in enumerate(rebuilt_sections):
                s["section_index"] = k
            calc_results["sections"] = rebuilt_sections

        def one_submain_pass(use_lat_q: bool):
            """use_lat_q=False: номінальний розподіл Q; True: Q(s) з фактичних латералів."""
            if use_lat_q:
                calc_results["sections"] = []
                calc_results["submain_profiles"] = {}
                calc_results["valves"] = {}
                calc_results["submain_math_zones"] = {}
                report_lines.append("")
                report_lines.append(
                    "=== Повторний прохід: сабмейн за фактичними Q латералів (після прямого HW) ==="
                )
            for sm_idx, sm_coords in enumerate(submain_lines):
                block_i = int(submain_block_idx[sm_idx]) if sm_idx < len(submain_block_idx) else 0
                eff_allowed = _allowed_for_block_idx(block_i)
                LMC_dists = [0.0]
                cur_d = 0.0
                for i in range(len(sm_coords) - 1):
                    cur_d += math.hypot(
                        sm_coords[i + 1][0] - sm_coords[i][0],
                        sm_coords[i + 1][1] - sm_coords[i][1],
                    )
                    LMC_dists.append(cur_d)
                L_total = LMC_dists[-1]
                if L_total == 0:
                    _tap(f"Магістраль {sm_idx + 1}/{n_sm} — пропуск (нульова довжина)")
                    continue
                sm_line = LineString(sm_coords)
                report_lines.append(f"--- Магістраль {sm_idx+1} ({L_total:.1f}м) ---")
                plan_lens = []
                try:
                    raw_plan = (
                        submain_section_lengths_by_sm[sm_idx]
                        if sm_idx < len(submain_section_lengths_by_sm)
                        else []
                    )
                    if isinstance(raw_plan, list):
                        plan_lens = [float(v) for v in raw_plan if float(v) > 1e-9]
                except Exception:
                    plan_lens = []
                if plan_lens:
                    s_plan = sum(plan_lens)
                    if s_plan > 1e-9:
                        # Узгоджуємо із реальною геометрією сабмейну (дрібні похибки/округлення).
                        _k = L_total / s_plan
                        plan_lens = [max(1e-6, float(v) * _k) for v in plan_lens]
                    sections_count = len(plan_lens)
                    bounds = [0.0]
                    _acc = 0.0
                    for _l in plan_lens:
                        _acc += float(_l)
                        bounds.append(_acc)
                    bounds[-1] = float(L_total)
                    report_lines.append(
                        f"Секції: {sections_count} (план редактора), ΣLплан={sum(plan_lens):.1f} м"
                    )
                else:
                    if fixed_sec:
                        sections_count = max(1, int(num_sec_req))
                    else:
                        if L_total < auto_sec_min_len:
                            sections_count = 1
                        else:
                            n_by_len = int(L_total // auto_sec_min_len)
                            sections_count = max(1, min(auto_sec_max, n_by_len))
                    L_sec = L_total / sections_count
                    bounds = [0.0]
                    for _i in range(1, sections_count):
                        bounds.append(float(_i) * float(L_sec))
                    bounds.append(float(L_total))
                    if fixed_sec:
                        report_lines.append(
                            f"Секції: {sections_count} (фіксовано), Lсек ≈ {L_sec:.1f} м"
                        )
                    else:
                        report_lines.append(
                            f"Секції: {sections_count} (авто, до {auto_sec_max} шт, мін. {auto_sec_min_len:.0f} м), Lсек ≈ {L_sec:.1f} м"
                        )
                math_zones = []
                candidates_sm = allowed_pipe_candidates_sorted(eff_allowed, pipes_db)
                if not candidates_sm:
                    report_lines.append(
                        f"--- Магістраль {sm_idx + 1}: немає дозволених труб у наборі блоку {block_i + 1} — пропуск ---"
                    )
                    continue
                if use_lat_q:
                    for i in range(sections_count):
                        z_start = bounds[i]
                        z_end = bounds[i + 1]
                        s_mid = 0.5 * (z_start + z_end)
                        current_q = sum(
                            L["q_m3s"]
                            for L in lateral_loads_for_sm
                            if L["sm_idx"] == sm_idx and L["s"] > s_mid + 1e-6
                        )
                        if current_q < 1e-14:
                            current_q = max(1e-12, q_nom_m3s * 0.05)
                        req_area = current_q / v_max if v_max > 0 else 0
                        req_d_inner = 2 * math.sqrt(req_area / math.pi) * 1000.0 if req_area > 0 else 0
                        chosen = pick_smallest_allowed_pipe_for_inner_req(
                            candidates_sm, req_d_inner
                        )
                        chosen_d = chosen_inner = chosen_v = None
                        chosen_color, chosen_pn, chosen_mat = "#FFFFFF", "", mat_str
                        chosen_c_hw = DEFAULT_HAZEN_WILLIAMS_C
                        if chosen:
                            chosen_d = chosen["d"]
                            chosen_inner = chosen["inner"]
                            chosen_color = chosen["color"]
                            chosen_pn = chosen["pn"]
                            chosen_mat = chosen["mat"]
                            chosen_c_hw = float(chosen["c_hw"])
                            area = math.pi * ((chosen_inner / 1000.0) / 2) ** 2
                            chosen_v = current_q / area if area > 0 else 0.0
                        math_zones.append(
                            {
                                "start": z_start,
                                "end": z_end,
                                "d": chosen_d,
                                "inner": chosen_inner,
                                "color": chosen_color,
                                "pn": chosen_pn,
                                "mat": chosen_mat,
                                "c_hw": chosen_c_hw,
                                "q": current_q,
                                "v": chosen_v,
                            }
                        )
                else:
                    current_q = total_q_m3s / len(submain_lines)
                    for i in range(sections_count):
                        z_start = bounds[i]
                        z_end = bounds[i + 1]
                        req_area = current_q / v_max if v_max > 0 else 0
                        req_d_inner = 2 * math.sqrt(req_area / math.pi) * 1000.0 if req_area > 0 else 0
                        chosen = pick_smallest_allowed_pipe_for_inner_req(
                            candidates_sm, req_d_inner
                        )
                        chosen_d = chosen_inner = chosen_v = None
                        chosen_color, chosen_pn, chosen_mat = "#FFFFFF", "", mat_str
                        chosen_c_hw = DEFAULT_HAZEN_WILLIAMS_C
                        if chosen:
                            chosen_d = chosen["d"]
                            chosen_inner = chosen["inner"]
                            chosen_color = chosen["color"]
                            chosen_pn = chosen["pn"]
                            chosen_mat = chosen["mat"]
                            chosen_c_hw = float(chosen["c_hw"])
                            area = math.pi * ((chosen_inner / 1000.0) / 2) ** 2
                            chosen_v = current_q / area if area > 0 else 0.0
                        math_zones.append(
                            {
                                "start": z_start,
                                "end": z_end,
                                "d": chosen_d,
                                "inner": chosen_inner,
                                "color": chosen_color,
                                "pn": chosen_pn,
                                "mat": chosen_mat,
                                "c_hw": chosen_c_hw,
                                "q": current_q,
                                "v": chosen_v,
                            }
                        )
                        current_q -= current_q / (sections_count - i)
                if math_zones:
                    _enforce_submain_nonwidening(math_zones, pipes_db, eff_allowed)
                    if use_lat_q:
                        calc_results["submain_math_zones"][str(sm_idx)] = copy.deepcopy(math_zones)
                # Розрізи вздовж довжини: математичні межі зон + усі вершини полілінії (повороти).
                # Діаметр кожного відрізка береться з math_zones за СЕРЕДИНОЮ відрізка [d_start,d_end],
                # тому після повороту короткий прямий шматок часто потрапляє в іншу гідравлічну зону
                # (інша Q по довжині магістралі) — візуально «не продовжується» той самий d, хоча труба одна.
                final_cuts = []
                Math_dists = list(bounds[1:-1])
                for md in Math_dists:
                    if all(abs(md - lmc) >= 10.0 for lmc in LMC_dists):
                        final_cuts.append(md)
                final_cuts.extend(LMC_dists)
                final_cuts.sort()
                cleaned_cuts = [final_cuts[0]]
                for fc in final_cuts[1:]:
                    if fc - cleaned_cuts[-1] > 0.5:
                        cleaned_cuts.append(fc)
                if abs(cleaned_cuts[-1] - L_total) > 0.5:
                    cleaned_cuts.append(L_total)
                total_h_loss = 0
                sm_segs = []
                for i in range(len(cleaned_cuts) - 1):
                    d_start = cleaned_cuts[i]
                    d_end = cleaned_cuts[i + 1]
                    seg_len = d_end - d_start
                    if seg_len < 0.1:
                        continue
                    mid = (d_start + d_end) / 2.0
                    zone = math_zones[-1]
                    for z in math_zones:
                        if z["start"] - 0.1 <= mid <= z["end"] + 0.1:
                            zone = z
                            break
                    segment = substring(sm_line, d_start, d_end)
                    dz = 0.0
                    if topo and submain_topo_in_headloss:
                        pt_start, pt_end = Point(segment.coords[0]), Point(segment.coords[-1])
                        dz = topo.get_z(pt_end.x, pt_end.y) - topo.get_z(pt_start.x, pt_start.y)
                    hf = 0
                    if zone["q"] > 0 and zone["inner"] > 0:
                        _chw = _hw_c_for_zone(zone)
                        hf = (
                            10.67
                            * (zone["q"] ** 1.852)
                            / (_chw**1.852 * (zone["inner"] / 1000.0) ** 4.87)
                            * seg_len
                        )
                    total_h_loss += hf + dz
                    sm_segs.append(
                        {
                            "d_start": d_start,
                            "d_end": d_end,
                            "seg_len": seg_len,
                            "zone": zone,
                            "hf": hf,
                            "dz": dz,
                        }
                    )
                    sec_i = len(calc_results["sections"])
                    calc_results["sections"].append(
                        {
                            "coords": list(segment.coords),
                            "d": zone["d"],
                            "L": seg_len,
                            "mat": str(zone.get("mat") or mat_str),
                            "pn": zone["pn"],
                            "color": zone["color"],
                            "block_idx": block_i,
                            "section_index": sec_i,
                            "sm_idx": sm_idx,
                        }
                    )
                    q_m3h_display = zone["q"] * 3600
                    v_warning = " ⚠️(V>Vmax)" if zone["v"] > v_max else ""
                    dz_info = f" | dZ={dz:+.2f}м" if topo and abs(dz) > 0.01 else ""
                    _zm = str(zone.get("mat") or mat_str)
                    report_lines.append(
                        f"Секція {i+1}: {_zm} d{zone['d']}(PN{zone['pn']}) | L={seg_len:.1f}м | "
                        f"Q={q_m3h_display:.1f}м³/г | V={zone['v']:.2f}м/с{v_warning}{dz_info}"
                    )
                h_required = 10.0 + total_h_loss
                if use_lat_q:
                    q_sum_m3s = sum(
                        L["q_m3s"] for L in lateral_loads_for_sm if L["sm_idx"] == sm_idx
                    )
                    submain_q_m3h = q_sum_m3s * 3600 if q_sum_m3s > 1e-14 else q_nom_m3s * 3600
                else:
                    submain_q_m3h = total_q_m3h / n_sm
                profile = []
                H_run = float(h_required)
                for seg in sm_segs:
                    d_start = seg["d_start"]
                    p = sm_line.interpolate(d_start)
                    z0 = topo.get_z(p.x, p.y) if topo else 0.0
                    q_m3h = seg["zone"]["q"] * 3600
                    profile.append(
                        {
                            "s": round(d_start, 3),
                            "z": round(z0, 3),
                            "h": round(H_run, 4),
                            "q_m3h": round(q_m3h, 2),
                        }
                    )
                    H_run -= seg["hf"] + seg["dz"]
                if sm_segs:
                    d_end = sm_segs[-1]["d_end"]
                    p = sm_line.interpolate(d_end)
                    z1 = topo.get_z(p.x, p.y) if topo else 0.0
                    profile.append(
                        {
                            "s": round(d_end, 3),
                            "z": round(z1, 3),
                            "h": round(H_run, 4),
                            "q_m3h": 0.0,
                        }
                    )
                elif L_total > 0:
                    p0 = sm_line.interpolate(0)
                    p1 = sm_line.interpolate(L_total)
                    z0 = topo.get_z(p0.x, p0.y) if topo else 0.0
                    z1 = topo.get_z(p1.x, p1.y) if topo else 0.0
                    q0 = submain_q_m3h
                    profile = [
                        {"s": 0.0, "z": round(z0, 3), "h": round(float(h_required), 4), "q_m3h": round(q0, 2)},
                        {"s": round(L_total, 3), "z": round(z1, 3), "h": round(float(h_required), 4), "q_m3h": 0.0},
                    ]

                # Для проходу за фактичними навантаженнями формуємо Q(s) по вузлах відбору латералей.
                # Це дає фізично коректний ступінчастий профіль витрати (а не майже пряму).
                if use_lat_q and profile:
                    loads_sm = [
                        (float(L["s"]), float(L["q_m3s"]))
                        for L in lateral_loads_for_sm
                        if int(L.get("sm_idx", -1)) == int(sm_idx)
                    ]
                    if loads_sm:
                        loads_sm.sort(key=lambda t: t[0])
                        total_q_load_m3s = sum(q for _s, q in loads_sm)
                        # Вузли профілю: наявні точки + точки врізок латералей.
                        knot_s = {max(0.0, min(float(L_total), float(r.get("s", 0.0) or 0.0))) for r in profile}
                        for ls, _lq in loads_sm:
                            knot_s.add(max(0.0, min(float(L_total), float(ls))))
                        knot_s_sorted = sorted(knot_s)

                        prof_sorted = sorted(
                            (
                                {
                                    "s": float(r.get("s", 0.0) or 0.0),
                                    "z": float(r.get("z", 0.0) or 0.0),
                                    "h": float(r.get("h", 0.0) or 0.0),
                                }
                                for r in profile
                            ),
                            key=lambda rr: rr["s"],
                        )

                        def _interp_hz_at(sq: float) -> Tuple[float, float]:
                            if not prof_sorted:
                                return (0.0, 0.0)
                            if sq <= prof_sorted[0]["s"] + 1e-9:
                                return (prof_sorted[0]["h"], prof_sorted[0]["z"])
                            if sq >= prof_sorted[-1]["s"] - 1e-9:
                                return (prof_sorted[-1]["h"], prof_sorted[-1]["z"])
                            for j in range(len(prof_sorted) - 1):
                                a = prof_sorted[j]
                                b = prof_sorted[j + 1]
                                sa = float(a["s"])
                                sb = float(b["s"])
                                if sa - 1e-9 <= sq <= sb + 1e-9:
                                    ds = max(1e-12, sb - sa)
                                    t = max(0.0, min(1.0, (sq - sa) / ds))
                                    hq = float(a["h"]) + (float(b["h"]) - float(a["h"])) * t
                                    zq = float(a["z"]) + (float(b["z"]) - float(a["z"])) * t
                                    return (hq, zq)
                            return (prof_sorted[-1]["h"], prof_sorted[-1]["z"])

                        # Двовказівниковий кумулятив: Q_downstream(s) = total - sum(loads with s_load <= s)
                        q_prefix = 0.0
                        li = 0
                        nld = len(loads_sm)
                        profile_refined = []
                        for sq in knot_s_sorted:
                            while li < nld and loads_sm[li][0] <= sq + 1e-9:
                                q_prefix += float(loads_sm[li][1])
                                li += 1
                            q_down = max(0.0, total_q_load_m3s - q_prefix)
                            hh, zz = _interp_hz_at(float(sq))
                            profile_refined.append(
                                {
                                    "s": round(float(sq), 3),
                                    "z": round(float(zz), 3),
                                    "h": round(float(hh), 4),
                                    "q_m3h": round(float(q_down * 3600.0), 2),
                                }
                            )
                        if profile_refined:
                            profile = profile_refined
                calc_results["submain_profiles"][str(sm_idx)] = profile
                v_key = str((round(sm_coords[0][0], 2), round(sm_coords[0][1], 2)))
                if v_key in calc_results["valves"]:
                    calc_results["valves"][v_key]["H"] = max(calc_results["valves"][v_key]["H"], h_required)
                    calc_results["valves"][v_key]["Q"] += submain_q_m3h
                else:
                    calc_results["valves"][v_key] = {"H": h_required, "Q": submain_q_m3h}
                report_lines.append(f"➤ Вузол H_потр: {h_required:.1f} м\n")
                _tap(
                    f"Магістраль {sm_idx + 1}/{n_sm} — "
                    f"{'фактичні Q латералів' if use_lat_q else 'номінальний розподіл Q'}"
                )
        if callable(progress_cb):
            try:
                progress_cb(0, total_prog_steps, "Гідравліка: 1-й прохід сабмейну…")
            except Exception:
                pass

        one_submain_pass(False)
        lateral_loads_for_sm.clear()
        iterate_laterals(dict(calc_results.get("submain_profiles") or {}), True)
        if not lateral_collect_exact:
            report_lines.append(
                "〔Латералі〕 Q для 2-го сабмейну: швидка оцінка (емітери×вилив при H_врізки); фінальні крила — повний HW."
            )
        one_submain_pass(True)
        report_lines.append(
            "【Сабмейн】 Повторний прохід: діаметри/втрати за Q(s) від фактичних витрат латералів; далі латералі з оновленим профілем H."
        )

        _SUBMAIN_REFINE_MAX = 10
        _SUBMAIN_REFINE_TOL_M = 0.08
        submain_refine_applied = False
        valve_h_max_opt_iters = 0
        if (
            bool(data.get("submain_head_refine", True))
            and _band_for_submain_refine
            and n_lat > 0
            and calc_results.get("submain_math_zones")
        ):

            def _submain_taps_within_band() -> bool:
                profs = calc_results.get("submain_profiles") or {}
                for gl in lat_geom:
                    sm_i = gl["sm_i"]
                    s_along = gl["s_along"]
                    H = _head_on_profile_at_s(profs.get(str(sm_i), []), s_along)
                    if h_press_min_m > 1e-9 and H < h_press_min_m - _SUBMAIN_REFINE_TOL_M:
                        return False
                    if h_press_max_m > 1e-9 and H > h_press_max_m + _SUBMAIN_REFINE_TOL_M:
                        return False
                return True

            if not _submain_taps_within_band():
                report_lines.append(
                    "【Сабмейн】 Підгонка зон під діапазон тиску на врізці (до 10 кроків, той самий PN; з обмеженнями Vmin/Vmax)."
                )
                for step in range(_SUBMAIN_REFINE_MAX):
                    if _submain_taps_within_band():
                        report_lines.append(
                            f"【Сабмейн】 Підгонка: зупинка на кроці {step} (врізки в межах допуску ±{_SUBMAIN_REFINE_TOL_M:.2f} м)."
                        )
                        break
                    worst = None
                    profs = calc_results.get("submain_profiles") or {}
                    for gl in lat_geom:
                        sm_i = gl["sm_i"]
                        s_along = gl["s_along"]
                        H = _head_on_profile_at_s(profs.get(str(sm_i), []), s_along)
                        if h_press_max_m > 1e-9 and H > h_press_max_m + _SUBMAIN_REFINE_TOL_M:
                            err = H - h_press_max_m
                            tup = (err, sm_i, s_along, H, True)
                        elif h_press_min_m > 1e-9 and H < h_press_min_m - _SUBMAIN_REFINE_TOL_M:
                            err = h_press_min_m - H
                            tup = (err, sm_i, s_along, H, False)
                        else:
                            continue
                        if worst is None or err > worst[0]:
                            worst = tup
                    if worst is None:
                        break
                    _err, sm_i, s_along, H_act, high = worst
                    zones = calc_results["submain_math_zones"].get(str(sm_i))
                    if not zones:
                        break
                    z_k = None
                    for z in zones:
                        if z["start"] - 0.05 <= s_along <= z["end"] + 0.05:
                            z_k = z
                            break
                    if z_k is None:
                        break
                    _blk_ref = (
                        int(submain_block_idx[sm_i]) if sm_i < len(submain_block_idx) else 0
                    )
                    adj = _pick_adjacent_nominal(
                        str(z_k.get("mat") or mat_str),
                        z_k["pn"],
                        z_k["d"],
                        smaller=high,
                        pipes_db=pipes_db,
                        allowed_pipes=_allowed_for_block_idx(_blk_ref),
                    )
                    if adj is None:
                        report_lines.append(
                            f"【Сабмейн】 Підгонка: крок {step + 1} — немає сусіднього номіналу d у PN{z_k['pn']}."
                        )
                        break
                    new_d, new_inner, new_c, new_c_hw = adj
                    q = float(z_k["q"])
                    area = math.pi * ((new_inner / 1000.0) / 2) ** 2
                    new_v = q / area if area > 0 else 0.0
                    if high and new_v > v_max + 0.02:
                        report_lines.append(
                            f"【Сабмейн】 Підгонка: крок {step + 1} — обмеження Vmax, зменшення d неможливе."
                        )
                        break
                    if (not high) and new_v < v_min - 0.02:
                        report_lines.append(
                            f"【Сабмейн】 Підгонка: крок {step + 1} — обмеження Vmin, збільшення d неможливе."
                        )
                        break
                    z_k["d"] = new_d
                    z_k["inner"] = new_inner
                    z_k["color"] = new_c
                    z_k["c_hw"] = float(new_c_hw)
                    z_k["v"] = new_v
                    _enforce_submain_nonwidening(
                        zones, pipes_db, _allowed_for_block_idx(_blk_ref)
                    )
                    sm_coords = submain_lines[sm_i]
                    block_i = int(submain_block_idx[sm_i]) if sm_i < len(submain_block_idx) else 0
                    out = _recompute_submain_from_zones(
                        sm_i,
                        sm_coords,
                        zones,
                        block_i=block_i,
                        mat_str=mat_str,
                        topo=topo,
                        lateral_loads_for_sm=lateral_loads_for_sm,
                        q_nom_m3s=q_nom_m3s,
                        topo_dz_active=submain_topo_in_headloss,
                    )
                    if out is None:
                        break
                    rest = [s for s in calc_results["sections"] if s.get("sm_idx") != sm_i]
                    new_secs = out["sections"]
                    for k, s in enumerate(rest + new_secs):
                        s["section_index"] = k
                    calc_results["sections"] = rest + new_secs
                    calc_results["submain_profiles"][str(sm_i)] = out["profile"]
                    vk = out["v_key"]
                    if vk in calc_results["valves"]:
                        calc_results["valves"][vk]["H"] = max(
                            calc_results["valves"][vk]["H"], out["h_required"]
                        )
                    else:
                        calc_results["valves"][vk] = {
                            "H": out["h_required"],
                            "Q": out["submain_q_m3h"],
                        }
                    _tap(f"Підгонка сабмейну: крок {step + 1}/{_SUBMAIN_REFINE_MAX}…")
                    submain_refine_applied = True

        if submain_refine_applied:
            report_lines.append(
                "【Сабмейн】 Після підгонки — повторний збір Q латералів за оновленим профілем H і перерахунок магістралей."
            )
            _resync_submains_full()

        if (
            bool(data.get("valve_h_max_optimize", True))
            and valve_h_max_m > 1e-9
            and calc_results.get("submain_math_zones")
        ):
            report_lines.append("")
            report_lines.append(
                f"--- Підбір d сабмейну під H на крані ≤ {valve_h_max_m:.2f} м вод. ст. ---"
            )
            _VALVE_HMAX_MAX_STEPS = 100
            for _vh_step in range(_VALVE_HMAX_MAX_STEPS):
                worst_vk = None
                worst_h = 0.0
                for vk, vr in (calc_results.get("valves") or {}).items():
                    hc = float(vr.get("H", 0))
                    if hc > valve_h_max_m + 0.03 and hc > worst_h:
                        worst_h = hc
                        worst_vk = vk
                if worst_vk is None:
                    break
                progressed = False
                sms_for_vk = [
                    sj
                    for sj in range(n_sm)
                    if str(
                        (round(submain_lines[sj][0][0], 2), round(submain_lines[sj][0][1], 2))
                    )
                    == worst_vk
                ]
                for sm_ii in sorted(sms_for_vk):
                    zones = calc_results["submain_math_zones"].get(str(sm_ii))
                    if not zones:
                        continue
                    _blk_v = (
                        int(submain_block_idx[sm_ii])
                        if sm_ii < len(submain_block_idx)
                        else 0
                    )
                    _eff_v = _allowed_for_block_idx(_blk_v)
                    for zi in range(len(zones)):
                        z = zones[zi]
                        adj = _pick_adjacent_nominal(
                            str(z.get("mat") or mat_str),
                            z["pn"],
                            z["d"],
                            smaller=False,
                            pipes_db=pipes_db,
                            allowed_pipes=_eff_v,
                        )
                        if adj is None:
                            continue
                        new_d, new_inner, new_c, new_c_hw = adj
                        q = float(z["q"])
                        area = math.pi * ((new_inner / 1000.0) / 2) ** 2
                        new_v = q / area if area > 0 else 0.0
                        if new_v > v_max + 0.02:
                            continue
                        z["d"] = new_d
                        z["inner"] = new_inner
                        z["color"] = new_c
                        z["c_hw"] = float(new_c_hw)
                        z["v"] = new_v
                        _enforce_submain_nonwidening(zones, pipes_db, _eff_v)
                        _resync_submains_full()
                        valve_h_max_opt_iters += 1
                        progressed = True
                        _tap(f"Підбір під H_крана: крок {valve_h_max_opt_iters}…")
                        break
                    if progressed:
                        break
                if not progressed:
                    report_lines.append(
                        f"  Зупинка підбору: немає номіналу або V>Vmax — залишковий H на {worst_vk} ≈ {worst_h:.2f} м."
                    )
                    break
            if valve_h_max_opt_iters > 0:
                report_lines.append(f"  Виконано кроків збільшення d: {valve_h_max_opt_iters}.")

        max_dH_tip = 0.0
        max_dQ_m3s = 0.0
        max_dQ_rel = 0.0
        wing_solves = 0
        sum_it_bi = 0
        sum_it_nr = 0
        sum_it_trickle = 0
        project_emitters = iterate_laterals(dict(calc_results.get("submain_profiles") or {}), False)

        if valve_h_max_m > 1e-9:
            report_lines.append("")
            report_lines.append(
                f"--- Обмеження напору на крані (задано): H_макс = {valve_h_max_m:.2f} м вод. ст. ---"
            )
            vmax_ok = True
            for _vk, vr in list((calc_results.get("valves") or {}).items()):
                hc = float(vr.get("H", 0))
                vr["valve_h_max_m_spec"] = round(float(valve_h_max_m), 4)
                ex = hc > valve_h_max_m + 1e-4
                vr["exceeds_valve_h_max"] = bool(ex)
                if ex:
                    vmax_ok = False
                    report_lines.append(
                        f"  ⚠️ Вузол {_vk}: розрахунковий H = {hc:.2f} м > заданого макс {valve_h_max_m:.2f} м."
                    )
            calc_results["valve_h_max_m_spec"] = round(float(valve_h_max_m), 4)
            calc_results["valve_pressure_within_spec"] = bool(vmax_ok)
            if vmax_ok:
                report_lines.append("  Усі вузли кранів у межах заданого H_макс.")
        else:
            calc_results["valve_h_max_m_spec"] = None
            calc_results["valve_pressure_within_spec"] = True
            for vr in (calc_results.get("valves") or {}).values():
                vr.pop("valve_h_max_m_spec", None)
                vr.pop("exceeds_valve_h_max", None)

        if wing_solves > 0:
            ab = sum_it_bi / wing_solves
            an = sum_it_nr / wing_solves
            _lat_hdr = (
                "багатовузловий Ньютон (q∼(Δh/C)^0.54, вилив K(H−E)^x)"
                if lat_mode == "trickle_nr"
                else "прямий HW"
            )
            report_lines.append(f"--- Латералі (режим: {lat_mode}) — {_lat_hdr} ---")
            if lat_mode == "compare":
                report_lines.append(
                    f"Порівняння бісекція vs Ньютон: макс. |ΔH_тупик| = {max_dH_tip:.4f} м; "
                    f"макс. |ΔQ| = {max_dQ_m3s:.6f} м³/с ({max_dQ_m3s * 3600 * 1000:.2f} л/год); "
                    f"макс. відносна |ΔQ|/Q = {max_dQ_rel * 100:.3f}%"
                )
                report_lines.append(
                    f"Ітерації: бісекція сумарно {sum_it_bi} (сер. {ab:.1f} на крило), "
                    f"Ньютон сумарно {sum_it_nr} (сер. {an:.1f} на крило)."
                )
            elif lat_mode == "bisection":
                report_lines.append(
                    f"Лише бісекція: сумарно ітерацій {sum_it_bi}, середнє на крило {ab:.1f}."
                )
            elif lat_mode == "trickle_nr":
                at = sum_it_trickle / wing_solves if wing_solves else 0.0
                report_lines.append(
                    f"Багатовузловий Ньютон по лінії: сумарно ітерацій {sum_it_trickle}, середнє на крило {at:.1f}."
                )
                if bool(emitter_opts.get("compensated")):
                    report_lines.append(
                        "  (Компенсовані крапельниці: профіль рахується як прямий HW + бісекція.)"
                    )
                else:
                    _q_all: list = []
                    _q_by_block: dict = {}
                    for _idx, _gl in enumerate(lat_geom):
                        _pay = project_emitters.get(f"lat_{_idx}") or {}
                        try:
                            _bi0 = int(lateral_block_idx[_idx]) if _idx < len(lateral_block_idx) else 0
                        except (TypeError, ValueError, IndexError):
                            _bi0 = 0
                        _lst = _q_by_block.setdefault(_bi0, [])
                        for _wing in (_pay.get("L1") or [], _pay.get("L2") or []):
                            for _row in _wing:
                                _qe = float(_row.get("q_emit", 0))
                                if _qe > 1e-9:
                                    _q_all.append(_qe)
                                    _lst.append(_qe)
                    calc_results["block_emitter_uniformity"] = {}
                    for _bi0, _ql in sorted(_q_by_block.items(), key=lambda t: int(t[0])):
                        if len(_ql) >= 2:
                            _bm = trickle_nr.emitter_flow_uniformity_metrics(_ql)
                            calc_results["block_emitter_uniformity"][str(_bi0)] = {
                                "du_low_quarter_pct": round(_bm["du_low_quarter_pct"], 4),
                                "christiansen_cu": round(_bm["christiansen_cu"], 6),
                                "n_emitters": int(len(_ql)),
                            }
                    if len(_q_all) >= 2:
                        _um = trickle_nr.emitter_flow_uniformity_metrics(_q_all)
                        report_lines.append(
                            f"  Рівномірність виливу (усі крапельниці): DU (низькі ¼) = "
                            f"{_um['du_low_quarter_pct']:.2f} %; Christiansen CU = {_um['christiansen_cu']:.4f}."
                        )
                        calc_results["lateral_emitter_uniformity"] = {
                            "du_low_quarter_pct": round(_um["du_low_quarter_pct"], 4),
                            "christiansen_cu": round(_um["christiansen_cu"], 6),
                            "n_emitters": int(len(_q_all)),
                        }
            else:
                report_lines.append(
                    f"Лише Ньютон–Рафсон: сумарно ітерацій {sum_it_nr}, середнє на крило {an:.1f}."
                )
            report_lines.append(
                "Напір на врізці H береться з профілю сабмейну в точці з’єднання (інтерполяція по s)."
            )
        calc_results["emitters"] = project_emitters

        lateral_pressure_audit: dict = {}
        block_emit_sum: dict = {}
        block_emit_count: dict = {}

        def _topo_xy(px: float, py: float) -> float:
            if not topo:
                return 0.0
            return float(topo.get_z(float(px), float(py)))

        c_hw_audit = float(DEFAULT_HAZEN_WILLIAMS_C)
        band_active = (h_press_min_m > 1e-9) or (h_press_max_m > 1e-9)

        def _emit_h_mm(h_sub_try: float, gl: dict, lat_idx: int):
            if per_e_steps is not None:
                es_a = per_e_steps[lat_idx]
                ef_a = per_e_flows[lat_idx]
            else:
                try:
                    es_a = max(1e-9, float(e_step))
                except (TypeError, ValueError):
                    es_a = 0.3
                try:
                    ef_a = float(e_flow)
                except (TypeError, ValueError):
                    ef_a = 1.05
            d_use = per_d_in_m[lat_idx] if per_d_in_m is not None else lateral_inner_d_m
            return lat_sol.emitter_head_min_max_for_h_sub(
                gl["lat"],
                gl["conn_dist"],
                h_sub_try,
                es_a,
                ef_a,
                _topo_xy,
                d_inner_m=d_use,
                C_hw=c_hw_audit,
                h_ref_m=h_ref_m,
                emitter_opts=emitter_opts,
            )

        for idx, gl in enumerate(lat_geom):
            _tap(f"Аудит тиску: латераль {idx + 1}/{n_lat}…")
            lk = f"lat_{idx}"
            pay = project_emitters.get(lk) or {}
            L1 = pay.get("L1") or []
            L2 = pay.get("L2") or []
            hs_emit = []
            q_emit_sum = 0.0
            n_em = 0
            for wing in (L1, L2):
                for row in wing:
                    qe = float(row.get("q_emit", 0))
                    if qe > 1e-4:
                        hs_emit.append(float(row.get("h", 0)))
                        q_emit_sum += qe
                        n_em += 1
            bi = int(lateral_block_idx[idx]) if idx < len(lateral_block_idx) else 0
            if n_em:
                block_emit_sum[bi] = block_emit_sum.get(bi, 0.0) + q_emit_sum
                block_emit_count[bi] = block_emit_count.get(bi, 0) + n_em

            h_actual_sub = float(pay.get("H_submain_conn_m", 0.0))

            def _heads_from_wing(rows):
                out = []
                for row in rows:
                    qe = float(row.get("q_emit", 0))
                    if qe > 1e-4:
                        out.append(float(row.get("h", 0)))
                return out

            hs_l1 = _heads_from_wing(L1)
            hs_l2 = _heads_from_wing(L2)

            # Допуск у метрах: 1e-6 давав хибні «переливи» через шум float після HW.
            _h_band_ui_tol_m = 0.02

            def _band_status_from_heads(hs):
                if not hs:
                    return "no_emitters"
                mn_w, mx_w = min(hs), max(hs)
                st_w = "ok"
                if band_active:
                    if h_press_max_m > 1e-9 and mx_w > h_press_max_m + _h_band_ui_tol_m:
                        st_w = "overflow"
                    if h_press_min_m > 1e-9 and mn_w < h_press_min_m - _h_band_ui_tol_m:
                        st_w = "underflow" if st_w == "ok" else "both"
                return st_w

            st_l1 = _band_status_from_heads(hs_l1)
            st_l2 = _band_status_from_heads(hs_l2)

            if not hs_emit:
                lateral_pressure_audit[lk] = {
                    "block_idx": bi,
                    "min_h_emit_m": None,
                    "max_h_emit_m": None,
                    "min_h_emit_l1_m": None,
                    "max_h_emit_l1_m": None,
                    "min_h_emit_l2_m": None,
                    "max_h_emit_l2_m": None,
                    "h_sub_actual_m": round(h_actual_sub, 4),
                    "h_sub_target_m": None,
                    "status": "no_emitters",
                    "status_l1": st_l1,
                    "status_l2": st_l2,
                }
                continue

            mn = min(hs_emit)
            mx = max(hs_emit)
            st = "ok"
            if band_active:
                if h_press_max_m > 1e-9 and mx > h_press_max_m + _h_band_ui_tol_m:
                    st = "overflow"
                if h_press_min_m > 1e-9 and mn < h_press_min_m - _h_band_ui_tol_m:
                    st = "underflow" if st == "ok" else "both"

            h_sub_lo_need = None
            h_sub_hi_allow = None
            audit_bisect = (
                band_active
                and n_em
                and n_lat > 0
                and n_lat <= _AUDIT_BISECT_MAX_LAT
            )
            bisect_iters = 22 if n_lat > 20 else 32
            if audit_bisect:
                if h_press_min_m > 1e-9:
                    lo_a, hi_a = 0.12, 48.0
                    mn_hi, _mx_hi = _emit_h_mm(hi_a, gl, idx)
                    if mn_hi is not None and mn_hi >= h_press_min_m - 1e-3:
                        b_lo, b_hi = lo_a, hi_a
                        for _ in range(bisect_iters):
                            mid = 0.5 * (b_lo + b_hi)
                            mn_m, _mx = _emit_h_mm(mid, gl, idx)
                            if mn_m is None:
                                b_lo = mid
                                continue
                            if mn_m >= h_press_min_m - 5e-4:
                                b_hi = mid
                            else:
                                b_lo = mid
                        h_sub_lo_need = b_hi

                if h_press_max_m > 1e-9:
                    lo_b, hi_b = 0.12, 48.0
                    _mn_lo, mx_lo = _emit_h_mm(lo_b, gl, idx)
                    if mx_lo is not None and mx_lo <= h_press_max_m + 1e-3:
                        lo2, hi2 = lo_b, hi_b
                        for _ in range(bisect_iters):
                            mid = 0.5 * (lo2 + hi2)
                            _mn_m, mx_m = _emit_h_mm(mid, gl, idx)
                            if mx_m is None:
                                hi2 = mid
                                continue
                            if mx_m <= h_press_max_m + 5e-4:
                                lo2 = mid
                            else:
                                hi2 = mid
                        h_sub_hi_allow = lo2
                    else:
                        h_sub_hi_allow = None

            h_sub_target = None
            if band_active:
                if h_sub_lo_need is not None and h_sub_hi_allow is not None:
                    if h_sub_lo_need <= h_sub_hi_allow + 1e-3:
                        h_sub_target = 0.5 * (h_sub_lo_need + h_sub_hi_allow)
                    else:
                        h_sub_target = None
                elif h_sub_lo_need is not None:
                    h_sub_target = h_sub_lo_need
                elif h_sub_hi_allow is not None:
                    h_sub_target = h_sub_hi_allow

            lateral_pressure_audit[lk] = {
                "block_idx": bi,
                "min_h_emit_m": round(mn, 4),
                "max_h_emit_m": round(mx, 4),
                "min_h_emit_l1_m": round(min(hs_l1), 4) if hs_l1 else None,
                "max_h_emit_l1_m": round(max(hs_l1), 4) if hs_l1 else None,
                "min_h_emit_l2_m": round(min(hs_l2), 4) if hs_l2 else None,
                "max_h_emit_l2_m": round(max(hs_l2), 4) if hs_l2 else None,
                "h_sub_actual_m": round(h_actual_sub, 4),
                "h_sub_target_m": round(h_sub_target, 4) if h_sub_target is not None else None,
                "h_sub_band_low_m": round(h_sub_lo_need, 4)
                if h_sub_lo_need is not None
                else None,
                "h_sub_band_high_m": round(h_sub_hi_allow, 4)
                if h_sub_hi_allow is not None
                else None,
                "status": st,
                "status_l1": st_l1,
                "status_l2": st_l2,
            }

        calc_results["lateral_pressure_audit"] = lateral_pressure_audit
        calc_results["block_avg_emit_lph"] = {
            str(bi): round(block_emit_sum[bi] / max(1, block_emit_count[bi]), 4)
            for bi in block_emit_sum
        }

        if band_active and lateral_pressure_audit:
            report_lines.append("")
            report_lines.append("--- Латералі: тиск на крапельницях та рекомендований H біля врізки ---")
            if n_lat > _AUDIT_BISECT_MAX_LAT:
                report_lines.append(
                    f"〔Підбір H_врізки (бісекція) вимкнено: латералів {n_lat} > {_AUDIT_BISECT_MAX_LAT}. "
                    f"Показано min/max H на крапельницях і статус; рекомендований H_врізки не шукався.〕"
                )
            bad_u = sum(1 for a in lateral_pressure_audit.values() if a.get("status") == "underflow")
            bad_o = sum(1 for a in lateral_pressure_audit.values() if a.get("status") == "overflow")
            bad_b = sum(1 for a in lateral_pressure_audit.values() if a.get("status") == "both")
            if bad_u or bad_o or bad_b:
                report_lines.append(
                    f"Поза діапазоном: недолив (жовтий) — {bad_u} шт.; перелив (червоний) — {bad_o} шт.; обидва — {bad_b} шт."
                )
            for lk in sorted(lateral_pressure_audit.keys(), key=lambda x: int(x.split("_")[-1])):
                a = lateral_pressure_audit[lk]
                if a.get("status") == "no_emitters":
                    continue
                mn = a.get("min_h_emit_m")
                mx = a.get("max_h_emit_m")
                ha = a.get("h_sub_actual_m")
                ht = a.get("h_sub_target_m")
                st = a.get("status", "")
                tag = {"ok": "OK", "overflow": "ПЕРЕЛИВ", "underflow": "НЕДОЛИВ", "both": "ПЕРЕЛИВ+НЕДОЛИВ"}.get(
                    st, st
                )
                tag_w = {
                    "ok": "OK",
                    "overflow": "ПЕРЕЛИВ",
                    "underflow": "НЕДОЛИВ",
                    "both": "ПЕРЕЛИВ+НЕДОЛИВ",
                    "no_emitters": "—",
                }
                tline = f"  {lk} (блок {int(a.get('block_idx', 0)) + 1}): H_кр min…max = {mn}…{mx} м | H_врізки зараз {ha} м"
                s1, s2 = a.get("status_l1"), a.get("status_l2")
                if s1 is not None and s2 is not None and (s1 != "ok" or s2 != "ok" or s1 != s2):
                    tline += (
                        f" | крило1 (до врізки): {tag_w.get(s1, s1)}"
                        f" | крило2 (після врізки): {tag_w.get(s2, s2)}"
                    )
                if ht is not None:
                    tline += f" | рекоменд. H_врізки ≈ {ht} м"
                else:
                    if n_lat > _AUDIT_BISECT_MAX_LAT:
                        tline += " | підбір H_врізки не виконувався (багато латералів)"
                    elif st in ("overflow", "underflow", "both"):
                        tline += " | немає сумісного H_врізки в сканованому діапазоні"
                tline += f" — {tag}"
                report_lines.append(tline)
            if calc_results["block_avg_emit_lph"]:
                report_lines.append("Середній вилив по блоках (л/год на крапельницю):")
                for bk in sorted(calc_results["block_avg_emit_lph"].keys(), key=lambda x: int(x)):
                    report_lines.append(
                        f"  Блок {int(bk) + 1}: {calc_results['block_avg_emit_lph'][bk]:.3f} л/год"
                    )

        _lu_snap = calc_results.get("lateral_emitter_uniformity")
        calc_results["lateral_solver_stats"] = {
            "mode": lat_mode,
            "lateral_collect_exact": lateral_collect_exact,
            "emitter_compensated": bool(emitter_opts.get("compensated", False)),
            "emitter_h_min_m": round(float(emitter_opts.get("h_min_m", 1.0)), 4),
            "emitter_h_ref_m": round(float(h_ref_m), 4),
            "emitter_h_press_min_m": round(float(h_press_min_m), 4),
            "emitter_h_press_max_m": round(float(h_press_max_m), 4),
            "lateral_audit_bisection_max_lat": int(_AUDIT_BISECT_MAX_LAT),
            "lateral_audit_bisection_skipped": bool(
                band_active and n_lat > _AUDIT_BISECT_MAX_LAT
            ),
            "submain_recalibrated_for_lateral_q": True,
            "submain_topo_in_headloss": bool(submain_topo_in_headloss),
            "submain_refine_resync_done": bool(submain_refine_applied),
            "valve_h_max_m": round(float(valve_h_max_m), 4) if valve_h_max_m > 1e-9 else None,
            "valve_h_max_optimize": bool(data.get("valve_h_max_optimize", True)),
            "valve_h_max_optimize_steps": int(valve_h_max_opt_iters),
            "wings_solved": wing_solves,
            "sum_bisection_iters": sum_it_bi,
            "sum_newton_iters": sum_it_nr,
            "sum_trickle_nr_iters": sum_it_trickle,
            "avg_bisection_iters": round(sum_it_bi / wing_solves, 3) if wing_solves else 0.0,
            "avg_newton_iters": round(sum_it_nr / wing_solves, 3) if wing_solves else 0.0,
            "avg_trickle_nr_iters": round(sum_it_trickle / wing_solves, 3)
            if wing_solves
            else 0.0,
            "max_delta_H_tip_m": round(max_dH_tip, 6),
            "max_delta_Q_m3s": round(max_dQ_m3s, 8),
            "max_delta_Q_relative": round(max_dQ_rel, 8),
            "emitter_du_low_quarter_pct": (
                float(_lu_snap["du_low_quarter_pct"]) if _lu_snap else None
            ),
            "emitter_christiansen_cu": (
                float(_lu_snap["christiansen_cu"]) if _lu_snap else None
            ),
        }

        return "\n".join(report_lines), calc_results
