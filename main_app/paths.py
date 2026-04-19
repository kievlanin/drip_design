"""Single source of truth for repository-root-relative paths."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPES_DB_PATH = PROJECT_ROOT / "pipes_db.json"
DRIPPERS_DB_PATH = PROJECT_ROOT / "drippers_db.json"
DRIPPERLINES_DB_PATH = PROJECT_ROOT / "dripperlines_db.json"
LATERALS_DB_PATH = PROJECT_ROOT / "laterals_db.json"
DESIGNS_DIR = PROJECT_ROOT / "designs"
# Повні тайли SRTM (.hgt), без різання — за межами імпортованого KML / поля
SRTM_DIR = PROJECT_ROOT / "_srtm_"
# Кеш відповідей Overpass для модуля osm_cad_context (резерв / тести; у UI карти векторний шар вимкнено)
OSM_CAD_CACHE_DIR = PROJECT_ROOT / "_osm_cad_cache_"
