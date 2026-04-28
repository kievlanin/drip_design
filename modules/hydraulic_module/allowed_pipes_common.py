from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

from .hydraulics_constants import hazen_c_from_pipe_entry


def normalize_allowed_pipes_map_common(ap: Any) -> Dict[str, Dict[str, List[str]]]:
    """
    Normalize allowed-pipes map loaded from JSON:
    - material keys as non-empty strings
    - PN keys as strings
    - OD values as non-empty strings
    """
    out: Dict[str, Dict[str, List[str]]] = {}
    if not isinstance(ap, dict):
        return out
    for mat, pns in ap.items():
        if not isinstance(pns, dict):
            continue
        mkey = str(mat).strip()
        if not mkey:
            continue
        sub: Dict[str, List[str]] = {}
        for pn, ods in pns.items():
            if not isinstance(ods, list):
                continue
            pk = str(pn).strip()
            olist = [str(o).strip() for o in ods if str(o).strip()]
            sub[pk] = olist
        if sub:
            out[mkey] = sub
    return out


def pn_sort_tuple_common(pn_val: Any) -> Tuple[Any, Any]:
    s = str(pn_val).replace(",", ".").strip()
    try:
        return (0, float(s))
    except ValueError:
        return (1, s)


def allowed_pipe_candidates_sorted_common(
    eff_allowed: Mapping[str, Any], pipes_db: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """
    Flatten allowed catalog entries to stable sorted candidates:
    by inner diameter, material, PN, and OD.
    """
    out: List[Dict[str, Any]] = []
    eff = normalize_allowed_pipes_map_common(eff_allowed) or {}
    pdb = dict(pipes_db)
    for mat, pns in eff.items():
        mat_db = pdb.get(mat)
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
    out.sort(key=lambda c: (c["inner"], c["mat"], pn_sort_tuple_common(c["pn"]), c["d"]))
    return out
