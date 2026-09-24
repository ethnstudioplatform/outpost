"""Country outlines for the map (Natural Earth 110m via world-atlas topojson)."""
import json
import re

from . import config

_countries = None

ALIASES = {
    "UNITED STATES": "UNITED STATES OF AMERICA", "USA": "UNITED STATES OF AMERICA", "US": "UNITED STATES OF AMERICA",
    "UK": "UNITED KINGDOM", "BRITAIN": "UNITED KINGDOM",
    "DR CONGO": "DEM. REP. CONGO", "DRC": "DEM. REP. CONGO", "DEMOCRATIC REPUBLIC OF THE CONGO": "DEM. REP. CONGO",
    "SOUTH SUDAN": "S. SUDAN", "GAZA": "PALESTINE", "WEST BANK": "PALESTINE", "GAZA STRIP": "PALESTINE",
    "CENTRAL AFRICAN REPUBLIC": "CENTRAL AFRICAN REP.", "BOSNIA": "BOSNIA AND HERZ.",
    "NORTH KOREA": "NORTH KOREA", "SOUTH KOREA": "SOUTH KOREA", "MYANMAR": "MYANMAR", "BURMA": "MYANMAR",
}


def countries():
    """List of (NAME_UPPER, [polygon rings as (lon, lat) lists]). Empty if data missing."""
    global _countries
    if _countries is not None:
        return _countries
    path = config.ROOT / "assets" / "countries-50m.json"
    if not path.exists():
        path = config.ROOT / "assets" / "countries-110m.json"
    try:
        topo = json.loads(path.read_text())
    except Exception:
        _countries = []
        return _countries
    tr = topo.get("transform")
    arcs = []
    for a in topo["arcs"]:
        pts, x, y = [], 0, 0
        for p in a:
            if tr:
                x += p[0]; y += p[1]
                pts.append((x * tr["scale"][0] + tr["translate"][0], y * tr["scale"][1] + tr["translate"][1]))
            else:
                pts.append((p[0], p[1]))
        arcs.append(pts)

    def ring(r):
        out = []
        for k, i in enumerate(r):
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            out.extend(a if k == 0 else a[1:])
        return out

    res = []
    for g in topo["objects"]["countries"]["geometries"]:
        name = str((g.get("properties") or {}).get("name", "")).upper()
        if g.get("type") == "Polygon":
            polys = [g["arcs"]]
        elif g.get("type") == "MultiPolygon":
            polys = g["arcs"]
        else:
            continue
        rings = [ring(p[0]) for p in polys]
        res.append((name, rings))
    _countries = res
    return _countries


def match_country(place_name: str):
    """'KHARKIV, UKRAINE' -> 'UKRAINE' if that country exists in the data."""
    if not place_name:
        return None
    names = {n for n, _ in countries()}
    parts = [p.strip() for p in re.split(r"[,/]", place_name.upper()) if p.strip()]
    for cand in reversed(parts):
        cand = ALIASES.get(cand, cand)
        if cand in names:
            return cand
    return None
