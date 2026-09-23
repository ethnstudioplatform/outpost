"""World land outlines for the map panel. Reads Natural Earth 110m (world-atlas topojson)."""
import json

from . import config

_polys = None


def land():
    """List of polygons, each a list of (lon, lat). Empty list if the data file is missing."""
    global _polys
    if _polys is not None:
        return _polys
    path = config.ROOT / "assets" / "land-110m.json"
    try:
        topo = json.loads(path.read_text())
    except Exception:
        _polys = []
        return _polys
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for a in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in a:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def arc(i):
        return arcs[i] if i >= 0 else arcs[~i][::-1]

    def ring(r):
        pts = []
        for k, i in enumerate(r):
            a = arc(i)
            pts.extend(a if k == 0 else a[1:])
        return pts

    obj = topo["objects"]["land"]
    geoms = obj["geometries"] if obj["type"] == "GeometryCollection" else [obj]
    polys = []
    for g in geoms:
        parts = [g["arcs"]] if g["type"] == "Polygon" else g["arcs"]
        for p in parts:
            polys.append(ring(p[0]))
    _polys = polys
    return _polys
