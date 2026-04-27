import json

from main_app.io.project_serialization import (
    atomic_write_text,
    format_json_decode_error,
    normalize_trunk_irrigation_hydro_cache_from_json,
    sanitize_for_json_export,
)


def test_sanitize_for_json_export_normalizes_keys_and_invalid_numbers():
    data = {
        ("A", "B"): {1: float("nan"), 2.0: b"ok"},
        True: float("inf"),
    }

    clean = sanitize_for_json_export(data)

    assert clean["A->B"]["1"] is None
    assert clean["A->B"]["2"] == "ok"
    assert clean["true"] is None


def test_atomic_write_text_overwrites_file_contents(tmp_path):
    path = tmp_path / "sample.json"

    atomic_write_text(str(path), "first")
    atomic_write_text(str(path), "second")

    assert path.read_text(encoding="utf-8") == "second"


def test_normalize_trunk_irrigation_hydro_cache_from_json_restores_int_keys():
    raw = {
        "seg_dominant_slot": {
            "1": "4",
            "2": None,
            "skip": "bad",
        }
    }

    normalized = normalize_trunk_irrigation_hydro_cache_from_json(raw)

    assert normalized["seg_dominant_slot"] == {1: 4, 2: None}


def test_format_json_decode_error_includes_context(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{\n  "a": 1,\n  "b":\n}\n', encoding="utf-8")

    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        message = format_json_decode_error(str(path), err)
    else:
        raise AssertionError("Expected invalid JSON")

    assert "Помилка JSON" in message
    assert '"b":' in message
