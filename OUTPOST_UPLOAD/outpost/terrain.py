"""Real elevation for the map: AWS Terrain Tiles (Terrarium PNG, public, free).
elevation_m = R*256 + G + B/256 - 32768. Returns None if tiles can't be fetched."""
import io
import math
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
from PIL import Image

URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def _merc(lon, lat, z):
    n = 256 * 2 ** z
    x = (lon + 180) / 360 * n
    lat = max(min(lat, 85), -85)
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def _tile(z, x, y):
    try:
        r = requests.get(URL.format(z=z, x=x, y=y), timeout=20)
        r.raise_for_status()
        a = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"), np.float32)
        return a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
    except Exception:
        return None


def heightmap(lon0, lat0, lon1, lat1, w, h):
    """Elevation (m) sampled on a regular lon/lat grid of w x h (row 0 = lat1, north)."""
    for z in range(9, 3, -1):
        x0, y0 = _merc(lon0, lat1, z)
        x1, y1 = _merc(lon1, lat0, z)
        tx0, ty0, tx1, ty1 = int(x0 // 256), int(y0 // 256), int(x1 // 256), int(y1 // 256)
        if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) <= 42:
            break
    coords = [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
    with ThreadPoolExecutor(8) as ex:
        tiles = list(ex.map(lambda c: _tile(z, c[0], c[1]), coords))
    ok = sum(t is not None for t in tiles)
    print(f"[terrain] zoom {z}: {ok}/{len(tiles)} tiles")
    if ok < len(tiles) * 0.7:
        return None
    mw, mh = (tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256
    mosaic = np.zeros((mh, mw), np.float32)
    for (tx, ty), t in zip(coords, tiles):
        if t is not None:
            mosaic[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = t
    lons = np.linspace(lon0, lon1, w)
    lats = np.linspace(lat1, lat0, h)
    px = np.array([_merc(lo, 0, z)[0] for lo in lons]) - tx0 * 256
    py = np.array([_merc(0, la, z)[1] for la in lats]) - ty0 * 256
    px = np.clip(px, 0, mw - 1.001)
    py = np.clip(py, 0, mh - 1.001)
    # bilinear sample
    X, Y = np.meshgrid(px, py)
    x0i, y0i = X.astype(int), Y.astype(int)
    fx, fy = X - x0i, Y - y0i
    a = mosaic[y0i, x0i]; b = mosaic[y0i, x0i + 1]; c = mosaic[y0i + 1, x0i]; d = mosaic[y0i + 1, x0i + 1]
    return (a * (1 - fx) * (1 - fy) + b * fx * (1 - fy) + c * (1 - fx) * fy + d * fx * fy).astype(np.float32)
