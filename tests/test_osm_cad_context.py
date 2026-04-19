"""Unit tests for OSM CAD context (Overpass JSON → drawables)."""

from modules.geo_module.osm_cad_context import overpass_json_to_drawables


def test_overpass_json_to_drawables_highway_line():
    payload = {
        "elements": [
            {
                "type": "way",
                "id": 1,
                "tags": {"highway": "residential"},
                "geometry": [
                    {"lat": 50.0, "lon": 30.0},
                    {"lat": 50.001, "lon": 30.002},
                ],
            }
        ]
    }
    d = overpass_json_to_drawables(payload, zoom=14.0)
    assert len(d) == 1
    assert d[0].kind == "line"
    assert d[0].highway == "residential"
    assert len(d[0].latlon) >= 2


def test_overpass_json_to_drawables_building_poly():
    payload = {
        "elements": [
            {
                "type": "way",
                "id": 2,
                "tags": {"building": "yes"},
                "geometry": [
                    {"lat": 50.0, "lon": 30.0},
                    {"lat": 50.0, "lon": 30.001},
                    {"lat": 50.001, "lon": 30.001},
                    {"lat": 50.001, "lon": 30.0},
                    {"lat": 50.0, "lon": 30.0},
                ],
            }
        ]
    }
    d = overpass_json_to_drawables(payload, zoom=16.0)
    assert len(d) == 1
    assert d[0].kind == "poly"
    assert d[0].is_building is True


def test_overpass_json_to_drawables_simplify_scale_keyword_only():
    payload = {
        "elements": [
            {
                "type": "way",
                "id": 3,
                "tags": {"highway": "residential"},
                "geometry": [{"lat": 50.0, "lon": 30.0}, {"lat": 50.0002, "lon": 30.0002}],
            }
        ]
    }
    d0 = overpass_json_to_drawables(payload, zoom=14.0, simplify_scale=1.0)
    d1 = overpass_json_to_drawables(payload, zoom=14.0, simplify_scale=5.0)
    assert len(d0) == 1 and len(d1) == 1
    assert len(d0[0].latlon) >= 2
