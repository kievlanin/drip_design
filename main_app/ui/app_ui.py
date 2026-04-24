import threading
import time
import tkinter as tk
from typing import Optional
from tkinter import ttk

from main_app.ui.silent_messagebox import silent_showerror, silent_showinfo, silent_showwarning
from main_app.ui.dripcad_legacy import DripCAD
from main_app.orchestrator import IrrigationOrchestrator


class DripCADUI(DripCAD):
    """
    Transitional UI layer:
    - keeps existing Tk/Canvas behavior
    - delegates Geo/Hydraulic/BOM workflows to Orchestrator
    """

    def __init__(self, root):
        self.orchestrator = IrrigationOrchestrator()
        super().__init__(root)
        self._rebind_engine_state()

    def _rebind_engine_state(self):
        self.pipe_db = self.orchestrator.get_default_pipe_db()
        self.engine.pipes_db = self.pipe_db
        self.orchestrator.sync_topography_from_ui(self.topo)

    @staticmethod
    def _ochre_progressbar_style(master: tk.Misc) -> str:
        """Тема clam + охристий заповнювач (ttk на Windows інакше часто ігнорує колір)."""
        style_name = "Ochre.Horizontal.TProgressbar"
        st = ttk.Style(master)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        ochre = "#C4933A"
        st.configure(
            style_name,
            troughcolor="#2a2520",
            background=ochre,
            lightcolor="#D4A85C",
            darkcolor="#8B5E28",
            borderwidth=0,
        )
        return style_name

    def build_contours_kriging(self):
        self.build_contours(interp_method="kriging")

    def build_contours(self, interp_method: str = "idw"):
        geom = self.contour_clip_geometry()
        if geom is None:
            silent_showwarning(self.root, 
                "Увага",
                "Потрібна зона проєкту (рамка на карті), контур поля, KML зони SRTM або щонайменше три точки висоти для меж ізоліній.",
            )
            return
        if not self.topo.elevation_points:
            silent_showwarning(self.root, "Увага", "Немає точок висоти! Розставте точки для побудови рельєфу.")
            return
        try:
            step_z = float(self.var_topo_step.get().replace(",", "."))
            grid_size = float(self.var_topo_grid.get().replace(",", "."))
        except ValueError:
            silent_showerror(self.root, "Помилка", "Некоректні значення параметрів рельєфу.")
            return

        elev_snapshot = [tuple(p) for p in self.topo.elevation_points]
        root = self.root
        title_backup = self.root.title()
        self._contour_title_backup = title_backup
        self._contour_build_active = True
        progress_throttle_s = 0.12
        _last_prog_t = [0.0]
        _im = str(interp_method or "idw").strip().lower()

        def _reset_contour_buttons():
            if hasattr(self.control_panel, "btn_build_contours"):
                self.control_panel.btn_build_contours.config(
                    state=tk.NORMAL,
                    text="Побудувати ізолінії",
                )
            if hasattr(self.control_panel, "btn_build_contours_kriging"):
                self.control_panel.btn_build_contours_kriging.config(
                    state=tk.NORMAL,
                    text="Побудувати ізолінії (кріггінг)",
                )

        def _busy_contour_buttons():
            if hasattr(self.control_panel, "btn_build_contours"):
                self.control_panel.btn_build_contours.config(
                    state=tk.DISABLED,
                    text="⏳ Ізолінії…" if _im == "idw" else "Побудувати ізолінії",
                )
            if hasattr(self.control_panel, "btn_build_contours_kriging"):
                self.control_panel.btn_build_contours_kriging.config(
                    state=tk.DISABLED,
                    text="⏳ Кріггінг…" if _im == "kriging" else "Побудувати ізолінії (кріггінг)",
                )

        def _restore_title():
            self._contour_build_active = False
            bt = getattr(self, "_contour_title_backup", title_backup)
            try:
                root.title(bt)
            except tk.TclError:
                pass

        def progress_cb(phase: str, cur: int, total: int) -> None:
            if total <= 0:
                return
            now = time.monotonic()
            is_last = cur >= total - 1
            if phase == "grid" and not is_last and (now - _last_prog_t[0]) < progress_throttle_s:
                return
            _last_prog_t[0] = now
            b = getattr(self, "_contour_title_backup", title_backup)
            pct = min(99, int(100.0 * (cur + 1) / max(total, 1)))

            def _upd_title():
                if not getattr(self, "_contour_build_active", False):
                    return
                if phase == "grid":
                    root.title(f"{b} | Ізолінії: сітка {cur + 1}/{total} (~{pct}%)")
                else:
                    root.title(f"{b} | Ізолінії: рівні {cur + 1}/{total} (~{pct}%)")

            root.after(0, _upd_title)

        def _task():
            try:
                contours = self.orchestrator.build_contours(
                    geom,
                    step_z,
                    grid_size,
                    elevation_points=elev_snapshot,
                    progress_cb=progress_cb,
                    interp_method=_im,
                )

                def _done():
                    _restore_title()
                    _reset_contour_buttons()
                    self.cached_contours = contours
                    note = getattr(
                        self.orchestrator.geo_module.engine,
                        "last_contour_adaptation_note",
                        None,
                    )
                    if not contours:
                        msg = (
                            "Не вдалося побудувати ізолінії. Можливо, перепад висот відсутній або точки за межами контуру."
                        )
                        if note:
                            msg += f"\n\n{note}"
                        silent_showinfo(self.root, "Інфо", msg)
                    else:
                        if note:
                            silent_showinfo(self.root, 
                                "Ізолінії (велике поле)",
                                note
                                + "\n\nЗа потреби збільште крок сітки або крок ізоліній на вкладці «Рельєф» для дрібнішої сітки.",
                            )
                    self.redraw()

                root.after(0, _done)
            except Exception as err:
                def _err():
                    _restore_title()
                    _reset_contour_buttons()
                    silent_showerror(self.root, "Помилка", f"Побудова ізоліній:\n{err}")

                root.after(0, _err)

        self.orchestrator.sync_topography_from_ui(self.topo)
        _busy_contour_buttons()
        threading.Thread(target=_task, daemon=True).start()

    def fetch_srtm_data(self):
        geom = self.contour_clip_geometry()
        if geom is None or geom.is_empty:
            silent_showwarning(self.root, 
                "Увага",
                "Потрібна зона проєкту (рамка на карті), контур поля (KML) або KML зони SRTM.",
            )
            return
        if getattr(self, "geo_ref", None) is None:
            silent_showwarning(self.root, "Увага", "Проект не має гео-прив'язки (імпортуйте KML з Google Earth)!")
            return

        try:
            res = float(self.var_srtm_res.get().replace(",", "."))
        except Exception:
            res = 30.0

        self.orchestrator.sync_topography_from_ui(self.topo)
        boundary = geom
        source_mode = "auto"
        if hasattr(self, "normalize_consumer_schedule"):
            self.normalize_consumer_schedule()
        try:
            source_mode = str(
                (getattr(self, "consumer_schedule", None) or {}).get("srtm_source_mode", "auto")
            ).strip().lower()
        except Exception:
            source_mode = "auto"

        def _on_success(count, result=None):
            result = result or {}
            self.orchestrator.sync_topography_to_ui(self.topo)
            self.cached_contours = []
            self.zoom_to_fit()
            self.redraw()
            if hasattr(self, "sync_srtm_model_status"):
                self.sync_srtm_model_status()
            active_provider = str(result.get("active_provider", "skadi_local") or "skadi_local")
            self._srtm_active_provider = active_provider
            if hasattr(self, "sync_srtm_source_mode_widgets"):
                self.sync_srtm_source_mode_widgets()
            if hasattr(self.control_panel, "btn_srtm"):
                self.control_panel.btn_srtm.config(state=tk.NORMAL, text="🌐 Завантажити з супутника")
            provider_ui = {
                "skadi_local": "локальні/Skadi",
                "open_elevation": "Open-Elevation",
                "earthdata": "NASA Earthdata",
            }.get(active_provider, active_provider)
            fallback_chain = result.get("fallback_chain_used") or []
            fallback_msg = ""
            if isinstance(fallback_chain, list) and len(fallback_chain) > 1:
                fallback_msg = f"\nFallback: {' -> '.join(str(x) for x in fallback_chain)}"
            silent_showinfo(self.root, 
                "Успіх",
                f"Побудовано {count} точок висоти.\n"
                f"Активне джерело: {provider_ui}.{fallback_msg}",
            )

        def _on_error(err):
            if hasattr(self.control_panel, "btn_srtm"):
                self.control_panel.btn_srtm.config(state=tk.NORMAL, text="🌐 Завантажити з супутника")
            silent_showerror(self.root, "Помилка", f"Не вдалося завантажити SRTM:\n{err}")

        if hasattr(self.control_panel, "btn_srtm"):
            self.control_panel.btn_srtm.config(state=tk.DISABLED, text="⏳ Очікування API...")

        def _task_wrapped():
            try:
                result = self.orchestrator.fetch_srtm_grid(
                    boundary,
                    self.geo_ref,
                    res,
                    source_mode=source_mode,
                )
                self.root.after(0, _on_success, result.get("count", 0), result)
            except Exception as err:
                self.root.after(0, _on_error, str(err))

        threading.Thread(target=_task_wrapped, daemon=True).start()

    def download_srtm_tiles(self):
        bb = self.field_download_bounds_xy()
        if bb is None:
            silent_showwarning(self.root, 
                "Увага",
                "Потрібна рамка зони проєкту на карті, замкнений контур поля або KML зони SRTM.",
            )
            return
        if getattr(self, "geo_ref", None) is None:
            silent_showwarning(self.root, "Увага", "Потрібна гео-прив'язка (KML з координатами).")
            return

        self.orchestrator.sync_topography_from_ui(self.topo)
        source_mode = "auto"
        if hasattr(self, "normalize_consumer_schedule"):
            self.normalize_consumer_schedule()
        try:
            source_mode = str(
                (getattr(self, "consumer_schedule", None) or {}).get("srtm_source_mode", "auto")
            ).strip().lower()
        except Exception:
            source_mode = "auto"

        def _task():
            try:
                results = self.orchestrator.download_srtm_tiles(self.geo_ref, bb, source_mode=source_mode)
                self.root.after(0, _on_ok, results)
            except Exception as err:
                self.root.after(0, _on_err, str(err))

        def _on_ok(results):
            if hasattr(self.control_panel, "btn_srtm_dl"):
                self.control_panel.btn_srtm_dl.config(state=tk.NORMAL, text="⬇ Тайли SRTM у _srtm_ (за KML/полем)")
            if hasattr(self, "sync_srtm_model_status"):
                self.sync_srtm_model_status()
            ok_n = sum(1 for _n, m in results if "завантажено" in m or "вже є" in m)
            lines = "\n".join(f"{n}: {msg}" for n, msg in results[:40])
            if len(results) > 40:
                lines += f"\n… ще {len(results) - 40} рядків"
            silent_showinfo(self.root, 
                "Тайли SRTM",
                f"Папка: _srtm_ у корені проєкту.\nУспішно: {ok_n} / {len(results)}\n\n{lines}",
            )

        def _on_err(err):
            if hasattr(self.control_panel, "btn_srtm_dl"):
                self.control_panel.btn_srtm_dl.config(state=tk.NORMAL, text="⬇ Тайли SRTM у _srtm_ (за KML/полем)")
            silent_showerror(self.root, "Помилка", f"Не вдалося завантажити тайли:\n{err}")

        if source_mode == "open_elevation":
            silent_showwarning(
                self.root,
                "SRTM",
                "Open-Elevation не надає файли тайлів .hgt.\n"
                "Оберіть у верхній панелі «Skadi+локальні» або «NASA Earthdata» (earthaccess / LP DAAC або EARTHDATA_SRTM_TILE_BASE).",
            )
            return

        if hasattr(self.control_panel, "btn_srtm_dl"):
            self.control_panel.btn_srtm_dl.config(state=tk.DISABLED, text="⏳ Завантаження тайлів…")

        threading.Thread(target=_task, daemon=True).start()

    def _compute_local_dem_pts_from_bounds(self, bb, res: float):
        """Сітка висот у локальних координатах; лише там, де є дані в _srtm_."""
        from modules.geo_module import srtm_tiles

        minx_f, miny_f, maxx_f, maxy_f = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
        cache_dir = srtm_tiles.ensure_srtm_dir()
        paths = sorted(cache_dir.glob("*.hgt")) + sorted(cache_dir.glob("*.hgt.gz"))
        if not paths:
            raise ValueError("У папці _srtm_ немає файлів .hgt — спочатку завантажте тайли.")
        tiles = []
        seen = set()
        for p in paths:
            sw = srtm_tiles.parse_hgt_tile_sw_from_stem(srtm_tiles.hgt_path_tile_stem(p))
            if sw is None or sw in seen:
                continue
            seen.add(sw)
            tiles.append(sw)
        if not tiles:
            raise ValueError("Не знайдено валідних імен тайлів у _srtm_.")
        ref_lon, ref_lat = self.geo_ref
        p_lat_min, p_lat_max, p_lon_min, p_lon_max = srtm_tiles.wgs84_bounds_from_xy_bounds(
            minx_f, miny_f, maxx_f, maxy_f, (ref_lon, ref_lat)
        )
        t_lat_min = min(la for la, _lo in tiles)
        t_lat_max = max(la + 1 for la, _lo in tiles)
        t_lon_min = min(lo for _la, lo in tiles)
        t_lon_max = max(lo + 1 for _la, lo in tiles)
        lat_min = max(p_lat_min, t_lat_min)
        lat_max = min(p_lat_max, t_lat_max)
        lon_min = max(p_lon_min, t_lon_min)
        lon_max = min(p_lon_max, t_lon_max)
        if lat_min >= lat_max or lon_min >= lon_max:
            return [], 0
        rect_local = []
        for lat, lon in (
            (lat_min, lon_min),
            (lat_min, lon_max),
            (lat_max, lon_max),
            (lat_max, lon_min),
        ):
            rect_local.append(srtm_tiles.lat_lon_to_local_xy(lat, lon, ref_lon, ref_lat))
        minx = min(p[0] for p in rect_local)
        maxx = max(p[0] for p in rect_local)
        miny = min(p[1] for p in rect_local)
        maxy = max(p[1] for p in rect_local)
        pts = []
        total = 0
        x = float(minx)
        while x <= float(maxx) + 1e-9:
            y = float(miny)
            while y <= float(maxy) + 1e-9:
                total += 1
                lat, lon = srtm_tiles.local_xy_to_lat_lon(x, y, ref_lon, ref_lat)
                z = srtm_tiles.elevation_from_local_srtm(lat, lon, cache_dir)
                if z is not None:
                    pts.append((x, y, float(z)))
                y += res
            x += res
        return pts, total

    def download_srtm_tiles_for_project_zone(self):
        """Лише завантажити .hgt у _srtm_ за рамкою зони проєкту; висоти — окремо («Завантажити з супутника» тощо)."""
        if getattr(self, "project_zone_bounds_local", None) is None:
            silent_showwarning(
                self.root,
                "Зона проєкту",
                "На вкладці «Карта»: інструмент «Зона проєкту (рамка)» — потягніть прямокутник ЛКМ.\n"
                "Далі натисніть «Тайли» тут або завантажте тайли за KML/полем.",
            )
            return
        if getattr(self, "geo_ref", None) is None:
            silent_showwarning(self.root, "Увага", "Потрібна геоприв'язка (задається при першій рамці або з KML).")
            return

        self.orchestrator.sync_topography_from_ui(self.topo)
        bb = self.project_zone_bounds_local
        source_mode = "auto"
        if hasattr(self, "normalize_consumer_schedule"):
            self.normalize_consumer_schedule()
        try:
            source_mode = str(
                (getattr(self, "consumer_schedule", None) or {}).get("srtm_source_mode", "auto")
            ).strip().lower()
        except Exception:
            source_mode = "auto"

        if source_mode == "open_elevation":
            silent_showwarning(
                self.root,
                "Зона проєкту",
                "Open-Elevation не надає завантаження тайлів .hgt.\n"
                "Оберіть «Skadi+локальні» або «NASA Earthdata» (earthaccess або EARTHDATA_SRTM_TILE_BASE), потім повторіть.",
            )
            return

        btn_map = getattr(self, "_map_prepare_zone_button", None)
        btn_cp = getattr(getattr(self, "control_panel", None), "btn_prepare_zone", None)

        def _set_prepare_btns(state: str, text: str):
            for b in (btn_map, btn_cp):
                if b is not None:
                    try:
                        b.config(state=state, text=text)
                    except tk.TclError:
                        pass

        _set_prepare_btns(tk.DISABLED, "⏳ Тайли…")

        def _task():
            try:
                results = self.orchestrator.download_srtm_tiles(self.geo_ref, bb, source_mode=source_mode)
                self.root.after(0, _done, results, None)
            except Exception as err:
                self.root.after(0, _done, None, str(err))

        def _done(results, err):
            _set_prepare_btns(tk.NORMAL, "⬇ Тайли")
            if hasattr(self, "sync_srtm_model_status"):
                self.sync_srtm_model_status()
            if err is not None:
                silent_showerror(self.root, "Помилка", err)
                return
            ok_n = sum(1 for _n, m in results if "завантажено" in m or "вже є" in m)
            lines = "\n".join(f"{n}: {msg}" for n, msg in results[:40])
            if len(results) > 40:
                lines += f"\n… ще {len(results) - 40} рядків"
            if hasattr(self, "_schedule_embedded_map_overlay_refresh"):
                self._schedule_embedded_map_overlay_refresh()
            silent_showinfo(
                self.root,
                "Тайли SRTM (зона)",
                f"Папка: _srtm_ у корені проєкту.\nУспішно: {ok_n} / {len(results)}\n\n{lines}\n\n"
                "Щоб залити висоти в модель рельєфу, використайте «Завантажити з супутника» або «Лише висоти з _srtm_».",
            )

        threading.Thread(target=_task, daemon=True).start()

    def load_local_srtm_heights_bbox(self):
        """Завантажити висоти в проєкт із локальних тайлів, простим bbox-перетином."""
        bb = self.field_download_bounds_xy()
        if bb is None:
            silent_showwarning(self.root, 
                "Увага",
                "Потрібна рамка зони на карті, контур поля або зона SRTM (KML).",
            )
            return
        if getattr(self, "geo_ref", None) is None:
            silent_showwarning(self.root, "Увага", "Потрібна гео-прив'язка (KML з координатами).")
            return
        try:
            res = float(self.var_srtm_res.get().replace(",", "."))
            if res <= 0:
                raise ValueError
        except Exception:
            res = 30.0

        def _task():
            try:
                pts, total = self._compute_local_dem_pts_from_bounds(bb, res)
                self.root.after(0, _on_ok, pts, total)
            except Exception as err:
                self.root.after(0, _on_err, str(err))

        def _on_ok(pts, total):
            if hasattr(self.control_panel, "btn_srtm_local_bbox"):
                self.control_panel.btn_srtm_local_bbox.config(
                    state=tk.NORMAL,
                    text="📥 Лише висоти з _srtm_ (рамка)",
                )
            if not pts:
                silent_showwarning(self.root, 
                    "SRTM",
                    "У вибраній рамці не знайдено локальних висот у _srtm_.",
                )
                return
            self.topo.clear()
            for x, y, z in pts:
                self.topo.add_point(float(x), float(y), float(z))
            self.cached_contours = []
            self.zoom_to_fit()
            self.redraw()
            if hasattr(self, "sync_srtm_model_status"):
                self.sync_srtm_model_status()
            silent_showinfo(self.root, 
                "SRTM",
                f"Додано точок висоти: {len(pts)} / {total}\n"
                "Джерело: тільки локальні тайли _srtm_ (без API).",
            )

        def _on_err(err):
            if hasattr(self.control_panel, "btn_srtm_local_bbox"):
                self.control_panel.btn_srtm_local_bbox.config(
                    state=tk.NORMAL,
                    text="📥 Лише висоти з _srtm_ (рамка)",
                )
            silent_showerror(self.root, "Помилка", f"Не вдалося завантажити локальні висоти:\n{err}")

        if hasattr(self.control_panel, "btn_srtm_local_bbox"):
            self.control_panel.btn_srtm_local_bbox.config(
                state=tk.DISABLED,
                text="⏳ Висоти…",
            )
        threading.Thread(target=_task, daemon=True).start()

    def _collect_hydro_dto(self, active_block_only: bool = True):
        self.orchestrator.sync_topography_from_ui(self.topo)
        merge_meta: dict = {}
        if active_block_only:
            abi = self._safe_active_block_idx()
            if abi is None or not self.field_blocks:
                raise ValueError("Немає активного блоку поля.")
            sm_lines_full, sm_blocks_full = self._all_submain_lines_with_block_indices()
            orig_sm_indices = [i for i, b in enumerate(sm_blocks_full) if b == abi]
            if not orig_sm_indices:
                raise ValueError("У активному блоці немає магістралі (полілінія ≥2 точок).")
            submain_lines = [sm_lines_full[i] for i in orig_sm_indices]
            submain_block_idx = [abi] * len(submain_lines)
            lat_lo, lat_hi = self._lat_index_range_for_block(abi)
            e_steps_full, e_flows_full = self._per_lateral_emit_steps_flows()
            e_steps = e_steps_full[lat_lo:lat_hi]
            e_flows = e_flows_full[lat_lo:lat_hi]
            d_inner_full = self._per_lateral_inner_d_mm()
            lateral_inner_slice = d_inner_full[lat_lo:lat_hi]
            blk = self.field_blocks[abi]
            all_lats = list(blk.get("auto_laterals") or []) + list(blk.get("manual_laterals") or [])
            lateral_block_idx = [abi] * len(all_lats)
            merge_meta["_orig_sm_indices"] = orig_sm_indices
            merge_meta["_merge_lat_lo"] = lat_lo
            merge_meta["_orig_block_idx"] = int(abi)
            plan_all = self._all_submain_section_lengths_by_sm()
            section_lengths_by_sm = [
                plan_all[i] if i < len(plan_all) else [] for i in orig_sm_indices
            ]
        else:
            submain_lines, submain_block_idx = self._all_submain_lines_with_block_indices()
            e_steps, e_flows = self._per_lateral_emit_steps_flows()
            lateral_inner_slice = self._per_lateral_inner_d_mm()
            all_lats = self._flatten_all_lats()
            lateral_block_idx = self._lateral_block_indices()
            section_lengths_by_sm = self._all_submain_section_lengths_by_sm()
        ref_bi = int(submain_block_idx[0]) if submain_block_idx else 0
        eff_ref = self._allowed_pipes_for_block_index(ref_bi)
        mat_str, pn_str = self._derive_hydro_mat_pn_from_allowed(eff_ref)
        emit_model_name = (self.var_emit_model.get() or "").strip()
        emit_nominal_str = (self.var_emit_nominal_flow.get() or "").strip()
        if active_block_only and (not emit_model_name or not emit_nominal_str):
            try:
                abi2 = int(merge_meta.get("_orig_block_idx", -1))
            except (TypeError, ValueError):
                abi2 = -1
            if 0 <= abi2 < len(self.field_blocks):
                p_blk = self.field_blocks[abi2].get("params") or {}
                if not emit_model_name:
                    emit_model_name = str(p_blk.get("emit_model", "") or "").strip()
                if not emit_nominal_str:
                    emit_nominal_str = str(
                        p_blk.get("emit_nominal_flow", p_blk.get("flow", "")) or ""
                    ).strip()
        try:
            emitter_k_coeff = float((self.var_emit_k_coeff.get().strip() or "0").replace(",", "."))
        except (TypeError, ValueError):
            emitter_k_coeff = 0.0
        try:
            emitter_x_exp = float((self.var_emit_x_exp.get().strip() or "0").replace(",", "."))
        except (TypeError, ValueError):
            emitter_x_exp = 0.0
        try:
            emitter_kd_coeff = float((self.var_emit_kd_coeff.get().strip() or "1").replace(",", "."))
        except (TypeError, ValueError):
            emitter_kd_coeff = 1.0
        if emitter_x_exp <= 1e-12:
            try:
                rec = self._dripper_record(emit_model_name, emit_nominal_str)
            except Exception:
                rec = None
            if isinstance(rec, dict):
                try:
                    emitter_k_coeff = float(rec.get("constant_k", emitter_k_coeff))
                except (TypeError, ValueError):
                    pass
                try:
                    emitter_x_exp = float(rec.get("exponent_x", emitter_x_exp))
                except (TypeError, ValueError):
                    pass
                try:
                    emitter_kd_coeff = float(rec.get("kd", emitter_kd_coeff))
                except (TypeError, ValueError):
                    pass
        dto = {
            "e_step": float(self.var_emit_step.get().replace(",", ".")),
            "e_flow": float(self.var_emit_flow.get().replace(",", ".")),
            "e_steps": e_steps,
            "e_flows": e_flows,
            "v_max": float(self.var_v_max.get().replace(",", ".")),
            "v_min": float(self.var_v_min.get().replace(",", ".")),
            "valve_h_max_m": float(
                (self.var_valve_h_max_m.get().strip() or "0").replace(",", ".")
            ),
            "valve_h_max_optimize": bool(self.var_valve_h_max_optimize.get()),
            "num_sec": int(self.var_num_sec.get()),
            "fixed_sec": self.var_fixed_sec.get(),
            "mat_str": mat_str,
            "pn_str": pn_str,
            "all_lats": all_lats,
            "submain_lines": submain_lines,
            "submain_block_idx": submain_block_idx,
            "submain_section_lengths_by_sm": section_lengths_by_sm,
            "allowed_pipes": self.allowed_pipes,
            "allowed_pipes_blocks": self._build_allowed_pipes_blocks_list(),
            "pipes_db": self.pipe_db,
            "topo": self.topo,
            "lateral_solver_mode": self.var_lateral_solver_mode.get().strip().lower(),
            "emitter_compensated": self._emitter_compensated_effective(),
            "emitter_h_min_m": float(self.var_emit_h_min.get().replace(",", ".")),
            "emitter_h_ref_m": float(self.var_emit_h_ref.get().replace(",", ".")),
            "lateral_inner_d_mm": float(
                (self.var_lat_inner_d_mm.get().strip() or "13.6").replace(",", ".")
            ),
            "lateral_inner_d_mm_list": lateral_inner_slice,
            "emitter_h_press_min_m": float(
                self.var_emit_h_press_min.get().replace(",", ".")
            ),
            "emitter_h_press_max_m": float(
                self.var_emit_h_press_max.get().replace(",", ".")
            ),
            "emitter_k_coeff": float(emitter_k_coeff),
            "emitter_x_exp": float(emitter_x_exp),
            "emitter_kd_coeff": float(emitter_kd_coeff),
            "emitter_model_name": emit_model_name,
            "emitter_nominal_flow_lph": emit_nominal_str,
            "lateral_block_idx": lateral_block_idx,
            "submain_topo_in_headloss": bool(getattr(self, "_submain_topo_in_headloss", True)),
            "submain_lateral_snap_m": self._submain_lateral_snap_m(),
        }
        dto.update(merge_meta)
        return dto

    def run_calculation(self):
        if not self._ensure_emitter_kx_ready():
            return
        abi = self._safe_active_block_idx()
        if abi is None or not self.field_blocks:
            silent_showwarning(self.root, "Увага", "Немає блоку поля для розрахунку.")
            return
        blk = self.field_blocks[abi]
        if not any(len(sm) > 1 for sm in (blk.get("submain_lines") or [])):
            silent_showwarning(self.root, 
                "Увага",
                "У активному блоці немає магістралі (полілінія ≥2 точок).",
            )
            return
        if not self._active_block_submains_have_connected_laterals():
            dmax = self._submain_lateral_snap_m()
            silent_showwarning(self.root, 
                "Увага",
                f"Кожен сабмейн активного блоку має перетинати латераль або бути поруч з нею (≤{dmax:.2f} м — "
                "див. «Керування»). Замкніть ручну dripline ПКМ біля сабмейну або збільшіть допуск.",
            )
            return

        old_label_pts = dict(self.calc_results.get("section_label_pos") or {})
        self._strip_hydro_for_block_keep_others(abi)
        use_active_block = True
        try:
            data = self._collect_hydro_dto(active_block_only=use_active_block)
        except Exception as err:
            silent_showerror(self.root, "Помилка", f"Некоректні дані: {err}")
            return

        prog_win = tk.Toplevel(self.root)
        prog_win.title("Розрахунок")
        prog_win.configure(bg="#1e1e1e")
        prog_win.transient(self.root)
        prog_win.resizable(False, False)
        prog_win.protocol("WM_DELETE_WINDOW", lambda: None)
        fr = tk.Frame(prog_win, bg="#1e1e1e", padx=20, pady=16)
        fr.pack(fill=tk.BOTH, expand=True)
        prog_lbl = tk.Label(
            fr,
            text="Підготовка…",
            fg="#00FFCC",
            bg="#1e1e1e",
            font=("Segoe UI", 10),
            wraplength=380,
            justify=tk.LEFT,
        )
        prog_lbl.pack(anchor=tk.W, pady=(0, 10))
        _pb_style = self._ochre_progressbar_style(prog_win)
        prog_bar = ttk.Progressbar(
            fr,
            length=380,
            mode="determinate",
            maximum=100,
            style=_pb_style,
        )
        prog_bar.config(value=0)
        prog_bar.pack(fill=tk.X)

        _prog_snap = {"done": 0, "total": 1, "msg": ""}
        _prog_paint_armed = [False]

        def _schedule_progress(done: int, total: int, msg: str) -> None:
            _prog_snap["done"] = max(0, int(done))
            _prog_snap["total"] = max(1, int(total))
            _prog_snap["msg"] = msg
            if _prog_paint_armed[0]:
                return
            _prog_paint_armed[0] = True

            def _paint_prog() -> None:
                _prog_paint_armed[0] = False
                try:
                    if not prog_win.winfo_exists():
                        return
                    tt = _prog_snap["total"]
                    dd = min(_prog_snap["done"], tt)
                    pct = min(100, int(100.0 * float(dd) / float(tt)))
                    prog_lbl.config(text=_prog_snap["msg"])
                    prog_bar.config(maximum=100, value=pct)
                except tk.TclError:
                    pass

            self.root.after(0, _paint_prog)

        data["progress"] = _schedule_progress

        def _hydro_task() -> None:
            try:
                hydro_result = self.orchestrator.run_hydraulic_preset(data)
                self.root.after(0, _after_hydro, hydro_result, None)
            except Exception as err:
                self.root.after(0, _after_hydro, None, err)

        def _after_hydro(hydro_result, err) -> None:
            if err is not None:
                try:
                    prog_win.destroy()
                except tk.TclError:
                    pass
                silent_showerror(self.root, "Помилка", f"Некоректні дані: {err}")
                return

            partial = hydro_result["results"]
            if use_active_block and "_orig_sm_indices" in data:
                remapped = self._remap_partial_hydro_results(
                    partial,
                    data["_orig_sm_indices"],
                    int(data["_merge_lat_lo"]),
                    int(data.get("_orig_block_idx", -1)),
                )
                self._merge_hydro_slice_into_state(remapped)
                self._refresh_emitter_q_extrema_overlay_state([int(data.get("_orig_block_idx", -1))])
            else:
                self.calc_results = partial
                self._refresh_emitter_q_extrema_overlay_state()
            self._restore_section_label_positions(old_label_pts)
            self.last_report = hydro_result["report"]
            self.orchestrator.last_hydraulic = {
                "report": self.last_report,
                "results": self.calc_results,
            }

            try:
                if prog_win.winfo_exists():
                    prog_bar.config(maximum=100, value=100)
            except (tk.TclError, ValueError, TypeError):
                pass
            prog_lbl.config(text="Відомість матеріалів (BOM)…")
            self.root.update_idletasks()
            try:
                self.orchestrator.run_bom(self.calc_results.get("sections", []), self.pipe_db)
            except Exception as e2:
                try:
                    prog_win.destroy()
                except tk.TclError:
                    pass
                silent_showerror(self.root, "Помилка", f"BOM: {e2}")
                return

            _finish_ok()

        def _finish_ok() -> None:
            try:
                if prog_win.winfo_exists():
                    try:
                        prog_bar.config(maximum=100, value=100)
                        prog_lbl.config(text="Готово.")
                        prog_win.update_idletasks()
                    except (tk.TclError, ValueError, TypeError):
                        pass
                    prog_win.destroy()
            except tk.TclError:
                pass
            self.redraw()
            self.sync_hydro_pipe_summary()
            if hasattr(self, "control_panel"):
                self.control_panel.sync_report_block_selector()
                self.control_panel._render_block_report_text()
                try:
                    self.control_panel.notebook.select(self.control_panel.tab_results)
                except Exception:
                    pass

        threading.Thread(target=_hydro_task, daemon=True).start()

    def run_stress_calculation(self):
        if not self._all_submain_lines():
            silent_showwarning(self.root, "Увага", "Намалюйте хоча б один сабмейн!")
            return
        if not self._all_submains_have_connected_laterals():
            dmax = self._submain_lateral_snap_m()
            silent_showwarning(self.root, 
                "Увага",
                f"Кожен сабмейн має перетинати латераль або бути поруч з нею (≤{dmax:.2f} м — "
                "див. «Керування»). Замкніть ручну dripline ПКМ біля сабмейну або збільшіть допуск.",
            )
            return
        if not self.orchestrator.last_hydraulic.get("results", {}).get("sections"):
            silent_showwarning(self.root, 
                "Увага",
                "Спочатку виконайте основний розрахунок кнопкою «▶ РОЗРАХУНОК».",
            )
            return
        try:
            base = self._collect_hydro_dto(active_block_only=False)
        except Exception as err:
            silent_showerror(self.root, "Помилка", f"Некоректні дані: {err}")
            return

        sw = tk.Toplevel(self.root)
        sw.title("Stress-тест")
        sw.configure(bg="#1e1e1e")
        sw.transient(self.root)
        sw.resizable(False, False)
        sw.protocol("WM_DELETE_WINDOW", lambda: None)
        fr = tk.Frame(sw, bg="#1e1e1e", padx=20, pady=16)
        fr.pack(fill=tk.BOTH, expand=True)
        sl = tk.Label(
            fr,
            text="Повторний гідравлічний прогін по полілініях секцій…",
            fg="#00FFCC",
            bg="#1e1e1e",
            font=("Segoe UI", 10),
            wraplength=380,
            justify=tk.LEFT,
        )
        sl.pack(anchor=tk.W, pady=(0, 10))
        _sb_style = self._ochre_progressbar_style(sw)
        sb = ttk.Progressbar(fr, length=380, mode="determinate", maximum=100, style=_sb_style)
        sb.pack(fill=tk.X)

        _st_snap = {"done": 0, "total": 1, "msg": ""}
        _st_arm = [False]

        def _sched_stress(done: int, total: int, msg: str) -> None:
            _st_snap["done"] = max(0, int(done))
            _st_snap["total"] = max(1, int(total))
            _st_snap["msg"] = msg
            if _st_arm[0]:
                return
            _st_arm[0] = True

            def _u() -> None:
                _st_arm[0] = False
                try:
                    if not sw.winfo_exists():
                        return
                    tt = _st_snap["total"]
                    dd = min(_st_snap["done"], tt)
                    pct = min(100, int(100.0 * float(dd) / float(tt)))
                    sl.config(text=f"Stress: {_st_snap['msg']}")
                    sb.config(maximum=100, value=pct)
                except tk.TclError:
                    pass

            self.root.after(0, _u)

        dto = {k: v for k, v in base.items()}
        dto["progress"] = _sched_stress

        def _st_task() -> None:
            try:
                self.orchestrator.run_stress_test(dto)
                self.root.after(0, _st_done, None)
            except Exception as ex:
                self.root.after(0, _st_done, ex)

        def _st_done(ex) -> None:
            try:
                if ex is None and sw.winfo_exists():
                    try:
                        sb.config(maximum=100, value=100)
                        sl.config(text="Stress: готово.")
                        sw.update_idletasks()
                    except (tk.TclError, ValueError, TypeError):
                        pass
                sw.destroy()
            except tk.TclError:
                pass
            if ex is not None:
                silent_showerror(self.root, "Stress-тест", str(ex))
                return
            self._show_stress_report_window(self.orchestrator.last_stress.get("report", ""))

        threading.Thread(target=_st_task, daemon=True).start()

    def _show_stress_report_window(self, report: str):
        top = tk.Toplevel(self.root)
        top.title("Stress-тест — звіт")
        top.geometry("560x520")
        top.configure(bg="#1e1e1e")
        sc = tk.Scrollbar(top)
        sc.pack(side=tk.RIGHT, fill=tk.Y)
        txt = tk.Text(top, bg="#222", fg="#FFCC66", font=("Consolas", 10), wrap=tk.WORD, yscrollcommand=sc.set)
        txt.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        sc.config(command=txt.yview)
        txt.insert(tk.END, report or "(порожній звіт)")
        txt.config(state=tk.DISABLED)


