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
- **Map tab:** `main_app/ui/map_viewer_tk_window.py` — embedded satellite map, project overlay, drawing sync, scale-bar overlay, geo_ref bootstrap from map center; **ліва колонка** — зверху **зона** (тайли, контур, лінії, шари), знизу **панель малювання** з 2 вкладками **Малювання / Магістраль** (`PanedWindow` + `Notebook`). Поля **H для пікета @ H** та **Vmax≥ (індикація v)** — на **правій** вкладці **«Магістраль (HW)»** (`control_panel_impl.py`), не на лівій «Магістраль».
- **Tooltips:** `main_app/ui/tooltips.py` — спільні `attach_tooltip` (панель) та `attach_tooltip_dark` (карта); підказки на кнопках у панелі, на карті, діалогах `dripcad_legacy`, редакторі сабмейну та окремих калькуляторах кореня репо.
- **Hydraulics:** `modules/hydraulic_module/hydraulics_core.py` (`HydraulicEngine.calculate_network`), `lateral_solver`, `submain_telescope_opt`, etc.
- **Топологія магістралі з карти/полотна:** `modules/hydraulic_module/trunk_map_graph.py` — валідація дерева, `build_oriented_edges`, **`expand_trunk_segments_to_pair_edges`** (один запис сегмента = одне ребро між двома вузлами; полілінія в `path_local`); **`ensure_trunk_node_ids`** дописує порожні `id` і **усуває дублікати** (`T9` двічі → другий вузол отримує наступний вільний `TN`, щоб `trunk_tree` і гідравліка поливів не ламались). Тести: `tests/test_trunk_map_graph.py` (у т.ч. `test_duplicate_trunk_node_ids_renumbered`).
- **Гідравліка магістралі за поливами:** `modules/hydraulic_module/trunk_irrigation_schedule_hydro.py` — `compute_trunk_irrigation_schedule_hydro` (HW по дереву, слоти `irrigation_slots`, конверт у кеш для кольорів сегментів). На вузлі-споживачі опційно **`trunk_schedule_q_m3h`** / **`trunk_schedule_h_m`** (індивідуальні Q та цільовий напір замість типових 60 м³/год і 40 м).
- **Ланцюжок споживачів у магістралі (2026-04-14):** вузол `consumption` може бути **проміжним** (не лише листом): локальний відбір Q вузла враховується, а потік далі йде на нащадка; «одночасність» визначається **лише** складом вузлів у `irrigation_slots`.
- **Діалоги без beep (Windows):** `main_app/ui/silent_messagebox.py` — `silent_showinfo` / `silent_showwarning` / `silent_showerror` / `silent_askyesno` (Toplevel + Text); замість `tkinter.messagebox` у основному UI, `file_io_impl`, карті, калькуляторах кореня.
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
- **«Улюблені» / pinned у IDE:** перелік шляхів для швидкого доступу — у [PROJECT_STATE.md](PROJECT_STATE.md), секція **«Ключові файли для швидкого доступу»** (разом із цим файлом тримайте під рукою в редакторі).

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

### Snapshot 2026-04-12 (GitHub, silent UI, магістраль поливів, споживач)
- **Git:** репозиторій підключено до **GitHub** (`origin`); робочі коміти з осмисленими повідомленнями; оновлювати **PROJECT_CONTEXT.md** / **PROJECT_STATE.md** разом із змінами коду.
- **Беззвучні сповіщення:** у проєкті прибрано прямі виклики `messagebox.*` на користь **`main_app/ui/silent_messagebox.py`** (у т.ч. `app_ui`, `control_panel_impl`, `file_io_impl`, `map_viewer_tk_window`, `dripcad_legacy`, `submain_segment_editor`, `lateral_field_calculator`, `submain_telescope_calculator`).
- **Магістраль за поливами:** кнопка/потік у `dripcad_legacy.run_trunk_irrigation_schedule_hydro` → `compute_trunk_irrigation_schedule_hydro`; кеш **`trunk_irrigation_hydro_cache`**; графік дефіциту напору по слотах (Canvas) після попереджень; підпис насоса: **H баж.** і **(розр. … м)**.
- **Подвійний ЛКМ:** по **відрізку** магістралі — діалог труби (матеріал, PN, Ø) з `trunk_allowed_pipes` / `pipe_db`; по **споживачу** (`consumption` / `valve`) — витрата та цільовий напір для сценарію поливу, далі **перерахунок** поливів. Працює на полотні та на вкладці «Карта» (спільний `handle_trunk_segment_double_click_world`).

### Snapshot 2026-04-13 (магістраль: унікальні id після редагування)
- **Проблема:** після вставки кількох пікетів з однаковим автогенерованим id (наприклад два **`T9`**) дерево `trunk_tree` і **гідравліка магістралі за поливами** втрачали коректну топологію (один id = дві вершини).
- **Рішення:** у `trunk_map_graph.ensure_trunk_node_ids` після заповнення порожніх id викликається **`_dedupe_trunk_node_ids_inplace`** — перший вузол зберігає id, наступні дублікати перейменовуються на наступний вільний **`TN`**.
- **Приклад у репо:** `designs/test01/test01.json` узгоджено (другий пікет на гілці від `T8` — **`T10`**, ребра `T8→T10`, `T10→T1`).
- **Тест:** `tests/test_trunk_map_graph.py` — `test_duplicate_trunk_node_ids_renumbered`.
- **Практична перевірка:** видалення ребра, додавання двох пікетів і нових ребер — сценарій **працює**; після змін варто **зберегти проєкт**, щоб `trunk_tree` на диску відповідав карті (дедуп по вузлах не переписує поля `parent_id`/`child_id` у застарілому дереві без синхронізації).

### Snapshot 2026-04-14 (магістраль: UX редагування, H-пікет, профіль)
- **Пріоритет магістралі у виборі/снапі:** коли активна вкладка **«Магістраль»** (і/або trunk-інструмент), hit-test віддає пріоритет вузлам/ребрам магістралі над межами блоків та іншими шарами; граф магістралі піднятий у верхній шар на полотні і карті.
- **Споживач у ланцюгу:** `consumption` може бути проміжним вузлом (не лише листом); одночасність визначається списком `irrigation_slots` (включений у слот — працює, інакше ні). Візуально: позначено кінцевий/проміжний споживач.
- **Авто-вставка пікета за напором:** вставка пікета в точці перетину `H=...` за peak-слотом поливів (поле **H, м** + **Пікет @ H** на вкладці **«Магістраль (HW)»**, `ControlPanel.var_trunk_picket_head_m`); ребро розривається на два сегменти з переносом pipe-атрибутів; автофокус і підсвітка вузла.
- **Профіль прокладки:** для вибраного сегмента труби відкривається профіль земної поверхні (`TopoEngine.get_z`) ламаною з кроком **6 м**; додано поле вертикального масштабу (default `0.1`) та інтерактивний курсор, що синхронно показує точку на лінії труби на карті/полотні.
- **Контекстне меню в `select` (панель «Магістраль»):** ЛКМ — вибір об’єкта; ПКМ — меню об’єкта. Для труби доступний пункт **«Профіль прокладки»**. Окрему кнопку профілю з панелі прибрано.

### Snapshot 2026-04-15 (оптимізація ваги труб, L мін сегмента, UX розкладу)
- **Новий модуль оптимізації труб:** `modules/hydraulic_module/pipe_weight_optimizer.py` (DTO + `optimize_fixed_topology_by_weight`, `optimize_single_line_allocation_by_weight`, побудова options з `pipes_db`).
- **Інтеграція магістралі:** у `run_trunk_irrigation_schedule_hydro` додано перемикач цілі `trunk_schedule_opt_goal` (`weight`/`cost_index`) та `trunk_schedule_min_seg_m`; для `weight` застосовується оптимізація ваги.
- **Короткі сегменти магістралі:** при `L < Lmin` сегмент **поглинається попереднім (upstream)** у підборі діаметра; у фінальних picks коротке ребро успадковує діаметр поглинача.
- **Інтеграція сабмейну:** `optimize_submain_telescope_by_weight` + підтримка `min_segment_length_m`; у калькуляторі сабмейну додано поле `Мін. довжина сегмента` і вибір критерію `weight/cost_index`.
- **UI розкладу:** прибрано кнопку **«Скинути чернетку»** і формулювання про «чернетку» для вибору споживачів; у лівому блоці додано поля **L мін магістралі** та **критерій підбору**.

### Snapshot 2026-04-15 (магістраль поливів — гідравліка, оптимізація, UX, BOM)
- **Швидкість потоку в оптимізації:** за замовчуванням **не** обмежується (`max_velocity_m_s` / `max_pipe_velocity_mps` / `v_max_m_s` = **0** означає вимкнено); фільтрація кандидатів за швидкістю лише якщо значення **> 0**. У розрахунку магістралі з UI примусово передається **v = 0**, щоб не підхоплювати застарілі значення з JSON. Поле **v max** у панелі розкладу **прибрано**.
- **Підбір діаметрів:** після знаходження допустимого рішення — фаза **«стиснення»** (зменшення Ø по сегментах у межах бюджету втрат напору), щоб не залишати завеликі труби на коротких ділянках. Порожній список **`allowed_pipes`** для PN трактується як **«жодна труба не дозволена»**, а не «усі».
- **Режим розрахунку магістралі:** прапорець **`consumer_schedule.trunk_pipes_selected`** — якщо **false**, підбір труб під **заданий** напір насоса; якщо **true** (після оптимізації або ручного вибору труб) — показ **потрібного** напору насоса (`use_required_pump_head` у `compute_trunk_irrigation_schedule_hydro`). Кнопка **«Скинути результат»** очищає кеш/підсвітку розрахунку без зміни обраних труб і слотів.
- **Діалог труби:** чекбокс **довжина = 0 для BOM** (`bom_length_zero` на сегменті) — сегмент **чорний** на полотні/мапі; у BOM ділянка з цим прапорцем **пропускається**. Поле проходить через `trunk_map_graph.expand_trunk_segments_to_pair_edges` (`carry_keys`).
- **Карта:** при панорамуванні/зумі вузли магістралі перемальовуються разом із трасою (`map_viewer_tk_window`: оновлення гліфів вузлів).
- **Інструмент «Зум рамкою»:** однакова поведінка на полотні та карті; під час перетягування видима **пунктирна рамка** після `redraw` (тег `preview` / `map_live_preview` за контекстом).
- **ПКМ по вибраній трубі магістралі:** пункт меню **«Вибір труби»** — той самий діалог, що й подвійний ЛКМ.
- **Hover Q/P над насосом:** окремий hit-test вузла **`source`** — `_trunk_hydro_hover_pick_pump` у `dripcad_legacy.py`; підказка **Q / P** прив’язана до координат насоса на **полотні** і на **накладенні карти** (раніше могла «залипати» в куті через fallback на сегмент).
- **Наступний крок (не зроблено):** явний тумблер у UI **автопідбір труб** / **фіксовані труби (рахувати потрібний H насоса)** поверх логіки `trunk_pipes_selected`.

### Snapshot 2026-04-18 (магістраль HW: телескоп, порядок секцій, підписи, контекст)
- **`trunk_irrigation_schedule_hydro.py`:** бюджет втрат на ребро для телескопа враховує **глобальний slack** до `max_head_loss_m` (коректний телескоп при одному ребрі / короткій магістралі); тест `test_single_edge_uses_global_slack_for_telescope`.
- **`pipe_weight_optimizer.py`:** після підбору порядок **`allocations`** орієнтований **уздовж траси parent→child**: спочатку **більший** `d_inner` (апстрім), далі менший — узгоджено з полілінією та кресленням.
- **Підписи телескопа на полотні/карті:** `dripcad_legacy._draw_trunk_map_on_canvas` — текст секції як у сабмейну (`mat Ø…/PN L=…м`); формат **Ø без «з’їдання» нулів** (цілі мм через `int(round)`, не `.rstrip("0")` на `"110"`).
- **Режим LBL (`SUB_LABEL`):** перетягування **підписів телескопа магістралі** так само, як секцій сабмейну (1-й ЛКМ — взяти, рух, 2-й ЛКМ — зафіксувати); позиції в **`consumer_schedule.trunk_telescope_label_pos`** (`"segIdx:chunkIdx"` → `[x,y]` м); при `normalize_consumer_schedule` — **пruning** зачиних ключів; підказка LBL у `map_left_draw_widgets.py`.
- **Права панель «Магістраль (HW)»:** блок **«На полотні / карті»** — **H, м** + **Пікет @ H**, **Vmax≥** (перенесено з лівої вкладки «Магістраль»); `ControlPanel.var_trunk_picket_head_m`.

### Snapshot 2026-04-19 (магістраль: шари BOM/косметика, Ø, маркери, графік H(s))
- **Підписи:** у тексті секцій телескопа та в суміжних підписах **Ø** замість префікса `d` перед діаметром (`_trunk_telescope_section_label_text`, `_trunk_telescope_short_label`, `trunk_segment_display_caption`).
- **Маркер зміни діаметра:** на стику секцій телескопа — трикутник (кут при вершині **45°**), вершина в бік **меншого** діаметра вздовж потоку; розмір ~**2.5× товщини** лінії труби на полотні (`_TRUNK_MAP_SEGMENT_LINE_WIDTH_PX`) з легким масштабом від zoom.
- **Шари Tk-тегів:** усе з тегом **`trunk_map_canvas`**; додатково **`trunk_map_BOM`** (лінії відрізків, підкладка v, трикутники переходу, геометрія вузлів, хрест профілю) та **`trunk_map_Cosmetic`** (підписи, кільця слотів поливу, пунктир снапу кранів, підказки довжини, підпис HW насоса); **`tag_raise(Cosmetic, BOM)`**.
- **ПКМ по ребру:** **«Графік тиску вздовж ребра»** — вікно **H(s)** після «Магістраль за поливами»: **HW по секціях** з `trunk_tree_data` + `edge_q` / `edge_h` з `per_slot`; **s** — відстань **по полілінії** (`_trunk_hw_pressure_pieces_along_polyline`, `hazen_williams_hloss_m` як у `compute_trunk_tree_steady`); fallback — лінійно між H кінців траси.
- **Полотно + графік:** рух миші на графіку → **`_set_trunk_profile_probe`** + **`_polyline_point_at_dist`** (як «Профіль прокладки»); скидання при **Leave** / закритті вікна. На графіку — вертикальний маркер-«стрілка» та ромб на кривій; **s** обмежено межами поля діаграми.

### Snapshot 2026-04-16 (ізолінії виливу, підпис насоса, індикація v на магістралі)
- **Ізолінії виливу (полотно):** після увімкнення показу ізоліній виливу на кожну лінію додається підпис **Q** у **л/г** (середина сегмента, тінь + світлий текст, тег `emit_flow_iso`); легенда згадує підпис.
- **Підпис насоса (магістраль за поливами, режим заданого H):** у `trunk_irrigation_schedule_hydro` для кожного слота обчислюється **`min_required_source_head_m`** (бінарний пошук мінімального H на джерелі під цілі Hспож); у **`envelope.min_required_source_head_m`** — максимум по слотах (найгірший сценарій). У `dripcad_legacy.trunk_irrigation_hydro_pump_label_lines` додано рядок **«H на джерелі мін. (оцінка) ≈ … м»** (лише коли не режим `pump_head_mode=required`). Тест: `tests/test_trunk_irrigation_schedule_hydro.py` (`test_fixed_pump_envelope_has_min_required_source_head`).
- **Індикація v ≥ (оновлення 2026-04-18):** поле вводу перенесено на **вкладку «Магістраль (HW)»** правої панелі (`control_panel_impl.py`); логіка без змін — поріг **м/с** для **візуальної** підсвітки відрізків після «Магістраль за поливами»; значення в **`consumer_schedule.trunk_display_velocity_warn_mps`**, JSON через **`file_io_impl`**, синхронізація **`DripCAD.sync_trunk_display_velocity_warn_var_from_schedule`**. Малювання: червона «підкладка» у **`_draw_trunk_map_on_canvas`** і на карті в **`map_viewer_tk_window.py`**. **`trunk_display_velocity_warn_mps_effective()`** не викликає **`normalize_consumer_schedule()`** під час `redraw`.
- **Оптимізація труб магістралі** як і раніше лише за **ΔH** і каталогом при **v = 0** у потоці розрахунку; поріг на панелі — лише індикація, не обмеження підбору.

### Snapshot 2026-04-17 (магістраль: явний режим автопідбору/фіксації труб)
- **Вкладка «Розклад» (`control_panel_impl.py`):** додано явний чекбокс **«Фіксовані труби (без автопідбору)»**. Він напряму керує `consumer_schedule.trunk_pipes_selected` (режим розрахунку), синхронізується після завантаження проєкту і флашиться перед збереженням.
- **Поведінка `run_trunk_irrigation_schedule_hydro` (`dripcad_legacy.py`):** режим береться з UI перед запуском; після успішного автопідбору ваги прапорець `trunk_pipes_selected` **більше не перемикається автоматично** в `True` (режим не «залипає»).
- **I/O (`file_io_impl.py`):** додано `_flush_schedule_trunk_pipe_mode_to_app` у пайплайн збереження та `_sync_schedule_trunk_pipe_mode_ui` після завантаження.

### Snapshot 2026-04-17 (емітери: без H опорної в моделі)
- **Модель виливу емітера (`lateral_drip_core.py`):** для некомпенсованих емітерів розрахунок ведеться через **`q = k * H^x`** (з `kd`), без використання змінної `H опорна`. Якщо `k/x` відсутні — застосовується сумісний fallback (`x=0.5`, `k` із `Qном@10м`).
- **UI (`control_panel_impl.py`):** у вкладці «Гідравліка» прибрано поле **`H опорна для Q ном`**; підпис змінено на **`Q ном (л/год), k·H^x`**.
- **Звіт гідравліки (`hydraulics_core.py`):** текст у звіті відображає закон `k·H^x` (або fallback), а не `√(H/Href)`.

### Snapshot 2026-04-17 (магістраль: objective money/weight + телескоп ребра)
- **Оптимізатор труб (`pipe_weight_optimizer.py`):** універсальна ціль `objective` (`money`/`weight`), `price_per_m` у `PipeOption` (для `money` без позитивних цін у дозволеному каталозі — зрозуміле повідомлення з рекомендацією `weight`), `total_objective_cost`; телескоп **1–4** секції: пара суміжних діаметрів + сітковий підбір для **3–4** суміжних ступенів; у `OptimizationConstraints` зарезервовано **`length_round_step_m`** (округлення довжин після підбору; з виклику магістралі зараз **0**).
- **Розклад магістралі (`dripcad_legacy.py`, `trunk_irrigation_schedule_hydro.py`):** `trunk_schedule_opt_goal` — `money`/`weight` (`cost_index` → `money`); **«Макс. секцій на ребро»** (1..4) у `consumer_schedule`; Q на ребро для оптимізації — **peak по слотах**; після телескопа перевірка тисків по всіх слотах; ітерація з **розширенням бюджету ΔH** при дефіциті напору.
- **Телескоп по ребру:** у підборі та JSON — `sections` / **`telescoped_sections`** (довжина, d_inner, objective_cost тощо); мін. довжина — `trunk_schedule_min_seg_m`. У **`compute_trunk_irrigation_schedule_hydro`** властивості ребра з дерева читають `sections` або **`telescoped_sections`**.
- **Сумісність дерева:** `trunk_tree_compute.TrunkTreeEdge` + `sections`; втрати по ребру — сума по секціях; **hover на карті** — підказка з секціями (`trunk_segment_display_caption` / `map_viewer_tk_window`).

### Snapshot 2026-04-17 (вечір — UX магістралі: перетягування, вибір, кран, труби)
- **Перетягування вузлів у VIEW:** ЛКМ по вузлу в межах snap — зміна `x`/`y`/`lat`/`lon` без зміни `node_indices` у сегментах (`sync_trunk_segment_paths_from_nodes`); на карті — ланцюг `B1-Motion` / `ButtonRelease-1` у `map_viewer_tk_window.py`. Якщо увімкнено **«Вибір»**, клік по вузлу **не** стартує рамку — пріоритет перетягування; у `_canvas_b1_motion` рух вузла обробляється **раніше** за оновлення рамки.
- **Споживач → кран:** при **постановці** інструментом «Споживач» — снап до початку сабмейну (`get_valves`, той самий радіус, що й у підборі); під час **перетягування** снап вимкнено, **один раз** після відпускання ЛКМ — `_finalize_trunk_node_drag` (щоб не «липло» в радіусі крана).
- **Зони снапу:** пунктирні кола (`#7CB342`, `dash=(5,4)`) навколо кранів на полотні (`_draw_consumer_valve_snap_zones_on_canvas`) і на карті в `_draw_trunk_map_node_glyphs`, коли активна панель магістралі / інструмент споживача / тягнеться споживач (`_consumer_valve_snap_overlay_enabled`).
- **«Труби для магістралі…»:** кнопка перенесена з лівої вкладки «Магістраль» на праву панель **«Магістраль (HW)»** (`control_panel_impl.py`); з лівої панелі прибрано (`map_left_draw_widgets.py`).
- **Подвійний ЛКМ по трубі:** `_pick_trunk_segment_index_for_pipe_edit` для **будь-якої** геометрії `_trunk_segment_world_path` (не лише `len(node_indices)==2`); у `_resolve_trunk_node_vs_segment_pick` перемагає **ближчий** об’єкт (вузол або ребро), без примусового пріоритету вузла в «сірій зоні»; при конфлікті споживач/ребро — діалог з меншою відстанню до курсора.
- **Видалення графа:** Delete/BackSpace **не** видаляють `trunk_node` / `trunk_seg` з вибору; **ПКМ** — `_open_trunk_graph_context_menu` (VIEW/PAN з панеллю магістралі та на карті перед скиданням пасивного інструмента): **Видалити вузол / відрізок**, труба, профіль, **графік тиску вздовж ребра**, Q/P для споживача.

## 8. UPCOMING ROADMAP (idea backlog)
- **Магістраль — розрахунок:** `trunk_tree_compute.py` уже в проді; **наступне** — глибша прив’язка споживачів до **кранів/блоків** і повний цикл Q(t) / гідравліки поля (див. PROJECT_STATE.md).
- **Наступний спроектований етап — магістраль (див. PROJECT_STATE.md):** окрема модель **відкритого графа-дерева** від **витоку** (джерело/насосна точка): вершини типів **поворот**, **розгалуження**, **споживання** (підключення зон поля / блоків / вузлів навантаження); ребра — ділянки трубопроводу. До вузлів споживання — **графік споживання** (часові ряди Q(t) або еквівалентні профілі) для гідравліки та планування поливу. Інтеграція з поточними сабмейнами блоків — поетапно (прив’язка навантажень, сумісність JSON).
- **Near-term (деталі в PROJECT_STATE.md):** per-block BOM + trunk BOM; active-block selector; submain segment editor з квантуванням довжин під «штанги» з `pipes_db`.
- KML export polish; SRTM / relief workflows; DXF refinements.
