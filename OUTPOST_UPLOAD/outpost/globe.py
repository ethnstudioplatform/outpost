"""Opt-in 3D globe look ("style": "globe").

A real photographic Earth (NASA Blue Marble shaded relief and bathymetry, public domain, served as
WMTS tiles by NASA GIBS) wrapped on a sphere and seen through a moving perspective camera, with
a soft atmosphere rim, starfield and lighting. The story opens on the whole planet and dives in to
the first place; pins, labels and text are drawn in screen space by the main renderer as usual.

The renderer keeps its own camera logic: in globe mode its "texture coordinates" are simply
(lon, -lat) in degrees and a camera span is the view width in degrees of arc at the target.
"""
import io
import math
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageFilter

GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/{layer}/default/500m/{z}/{r}/{c}.jpeg"
LAYER = "BlueMarble_ShadedRelief_Bathymetry"
CACHE = Path(__file__).resolve().parent.parent / "assets" / "globe"
HFOV = math.radians(40)          # horizontal field of view
SCALE = 2                        # globe rendered at 1/2 res, then upscaled
ATMO = np.array([92, 160, 255], np.float32)
SPACE = np.array([3, 6, 16], np.float32)


def _deg_per_tile(z):
    return 288.0 / 2 ** z


def _fetch(z, r, c):
    p = CACHE / LAYER / str(z) / str(r) / f"{c}.jpg"
    if p.exists():
        try:
            return np.asarray(Image.open(p).convert("RGB"))
        except Exception:
            pass
    try:
        res = requests.get(GIBS.format(layer=LAYER, z=z, r=r, c=c), timeout=25,
                           headers={"User-Agent": "outpost-video/1.0"})
        res.raise_for_status()
        im = Image.open(io.BytesIO(res.content)).convert("RGB")
        p.parent.mkdir(parents=True, exist_ok=True)
        im.save(p, quality=92)
        return np.asarray(im)
    except Exception:
        return None


def _mosaic(z, lon0, lat0, lon1, lat1):
    """Tiles at level z covering the box. Returns (rgb array, west lon, north lat, deg per px) or None."""
    dpt = _deg_per_tile(z)
    ncol = int(math.ceil(360 / dpt))
    nrow = int(math.ceil(180 / dpt))
    c0 = max(0, int((lon0 + 180) // dpt)); c1 = min(ncol - 1, int((lon1 + 180) // dpt))
    r0 = max(0, int((90 - lat1) // dpt)); r1 = min(nrow - 1, int((90 - lat0) // dpt))
    jobs = [(z, r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]
    with ThreadPoolExecutor(12) as ex:
        tiles = list(ex.map(lambda a: _fetch(*a), jobs))
    if sum(t is None for t in tiles) > len(tiles) * 0.1:
        return None
    out = np.zeros(((r1 - r0 + 1) * 512, (c1 - c0 + 1) * 512, 3), np.uint8)
    for (zz, r, c), t in zip(jobs, tiles):
        if t is not None:
            out[(r - r0) * 512:(r - r0 + 1) * 512, (c - c0) * 512:(c - c0 + 1) * 512] = t[:512, :512]
    return out, -180 + c0 * dpt, 90 - r0 * dpt, dpt / 512


def _grade(a):
    """Lift the rather dark Blue Marble tiles: gamma, contrast and a little saturation."""
    f = a.astype(np.float32) / 255
    f = f ** 0.8
    m = f.mean(axis=2, keepdims=True)
    f = m + (f - m) * 1.25
    f = (f - 0.02) * 1.12
    return np.clip(f * 255, 0, 255).astype(np.float32)


def _synthetic(w=2048, h=1024):
    """Offline fallback: soft procedural continents on a blue sea (only used if NASA tiles fail)."""
    rng = np.random.default_rng(3)
    acc = np.zeros((h, w), np.float32)
    for o in range(6):
        n = 3 * 2 ** o
        g = rng.random((n + 1, 2 * n + 1)).astype(np.float32)
        acc += np.asarray(Image.fromarray((g * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC), np.float32) / 255 / 1.8 ** o
    acc /= acc.max()
    land = np.clip((acc - 0.55) * 12, 0, 1)[..., None]
    sea = np.array([14, 42, 92], np.float32)
    grn = np.array([58, 96, 52], np.float32) + (acc[..., None] - 0.55) * np.array([160, 90, 40], np.float32)
    return sea * (1 - land) + grn * land


def _bilinear(img, x, y):
    h, w = img.shape[:2]
    x0 = np.floor(x).astype(np.int32); y0 = np.floor(y).astype(np.int32)
    fx = (x - x0)[:, None]; fy = (y - y0)[:, None]
    x0 %= w; x1 = (x0 + 1) % w
    y0 = np.clip(y0, 0, h - 1); y1 = np.clip(y0 + 1, 0, h - 1)
    a = img[y0, x0]; b = img[y0, x1]; c = img[y1, x0]; d = img[y1, x1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def _vec(lon, lat):
    lo, la = math.radians(lon), math.radians(lat)
    return np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])


def _ll(v):
    v = v / np.linalg.norm(v)
    return math.degrees(math.atan2(v[1], v[0])), math.degrees(math.asin(max(-1, min(1, v[2]))))


class Globe:
    def __init__(self, r, W, H):
        self.r, self.W, self.H = r, W, H
        self.w, self.h = W // SCALE, H // SCALE
        self.tanh = math.tan(HFOV / 2)
        self.tanv = self.tanh * H / W
        ys, xs = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        self.sx = ((xs + 0.5) / self.w * 2 - 1).ravel() * self.tanh
        self.sy = (1 - (ys + 0.5) / self.h * 2).ravel() * self.tanv
        self._build_textures()
        self._build_stars()
        # keep captions and the hook banner readable over bright desert or ice
        yy = (np.arange(self.h, dtype=np.float32) + 0.5) / self.h
        top = 0.72 + 0.28 * np.clip(yy / 0.2, 0, 1)
        bot = 1 - 0.55 * np.clip((yy - 0.58) / 0.3, 0, 1)
        self.vshade = (top * bot)[:, None, None]

    # ---- imagery ----
    def _build_textures(self):
        g = None
        if os.environ.get("OUTPOST_GLOBE_OFFLINE") != "1":
            g = _mosaic(3, -180, -90, 180, 90)
        if g is None:
            print("[globe] NASA tiles unavailable, synthetic Earth")
            tex = _synthetic()
            self.src = "synthetic"
            self.base = (tex, -180.0, 90.0, 360.0 / tex.shape[1])
            self.detail = []
            return
        arr, lw, ln, dpp = g
        arr = arr[: int(round(180 / dpp)), : int(round(360 / dpp))]
        self.base = (_grade(arr), -180.0, 90.0, 360.0 / arr.shape[1])
        self.src = "nasa-gibs"
        # sharper level-6 tiles (about 1 km a pixel) around every named place
        self.detail = []
        for p in (self.r.places or {}).values():
            la, lo = p["lat"], p["lon"]
            dl = 7.0 / max(0.25, math.cos(math.radians(la)))
            m = _mosaic(6, lo - dl, max(-89, la - 7), lo + dl, min(89, la + 7))
            if m is not None:
                a, w0, n0, d = m
                self.detail.append((_grade(a), w0, n0, d))
        print(f"[globe] NASA Blue Marble {arr.shape[1]}x{arr.shape[0]} + {len(self.detail)} detail patches")

    def sample(self, lon, lat):
        img, w0, n0, d = self.base
        out = _bilinear(img, (lon - w0) / d - 0.5, (n0 - lat) / d - 0.5)
        for img, w0, n0, d in self.detail:
            h, w = img.shape[:2]
            x = (lon - w0) / d - 0.5
            y = (n0 - lat) / d - 0.5
            inside = (x > 1) & (x < w - 2) & (y > 1) & (y < h - 2)
            if not inside.any():
                continue
            edge = np.minimum.reduce([x, w - 2 - x, y, h - 2 - y])[inside]
            k = np.clip(edge / 40.0, 0, 1)[:, None]
            out[inside] = out[inside] * (1 - k) + _bilinear(img, x[inside], y[inside]) * k
        return out

    def _build_stars(self):
        rng = np.random.default_rng(11)
        st = np.zeros((self.h * 2, self.w * 2), np.float32)
        n = 900
        xs = rng.integers(0, st.shape[1], n); ys = rng.integers(0, st.shape[0], n)
        st[ys, xs] = rng.random(n) ** 3 * 255
        st = np.asarray(Image.fromarray(st.astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6)), np.float32)
        self.stars = st * 1.6

    # ---- camera ----
    def tilt_for(self, span):
        # close up: tilted, like looking ahead from a plane; wide: straight down at the globe
        f = (math.log(max(span, 4)) - math.log(4)) / (math.log(90) - math.log(4))
        return math.radians(38 * (1 - min(1.0, max(0.0, f))))

    def basis(self, cam):
        lon, lat = cam[0], -cam[1]
        span, yaw = cam[2], cam[3]
        P = _vec(lon, lat)
        up = np.array([0, 0, 1.0])
        E = np.cross(up, P); nE = np.linalg.norm(E)
        E = E / nE if nE > 1e-6 else np.array([0, 1.0, 0])
        N = np.cross(P, E)
        c, s = math.cos(yaw), math.sin(yaw)
        E, N = E * c + N * s, -E * s + N * c
        width = math.radians(min(span, 170))
        dist = width / (2 * self.tanh)
        th = self.tilt_for(span)
        C = P + dist * (math.cos(th) * P - math.sin(th) * N)
        F = P - C; F /= np.linalg.norm(F)
        R = np.cross(F, N); R /= np.linalg.norm(R)
        U = np.cross(R, F)
        # aim the target a little below the centre so labels have room above
        return C, F, R, U

    def project(self, lon, lat, cam):
        C, F, R, U = self.basis(cam)
        P = _vec(lon, lat)
        if np.dot(P, C - P) <= 0.02:          # behind the horizon
            return None
        v = P - C
        z = float(np.dot(v, F))
        if z <= 1e-4:
            return None
        u = float(np.dot(v, R)) / z / self.tanh
        w = float(np.dot(v, U)) / z / self.tanv
        return self.W / 2 + u * self.W / 2, self.H / 2 - w * self.H / 2, 1.0 / z

    def layer(self, cam, dim, BG):
        C, F, R, U = self.basis(cam)
        D = (F[None, :] + self.sx[:, None] * R[None, :] + self.sy[:, None] * U[None, :]).astype(np.float32)
        D /= np.linalg.norm(D, axis=1, keepdims=True)
        Cf = C.astype(np.float32)
        b = D @ Cf
        c = float(Cf @ Cf) - 1.0
        disc = b * b - c
        hit = (disc > 0) & (b < 0)
        out = np.empty((D.shape[0], 3), np.float32)
        # space: stars that slide gently with the heading, plus the atmosphere glow at the limb
        yaw_px = int((cam[0] * 3 + math.degrees(cam[3]) * 6) % self.w)
        lat_px = int((cam[1] * 2) % self.h)
        st = np.roll(np.roll(self.stars, -yaw_px, axis=1), -lat_px, axis=0)[: self.h, : self.w].ravel()
        out[:] = SPACE
        out += st[:, None] * 0.55
        closest = np.sqrt(np.maximum(c + 1.0 - b * b, 0)) - 1.0
        glow = np.exp(-np.maximum(closest, 0) / 0.045) * (b < 0)
        out += glow[:, None] * ATMO * 0.85
        if hit.any():
            t = -b[hit] - np.sqrt(disc[hit])
            X = Cf[None, :] + D[hit] * t[:, None]
            lat = np.degrees(np.arcsin(np.clip(X[:, 2], -1, 1)))
            lon = np.degrees(np.arctan2(X[:, 1], X[:, 0]))
            col = self.sample(lon, lat)
            ndv = np.clip(-(X * D[hit]).sum(1), 0, 1)
            sun = (0.35 * R - 0.25 * U - 0.9 * F)                 # light from over the viewer's left shoulder
            sun = (sun / np.linalg.norm(sun)).astype(np.float32)
            lam = np.clip(X @ sun, 0, 1)
            shade = 0.48 + 0.62 * lam
            col = col * shade[:, None]
            fres = (1 - ndv) ** 3
            col = col * (1 - fres[:, None] * 0.55) + fres[:, None] * ATMO * 0.75
            out[hit] = col
        out = out.reshape(self.h, self.w, 3) * self.vshade
        out = BG + (out - BG) * dim
        im = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
        return im.resize((self.W, self.H), Image.BICUBIC)

    # ---- framing ----
    def target(self, sc, places):
        allp = list(places.values())
        if not allp:
            return 0.0, -20.0, 120.0
        vs = np.array([_vec(p["lon"], p["lat"]) for p in allp])
        m = vs.mean(0)
        lon, lat = _ll(m)
        mv = _vec(lon, lat)
        ext = max(math.degrees(math.acos(max(-1, min(1, float(np.dot(mv, v)))))) for v in vs)
        span = max(30.0, ext * 2.5)
        if sc.get("type") != "wide":
            span *= 1.2
        return lon, -lat, span
