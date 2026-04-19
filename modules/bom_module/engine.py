import math
from collections import defaultdict


def _section_sig(sec):
    return (
        int(sec.get("block_idx", 0)),
        sec.get("mat", "PVC"),
        str(sec.get("pn", "6")),
        str(sec.get("d", "0")),
    )


class BOMModule:
    """BOM and logistics module with quantization and freeze support."""

    def __init__(self):
        self._frozen = {}

    def freeze(self, bom_items):
        self._frozen = {item["key"]: dict(item) for item in bom_items}
        return {"frozen_count": len(self._frozen)}

    def unfreeze(self):
        self._frozen = {}

    def build_bom(self, dto):
        sections = dto.get("sections", [])
        pipes_db = dto.get("pipes_db", {})
        quant_overrides = dto.get("quantization", {})

        grouped = {}
        for sec in sections:
            if bool(sec.get("bom_length_zero", False)):
                continue
            mat = sec.get("mat", "PVC")
            pn = str(sec.get("pn", "6"))
            d = str(sec.get("d", "0"))
            block_idx = int(sec.get("block_idx", 0))
            length = float(sec.get("L", 0.0))
            if length <= 0:
                continue
            key = f"{block_idx}|{mat}|{pn}|{d}"
            grouped[key] = grouped.get(key, 0.0) + length

        bom_items = []
        for key, total_length in grouped.items():
            parts = key.split("|")
            if len(parts) == 4:
                bi_s, mat, pn, d = parts
                block_idx = int(bi_s)
            else:
                mat, pn, d = parts[0], parts[1], parts[2]
                block_idx = 0
            commercial_len = self._resolve_commercial_length(
                mat, pn, d, pipes_db, quant_overrides
            )
            qty = int(math.ceil(total_length / commercial_len)) if commercial_len > 0 else 0
            quantized_length = qty * commercial_len
            waste = max(0.0, quantized_length - total_length)

            item = {
                "key": key,
                "type": "pipe",
                "block_idx": block_idx,
                "material": mat,
                "pn": pn,
                "diameter": d,
                "required_length_m": round(total_length, 2),
                "unit_length_m": round(commercial_len, 2),
                "quantity": qty,
                "quantized_length_m": round(quantized_length, 2),
                "waste_m": round(waste, 2),
                "frozen": False,
                "source": "auto-size",
            }

            if key in self._frozen:
                frozen_item = dict(self._frozen[key])
                frozen_item["frozen"] = True
                bom_items.append(frozen_item)
            else:
                bom_items.append(item)

        bom_items.sort(
            key=lambda x: (
                int(x.get("block_idx", 0)),
                x["material"],
                float(x["pn"]),
                float(x["diameter"]),
            )
        )

        fitting_items = self._estimate_fittings(sections, pipes_db, quant_overrides)

        return {
            "items": bom_items,
            "fitting_items": fitting_items,
            "fitting_note": (
                "Оцінка фурнітури: муфти між бухтами одного DN (за стандартною довжиною з бази) "
                "та переходи на зміні діаметра/PN уздовж магістралі. Без вузлів латераль–сабмейн, "
                "колін на поворотах і арматури кранів — їх можна додати вручну до замовлення."
            ),
            "frozen_count": len(self._frozen),
        }

    def _estimate_fittings(self, sections, pipes_db, quant_overrides):
        """
        Логістика: муфти на стиках бухт одного типорозміру + переходи при зміні d/PN/Mat
        між послідовними ділянками однієї магістралі (порядок за sm_idx + section_index).
        """
        valid = []
        for s in sections:
            L = float(s.get("L", 0.0))
            if L <= 0:
                continue
            valid.append(s)

        by_sm = defaultdict(list)
        for s in valid:
            by_sm[int(s.get("sm_idx", 0))].append(s)

        coupling_totals = {}
        transition_totals = {}

        for sm_idx in sorted(by_sm.keys()):
            chain = sorted(by_sm[sm_idx], key=lambda x: int(x.get("section_index", 0)))
            cur_run = None
            for s in chain:
                sig = _section_sig(s)
                L = float(s.get("L", 0.0))
                if cur_run is None:
                    cur_run = {"sig": sig, "L": L}
                elif sig == cur_run["sig"]:
                    cur_run["L"] += L
                else:
                    self._accumulate_couplings_for_run(
                        cur_run, pipes_db, quant_overrides, coupling_totals
                    )
                    tkey = self._transition_aggregate_key(cur_run["sig"], sig)
                    transition_totals[tkey] = transition_totals.get(tkey, 0) + 1
                    cur_run = {"sig": sig, "L": L}
            if cur_run:
                self._accumulate_couplings_for_run(
                    cur_run, pipes_db, quant_overrides, coupling_totals
                )

        out = []

        for ckey in sorted(coupling_totals.keys()):
            qty = coupling_totals[ckey]
            if qty <= 0:
                continue
            parts = ckey.split("|")
            if len(parts) == 4:
                bi_s, mat, pn, d = parts
                block_idx = int(bi_s)
            else:
                continue
            out.append(
                {
                    "type": "fitting",
                    "role": "coupling_same_dn",
                    "key": ckey,
                    "block_idx": block_idx,
                    "material": mat,
                    "pn": pn,
                    "diameter": d,
                    "quantity": int(qty),
                    "label": f"Муфти/з'єднувачі d{d} (PN{pn}, {mat}), блок {block_idx}",
                }
            )

        for tkey in sorted(transition_totals.keys()):
            qty = transition_totals[tkey]
            if qty <= 0:
                continue
            left, right = tkey.split("->", 1)
            lp = left.split("|", 1)
            bi = int(lp[0]) if lp else 0
            from_part = lp[1] if len(lp) > 1 else left
            out.append(
                {
                    "type": "fitting",
                    "role": "transition",
                    "key": tkey,
                    "block_idx": bi,
                    "quantity": int(qty),
                    "label": f"Перехід/перехідна муфта: {from_part.replace('|', ' ')} → {right.replace('|', ' ')} (блок {bi})",
                }
            )

        out.sort(
            key=lambda x: (
                int(x.get("block_idx", 0)),
                0 if x.get("role") == "coupling_same_dn" else 1,
                x.get("label", ""),
            )
        )
        return out

    def _accumulate_couplings_for_run(self, run, pipes_db, quant_overrides, coupling_totals):
        bi, mat, pn, d = run["sig"]
        L = float(run["L"])
        stick = self._resolve_commercial_length(mat, pn, d, pipes_db, quant_overrides)
        if stick <= 0:
            return
        sticks = int(math.ceil(L / stick))
        joints = max(0, sticks - 1)
        if joints == 0:
            return
        ckey = f"{bi}|{mat}|{pn}|{d}"
        coupling_totals[ckey] = coupling_totals.get(ckey, 0) + joints

    @staticmethod
    def _transition_aggregate_key(sig_a, sig_b):
        bi_a, ma, pa, da = sig_a
        _, mb, pb, db = sig_b
        left = f"{bi_a}|{ma}|{pa}|{da}"
        right = f"{mb}|{pb}|{db}"
        return f"{left}->{right}"

    def _resolve_commercial_length(self, mat, pn, d, pipes_db, quant_overrides):
        override_key = f"{mat}|{pn}|{d}"
        if override_key in quant_overrides:
            return float(quant_overrides[override_key])

        by_mat = pipes_db.get(mat, {})
        by_pn = by_mat.get(pn, {})
        by_d = by_pn.get(d, {})
        if isinstance(by_d, dict) and by_d.get("length"):
            return float(by_d["length"])
        return 5.6

