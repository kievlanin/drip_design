# Project Name: Drip Designer Pro (DripCAD)
# Domain: Drip Irrigation System Design & Hydraulic Calculation CAD
# Tech Stack: Python, Tkinter (GUI), Shapely (Geometry), JSON (DB & File I/O)

## 1. PROJECT OVERVIEW
DripCAD is a specialized CAD application for agricultural drip irrigation: field boundaries (multiple blocks), auto/manual laterals, submains, hydraulic calculation (Hazen-Williams, lateral solvers), BOM, export to KML/DXF/PDF. UI and reports are Ukrainian.

## 2. CODE LAYOUT (actual repo)
- **Entry:** `main_app/main.py` → `DripCADUI` (`main_app/ui/app_ui.py`), orchestrator `main_app/orchestrator.py`.
- **Canvas / geometry / drawing:** `main_app/ui/dripcad_legacy.py` (core `DripCAD`).
- **Sidebar:** `main_app/ui/control_panel_impl.py` (`ControlPanel`).
- **I/O:** `main_app/io/file_io_impl.py` — projects under `designs/[name]/[name].json` + `pipes_db.json` per project; **Save As** always uses `designs/<name>/` (sanitized names). Global `pipes_db.json` via `main_app/paths.py` → `PIPES_DB_PATH`.
- **Map tab:** `main_app/ui/map_viewer_tk_window.py` — embedded satellite map, project overlay, drawing sync, scale-bar overlay, geo_ref bootstrap from map center; **ліва колонка** — зверху **зона** (тайли, контур, лінії, шари), знизу **панель малювання** з 2 вкладками **Малювання / Магістраль** (`PanedWindow` + `Notebook`).
- **Tooltips:** `main_app/ui/tooltips.py` — спільні `attach_tooltip` (панель) та `attach_tooltip_dark` (карта); підказки на кнопках у панелі, на карті, діалогах `dripcad_legacy`, редакторі сабмейну та окремих калькуляторах кореня репо.
- **Hydraulics:** `modules/hydraulic_module/hydraulics_core.py` (`HydraulicEngine.calculate_network`), `lateral_solver`, `submain_telescope_opt`, etc.
- **Топологія магістралі з карти/полотна:** `modules/hydraulic_module/trunk_map_graph.py` — валідація дерева, `build_oriented_edges`, **`expand_trunk_segments_to_pair_edges`** (один запис сегмента = одне ребро між двома вузлами; полілінія в `path_local`). Тести: `tests/test_trunk_map_graph.py`.
- **Geo / relief:** `modules/geo_module/`, `TopoEngine` in `dripcad_legacy`.

(Legacy names in older docs: `main.py`, `control_panel.py`, `hydraulics.py` — replaced by the layout above.)

## 3. STRICT DEVELOPMENT RULES (when user requests full files)
- **NO SNIPPETS with placeholders** for “full file” handoffs — deliver complete file content to avoid manual merge errors.
- **MODULARITY:** Keep hydraulics out of pure UI glue; keep file I/O in `file_io_impl`.
- **UKRAINIAN LOCALE:** UI, reports, user-facing strings in Ukrainian.

## 4. UI/UX PHILOSOPHY
- Max workspace, collapsible right panel, RMB for closing contours / ending drafts, snap toggle, visual feedback for snap and hydraulic audit (laterals / emitters).

## 5. ENGINEERING & HYDRAULIC LOGIC (high level)
- Submain sections from user polyline; PN / diameter selection with allowed set from project; lateral connection to submain for graphs and loads.
- Optional per-lateral emitter step/flow via `e_steps` / `e_flows` aligned with `_lateral_block_indices()`.
- Emitter graphs: connection point along lateral vs submain; L1/L2 wings.

## 6. WORKSPACE & FILE MANAGEMENT
- Projects: `designs/[Project_Name]/[name].json` + project `pipes_db.json`.
- `field_blocks_data[]`: per block `ring`, `submain`, `auto`/`manual` coords, optional `params` (grid + emitter snapshot for that block).

## 7. RECENT BEHAVIOR (snapshot 2026-04-10)
- **reset_calc:** not on every LMB while drafting a new contour (`DRAW` + not closed); not immediately after closing a new block polygon — existing hydraulic results stay until something else invalidates them.
- **Hydraulics tab:** no mat/PN comboboxes on the tab; summary label + button opens project pipe selector (mat/PN + allowed Ø in dialog). Hidden comboboxes keep `load_project` / traces working.
- **Submain section colors on map:** taken from current `pipe_db` by mat/PN/Ø when drawing; stale colors in saved `calc_results` do not block updates after DB edit.
- **Emitter dots:** optional pressure-band simplification (in-band → single green); gradient Q only out of band. Visibility follows visible auto-laterals with stable index mapping to `auto_laterals` (no reliance on object id after JSON reload). With “every N-th” (N>1), emitter dots only on those auto lines (manual lines without dots in that mode).
- **Progress (DripCADUI):** hydraulic + stress dialogs use 0–100 bar and coalesced `after(0)` updates so large projects (e.g. many laterals, `designs/kaharlyk/kaharlyk.json`) still show moving progress on Windows/ttk.
- **Global pipe editor:** color swatch next to hex; “Save to file” flushes current form row (including color) into `pipe_db` before JSON write; save does not wipe hydraulic results via `update_pn_dropdown(skip_reset=True)` + `redraw()`. One-click toggle in project pipe selector (`✅/❌` column).
- **DB editors (drippers / laterals / pipes):** added explicit text mask filter in header row (`Маска (усі поля):`) with wildcard support (`*`, `?`, `[]`) applied to concatenated row text.
- **Mask UX behavior:** if user enters plain text without wildcard symbols, mask is treated as substring match automatically (`text` -> `*text*`), so filtering by model names works intuitively.
- **Laterals editor stability under filtering:** row selection now maps to stable source-row index while masked, so update/delete actions target the correct record.
- **Hydro tab (sections / valve):** traces on `fixed_sec`, `num_sec`, `valve_h_max_m`, `valve_h_max_optimize` use `_invalidate_hydro_ui_active_block_or_all()` — only the **active block’s** stored hydro is stripped (`_strip_hydro_for_block_keep_others`); full `reset_calc` only when there are no field blocks. `toggle_sec_entry` only toggles the section-count entry state (no duplicate global reset).
- **Drawing new blocks:** left-click drafting in `DRAW` no longer calls global reset from the generic click invalidation branch; creating a new contour does not wipe hydraulic results of already computed blocks.
- **SUB_LABEL UX update:** two-step move mode — first LMB selects the nearest section label and it follows the cursor; second LMB commits the new position. Temporary move state is cleared by `reset_temp()`.
- **Label persistence on recalculation:** moved section labels are restored after hydraulic recalculation for matching section ids (`sm_idx + section_index + sub_idx`) so manual placement is not lost.
- **Section label keying fix:** label position keys now include `sm_idx` to avoid collisions between different submains with the same `section_index` (notable on `designs/kaharlyk/kaharlyk.json`).
- **Submain edits are block-local:** adding/removing submain geometry invalidates hydro only for the edited block (`_strip_hydro_for_block_keep_others`), preserving other blocks.
- **Report UI:** popup report windows were removed; results are shown in a dedicated `Результати` tab with block selector and in-panel text report.
- **Bottom toolbar:** drawing controls moved from tab content to a bottom toolbar; `VIEW` is default mode at startup/load, with LED-style mode/action buttons and tooltips.
- **Block tab display filters:** `every N-th` affects only auto-lateral line visibility; enabling start/end index selectors disables `every N-th`. Emitter dots checkbox is autonomous from line filters and shows only out-of-pressure-band emitters.
- **Testing (2026-04-06):** focused manual test pass for this iteration is **paused**; resume when new changes land.
- **Block tab — маски переливу / недоливу (2026-04-08):** на мапі активного блоку можна показати лише **зовнішні контури** зон (без маркування кожної крапельниці): union дисків навколо емітераів поза діапазоном H, morphological buffer між рядами, **без** внутрішніх кілець полігону; спрощення/decimate + **кеш** геометрії між `redraw` (pan/zoom). Для **переливу** до маски додається смуга вздовж **ізолінії Q = Q ном** (`var_emit_flow`): `TopoEngine.generate_contours(..., fixed_z_levels=[q_nom])` у `topography_core.py` (фіксований рівень без кратності `step_z`).
- **Щоб не плутати з «крапельницями»:** при увімкнених масках на карті **не** малюються ізолінії «Діаграма виливу»; латералі активного блоку без червоного/жовтого аудиту тиску (нейтральний зелений) — перелив/недолив лише контурами масок. Маски перемальовуються **поверх** точок рельєфу.
- **Submain segment editor (раніше):** ширина числових полів узгоджена з «Необхідна довжина…»; `nametofont(..., root=)` для Python 3.14.

### Snapshot 2026-04-10 (карта, файли, результати, гідравліка UI)
- **Вбудована карта** (`main_app/ui/map_viewer_tk_window.py`): після відкриття панелі **`geo_ref`** автоматично ставиться з **центру поточного виду** карти, якщо в проєкті ще не задано; ЛКМ у режимах малювання може додатково зафіксувати референс з першого кліку. Інструменти **захват тайлів / контур блоку / траса** — пунктир **від останньої вершини до курсора** під час чернетки (`map_draft_rubber`).
- **Режим DRAW на карті:** у попередньому перегляді малюються **усі вже покладені ребра** (суцільна лінія) + **пунктир** до курсора; аналогічно для **DRAW_LAT** (колір помаранчевий). Масштабна лінійка **100 м** — окремий **`Frame`+`Canvas`** поверх `map_area` (не на canvas тайлів `tkintermapview`; раніше виклик неіснуючого API робив лінійку невидимою). *Оновлення 2026-04-11:* панель режимів малювання перенесена в **ліву колонку** карти (див. знімок нижче); **tooltips** кнопок режимів — **зверху** (`_attach_dark_tooltip(..., above=True)`).

### Snapshot 2026-04-11 (проміжна фіксація — магістраль + «Інфо»)
*Зафіксовано на випадок обриву зв’язку; після відновлення продовжувати з цього знімка.*

- **Зберігання магістралі:** після завершення траси (полотно / карта) і після завантаження проєкту викликається **`normalize_trunk_segments_to_graph_edges()`** (`dripcad_legacy.DripCAD`): ланцюг вузлів у одному записі розбивається на послідовність ребер; **`sync_trunk_segment_paths_from_nodes`** для ребра з двома вузлами зберігає **проміжні точки** `path_local`, оновлюючи лише перший/останній під XY вузлів; **`_trunk_segment_world_path`** для двовузлового ребра віддає повну полілінію.
- **Інструмент «Інфо» (`map_pick_info`):** структурований підбір **`_collect_world_pick_hits`**; якщо перше попадання — **вузол або відрізок магістралі**, поверх траси малюється **жовтий** (`#FFEB3B`) шлях **до насоса**; якщо фокус — **розгалуження (junction)** — додатково **лайм** (`#ADFF2F`) усі **гілки від цього вузла до споживачів** (`consumption`). Реалізація: **`trunk_info_highlight_world_paths`**, дерево з **`build_oriented_edges`**. На **карті** — оновлення курсора в `<Motion>` + шар `map_live_preview`; на **«Без карти»** — шар `preview`. Підказки: `map_viewer_tk_window.py`, `map_left_draw_widgets.py`.

### Snapshot 2026-04-11 (карта: зона зверху, малювання знизу)
- **`create_embedded_map_panel`:** ліва смуга (~200 px) — вертикальний **`tk.PanedWindow`**: **верхня частина** — **«Зона / тайли / шари»** (усі кнопки зони, SRTM, overlay, підказки); **нижня** — окрема рамка **«Панель малювання»** з **`ttk.Notebook`** лише на **«Малювання»** (режими + ADD/DEL, LED) та **«Магістраль»** (траса). Плаваюча панель по центру карти **прибрана**; початкове положення розділювача задається `sash_place`.
- **`control_panel_impl.py`:** нижній toolbar «Малювання» **видалено**; поле **L (м)** — **над** рядком статистики правої панелі.
- **Z-order на canvas карти:** піднімаються `map_draft_rubber` і `map_live_preview` окремо від тегів тайлів; `lift` для порожнього тега не блокує підйом лінійки.
- **Гідравліка — режим латераля (бісекція / Ньютон / порівняння):** знято `trace` з `var_lateral_solver_mode`, що викликав глобальний `reset_calc()`; новий режим застосовується лише після **«▶ РОЗРАХУНОК»** (активний блок і так очищається перед прогоном). У підказці на вкладці «Гідравліка» зафіксовано цю поведінку.
- **Файл → Зберегти проект як (JSON):** діалог задає **ім’я**; запис **завжди** у **`designs/<ім’я>/<ім’я>.json`** поруч **`pipes_db.json`**. **`ensure_project_dir`** теж лише під `designs/`; ім’я теки/файлу проходить **санітизацію** заборонених для Windows символів (при потребі оновлюється `var_proj_name`).
- **Вкладка «Результати»:** для обраного блоку показується **площа поля** за контуром `ring` (м² і га, Shapely `Polygon`).

### Snapshot 2026-04-11 (інструмент «Вибір» на полотні «Без карти»)
- **Інструмент `select`** (`dripcad_legacy.py`): окремо від **«Інфо»** (`map_pick_info`); той самий підбір об’єктів, що й у `_collect_world_pick_hits`. **ЛКМ** — після відпускання: короткий рух — один об’єкт + діалог; **перетягування** — **рамка** (Л→П, об’єкт **цілком** у прямокутнику) або **кросрамка** (П→Л, **перетин**) через `_pick_hits_in_world_rect`; список обраного в **`_canvas_selection_keys`**, постійна підсвітка шаром **`_draw_canvas_selection_layer`** після `redraw` (магістраль — жовтий/лайм як у «Інфо», інші типи — свої контури/маркери). Під час drag рамки нижня плашка прев’ю з підписом під курсором **не** показується.
- **ПКМ у режимі «Вибір»:** перший раз очищає вибір; якщо вже порожньо — вихід з інструмента. Перемикання на інструменти, крім **«Інфо»**, очищає вибір; повне вимкнення інструментів — також.
- **Панель:** кнопка «Вибір» у `map_left_draw_widgets.py` — невеликий **`tk.Canvas`** з **діагональною стрілкою** (`create_line` + `arrow=tk.LAST`), не символ Unicode.

### Snapshot 2026-04-10 (рельєф — IDW + кріггінг, SRTM крок)
- **Ізолінії рельєфу:** дві сині кнопки на вкладці «Рельєф» — **IDW** (як раніше) і **звичайний кріггінг** (PyKrige `OrdinaryKriging`): спільна зона обрізки та marching squares; для великих DEM (>500 точок) — **локальне вікно найближчих спостережень**; після підбору варіограми застосовується **послаблення range/nugget**, щоб криві не були надто «рідинними»; старий шлях **natural neighbor (MetPy)** прибрано. Опційні пакети: `requirements-contours-optional.txt` (`pykrige`, `scipy`, `numpy`).
- **Малювання ізоліній рельєфу на полотні:** `create_line(..., smooth=False)` — без згладжування Безьє в Tk.
- **Роздільна здатність DEM / SRTM:** випадач **5, 15, 30, 45, 90** м (`var_srtm_res` у `control_panel_impl.py`); крок сітки точок висоти в `TopoEngine.fetch_srtm_grid(..., resolution)`.

### Snapshot 2026-04-10 (продовження — зона проєкту, сітка, UI)
- **Зона проєкту на карті:** прямокутник задає `project_zone_bounds_local` і оновлює межу SRTM у локальних координатах; конвеєр **тайли + висоти (зона)** / **лише тайли**; завантаження тайлів без повторного скачування вже наявних файлів; оверлей **меж кешу** на карті (чекбокс з коротким підписом **«Межі кешу»**); на вкладці «Рельєф» — узгоджений підпис **«Межі кешу (_srtm_ / DEM)»**.
- **Авто-латералі після «ОНОВИТИ СІТКУ»:** якщо задана зона проєкту, лінії сітки (перпендикуляри до напрямку рядів) обрізаються по **AABB зони проєкту**, а не лише по полігону блоку; промені будуються з динамічною довжиною за `bounds` обрізання. Без зони — як раніше, лише контур блоку.
- **`scene_lines`:** декоративні полілінії (ескіз на карті), зберігаються в JSON проєкту, **не** беруть участі в гідравліці; малювання на полотні та інструмент **«Лінії»** на карті.
- **Підказки (tooltips):** українські пояснення на кнопках по всьому основному UI та на окремих утилітах (`lateral_field_calculator.py`, `submain_telescope_calculator.py`); спільний модуль `main_app/ui/tooltips.py`.

## 8. UPCOMING ROADMAP (idea backlog)
- **Магістраль — розрахунок (перший крок):** `modules/hydraulic_module/trunk_tree_compute.py` — стійкий HW-розрахунок **дерева** (`TrunkTreeSpec`, `compute_trunk_tree_steady`, `validate_trunk_tree`); юніт-тести `tests/test_trunk_tree_compute.py`. Далі: UI/JSON, Q(t), прив’язка до блоків.
- **Наступний спроектований етап — магістраль (див. PROJECT_STATE.md):** окрема модель **відкритого графа-дерева** від **витоку** (джерело/насосна точка): вершини типів **поворот**, **розгалуження**, **споживання** (підключення зон поля / блоків / вузлів навантаження); ребра — ділянки трубопроводу. До вузлів споживання — **графік споживання** (часові ряди Q(t) або еквівалентні профілі) для гідравліки та планування поливу. Інтеграція з поточними сабмейнами блоків — поетапно (прив’язка навантажень, сумісність JSON).
- **Near-term (деталі в PROJECT_STATE.md):** per-block BOM + trunk BOM; active-block selector; submain segment editor з квантуванням довжин під «штанги» з `pipes_db`.
- KML export polish; SRTM / relief workflows; DXF refinements.
