# Tech context

## Stack

- **Language:** Python 3.x.
- **GUI:** Tkinter / ttk; embedded map: `tkintermapview` (map tab).
- **Geometry:** Shapely (where required); some hydraulic cores avoid Shapely for import weight.
- **Optional:** `numpy`, `scipy`, `pykrige` for kriging contours (`requirements-contours-optional.txt`); `earthaccess` for NASA SRTMGL1 tiles when no custom `EARTHDATA_SRTM_TILE_BASE` (`requirements-dem.txt`); `fpdf2`, `Pillow` for PDF export.

## Running

From repo root:

```text
py main_app/main.py
```

or `python -m main_app.main`. On Windows, prefer **`py`** if `python` points at an MSYS Python without deps.

## Environment

- Earthdata / NASA: `EARTHDATA_*` env vars; optional `EarthData.txt` (.netrc-style) loaded via `paths.load_earthdata_credentials_from_project_file()`; `EARTHDATA_USER` ↔ `EARTHDATA_USERNAME` synced for `earthaccess`. GUI login bridge: `configure_earthdata_tk_bridge` in `srtm_tiles.py`.
- SRTM cache: `_srtm_/` (content gitignored; `.gitkeep` preserved).

## Key paths

- Designs: `designs/<name>/<name>.json` + per-project `pipes_db.json`.
- Global pipe DB template: root `pipes_db.json` (see `PIPES_DB_PATH`).

## Tooling

- VS Code / Cursor: see “favorite files” table in `PROJECT_STATE.md` for quick navigation.
- Focused refactor tests in this branch use pytest style, e.g. `py -m pytest tests/test_project_serialization.py tests/test_project_normalizers.py`.
