from __future__ import annotations

import webview


HTML = """<!doctype html>
<html lang="uk">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DripCAD Map Viewer</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
        crossorigin="" />
  <style>
    html, body, #map {
      width: 100%;
      height: 100%;
      margin: 0;
      padding: 0;
      background: #111;
    }
    .leaflet-control-attribution {
      font-size: 11px;
    }
  </style>
</head>
<body>
  <div id="map"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
          integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
          crossorigin=""></script>
  <script>
    // Kyiv as initial map center.
    const kyiv = [50.4501, 30.5234];
    const map = L.map("map", {
      zoomControl: true
    }).setView(kyiv, 12);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 20,
      attribution: "&copy; OpenStreetMap contributors"
    }).addTo(map);

    L.marker(kyiv).addTo(map).bindPopup("Київ (стартова позиція)").openPopup();
  </script>
</body>
</html>
"""


def main() -> None:
    window = webview.create_window(
        "DripCAD - Мапа (навігація)",
        html=HTML,
        width=1200,
        height=800,
        resizable=True,
    )
    webview.start()


if __name__ == "__main__":
    main()

