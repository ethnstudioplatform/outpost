"""OUTPOST v5 renderer: quiet green, tilted relief map, pinned sourced labels, dot grids.

The map is a real-terrain texture (AWS terrain tiles + Natural Earth coastlines) seen through a
slowly moving, pitched camera. Everything else (pins, routes, numbers, subtitles) is drawn in
screen space on top so text stays crisp.
"""
import math
import re
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config as C
from . import geo, terrain

W, H, FPS = C.W, C.H, C.FPS
BG = (4, 10, 7)
INK = (218, 255, 228)
G = (96, 236, 146)
GM = (74, 166, 110)
GD = (26, 66, 42)
RED = (255, 74, 62)     # waypoints only

LEAD = 0.45        # voice starts under the cover
GAP = 0.34         # breath between lines
SCENE_GAP = 0.22   # extra breath when the picture changes kind
OUTRO = 1.8
COVER_T = 1.35
PITCH = math.radians(40)
FOC = 1.6
HORIZ = 0.44
MAPSCALE = 2       # map layer rendered at 1/2 res then upscaled (soft, fast)

MAP_SCENES = {"pin", "route", "flow", "wide", "flight"}
PANEL_SCENES = {"number", "compare", "quote", "statement"}

# ---------------- fonts ----------------
_fc = {}


def font(kind, size):
    key = (kind, size)
    if key in _fc:
        return _fc[key]
    fd = C.FONT_DIR
    f = None
    try:
        if kind in ("sans", "sansr"):
            f = ImageFont.truetype(str(fd / "Inter-Var.ttf"), size)
            try:
                vals = []
                for ax in f.get_variation_axes():
                    nm = ax.get("name", b"")
                    nm = nm.decode() if isinstance(nm, bytes) else str(nm)
                    if "eight" in nm:
                        vals.append(700 if kind == "sans" else 420)
                    elif "ptical" in nm:
                        vals.append(max(ax["minimum"], min(ax["maximum"], 32)))
                    else:
                        vals.append(ax.get("default", ax["minimum"]))
                f.set_variation_by_axes(vals)
            except Exception:
                pass
        else:
            f = ImageFont.truetype(str(fd / "IBMPlexMono-Regular.ttf"), size)
    except Exception:
        f = None
    if f is None:
        path = {"sans": "DejaVuSans-Bold.ttf", "sansr": "DejaVuSans.ttf", "mono": "DejaVuSansMono.ttf"}[kind]
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/" + path, size)
    _fc[key] = f
    return f


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def rgba(c, a):
    return (c[0], c[1], c[2], int(max(0, min(1, a)) * 255))


def wrap(text, f, width):
    rows, cur = [], ""
    for w in str(text).split():
        if cur and f.getlength(cur + " " + w) > width:
            rows.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        rows.append(cur)
    return rows


# ---------------- timeline + sound cues ----------------
def build_timeline(line_durs, scenes=None):
    scenes = scenes or [{"type": "wide"}] * len(line_durs)
    tl, t = [], LEAD
    for i, d in enumerate(line_durs):
        tl.append((t, t + d))
        gap = GAP
        if i + 1 < len(line_durs) and (scenes[i]["type"] in MAP_SCENES) != (scenes[i + 1]["type"] in MAP_SCENES):
            gap += SCENE_GAP
        t += d + gap
    return tl, tl[-1][1] + OUTRO


def sfx_events(script, timeline):
    ev = []
    prev = None
    for ln, (st, _) in zip(script["lines"], timeline):
        k = ln["scene"]["type"]
        if k in ("pin", "route", "flow"):
            ev.append((st + 0.3, "tick"))
        if k in ("number", "compare"):
            ev.append((st + 0.15, "pulse"))
        if prev and prev != k:
            ev.append((max(0, st - 0.4), "swell"))
        prev = k
    return ev


# ---------------- renderer ----------------
class Renderer:
    def __init__(self, script, timeline, total):
        self.s = script
        self.tl = timeline
        self.total = total
        self.lines = script["lines"]
        self.places = script.get("places") or {}
        self.notes = []
        self._build_texture()
        self._build_rays()
        self._build_cams()
        self._build_post()
        self._build_flags()

    # ---- map texture ----
    def _build_texture(self):
        pl = list(self.places.values())
        pl += [ln["scene"]["from"] for ln in self.lines
               if ln["scene"].get("type") == "flight" and isinstance(ln["scene"].get("from"), dict)]
        if pl:
            lons = [p["lon"] for p in pl]; lats = [p["lat"] for p in pl]
        else:
            lons, lats = [35.0], [32.0]
        ext = max(max(lons) - min(lons), max(lats) - min(lats), 1.5)
        pad = max(6.5, min(ext * 2.0, 24))
        self.latc = (max(lats) + min(lats)) / 2
        self.cosc = math.cos(math.radians(self.latc))
        lon0, lon1 = min(lons) - pad / self.cosc, max(lons) + pad / self.cosc
        lat0, lat1 = max(-80, min(lats) - pad), min(80, max(lats) + pad)
        self.bbox = (lon0, lat0, lon1, lat1)
        k = 3400 / ((lon1 - lon0) * self.cosc)
        TH = (lat1 - lat0) * k
        if TH > 4200:
            k *= 4200 / TH
        self.k = k
        TW, TH = int((lon1 - lon0) * self.cosc * k), int((lat1 - lat0) * k)
        self.TW, self.TH = TW, TH

        # land mask + outlines
        mask = Image.new("L", (TW, TH), 0)
        md = ImageDraw.Draw(mask)
        lines_img = Image.new("L", (TW, TH), 0)
        ld = ImageDraw.Draw(lines_img)
        n_poly = 0
        for _, rings in geo.countries():
            for r in rings:
                if not r:
                    continue
                xs = [p[0] for p in r]; ys = [p[1] for p in r]
                if max(xs) < lon0 - 1 or min(xs) > lon1 + 1 or max(ys) < lat0 - 1 or min(ys) > lat1 + 1:
                    continue
                pts = [self.T(x, y) for x, y in r]
                if len(pts) >= 3:
                    md.polygon(pts, fill=255)
                    ld.line(pts + [pts[0]], fill=255, width=3)
                    n_poly += 1
        land = np.asarray(mask, np.float32) / 255
        print(f"[render] map {TW}x{TH}, {n_poly} coastline rings")

        # elevation
        hm = None
        try:
            hm = terrain.heightmap(lon0, lat0, lon1, lat1, TW // 2, TH // 2)
        except Exception as e:
            print(f"[render] terrain failed: {e}")
        if hm is not None:
            hm = np.asarray(Image.fromarray(hm).resize((TW, TH), Image.BICUBIC), np.float32)
            self.terrain_src = "real"
            if n_poly == 0:
                land = (hm > 0).astype(np.float32)
        else:
            self.terrain_src = "synthetic"
            hm = self._synthetic_height(mask) * 2500
        hm = np.where(land > 0.5, np.maximum(hm, 0), 0)
        m_per_px = 111320 / k
        gy, gx = np.gradient(hm / m_per_px)
        shade = np.clip(0.52 + (-gx * 0.62 - gy * 0.78) * 2.4, 0, 1)
        en = np.clip(hm / max(600.0, float(np.percentile(hm[land > 0.5], 98)) if (land > 0.5).any() else 1), 0, 1)
        tex = np.zeros((TH, TW, 3), np.float32)
        for c in range(3):
            tex[..., c] = BG[c] + land * (shade * (22, 64, 38)[c] + (5, 12, 8)[c] + en * (6, 16, 10)[c])
        # faint graticule over the sea
        gimg = Image.new("L", (TW, TH), 0)
        gd = ImageDraw.Draw(gimg)
        for lo in range(int(math.floor(lon0)), int(math.ceil(lon1)) + 1):
            gd.line([self.T(lo, lat0), self.T(lo, lat1)], fill=255, width=2)
        for la in range(int(math.floor(lat0)), int(math.ceil(lat1)) + 1):
            gd.line([self.T(lon0, la), self.T(lon1, la)], fill=255, width=2)
        grid = np.asarray(gimg, np.float32)[..., None] / 255 * (1 - land[..., None])
        tex += grid * np.array([10, 26, 17], np.float32)
        ol = np.asarray(lines_img.filter(ImageFilter.GaussianBlur(0.8)), np.float32)[..., None] / 255
        tex = tex * (1 - ol * 0.8) + ol * 0.8 * np.array([70, 150, 100], np.float32)
        self.tex = np.clip(tex, 0, 255).astype(np.uint8)

    def _synthetic_height(self, mask):
        rng = np.random.default_rng(5)
        TW, TH = self.TW, self.TH
        out = np.zeros((TH, TW), np.float32)
        for o in range(6):
            n = 4 * 2 ** o
            g = rng.random((n + 1, n + 1)).astype(np.float32)
            out += np.asarray(Image.fromarray((g * 255).astype(np.uint8)).resize((TW, TH), Image.BICUBIC), np.float32) / 255 / 1.9 ** o
        out /= out.max()
        ridge = 1 - np.abs(2 * out - 1)
        inland = np.asarray(mask.filter(ImageFilter.GaussianBlur(120)), np.float32) / 255
        return np.clip(inland - 0.45, 0, 1) * 1.8 * (0.2 + ridge ** 2)

    def T(self, lon, lat):
        lon0, lat0, lon1, lat1 = self.bbox
        return ((lon - lon0) * self.cosc * self.k, (lat1 - lat) * self.k)

    # ---- camera ----
    def _build_rays(self):
        w, h = W // MAPSCALE, H // MAPSCALE
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        u = (xs * MAPSCALE - W / 2) / W
        v = (ys * MAPSCALE - H * HORIZ) / W
        den = FOC * math.cos(PITCH) - v * math.sin(PITCH)
        self.valid = den > 0.06
        den = np.where(self.valid, den, 1)
        self.RX = (u * FOC / den).astype(np.float32)
        self.RY = (v * FOC / den / math.cos(PITCH)).astype(np.float32)
        fade = np.clip((ys * MAPSCALE - H * 0.02) / (H * 0.36), 0, 1)
        self.fade = (fade * self.valid)[..., None].astype(np.float32)

    def _deg(self, d):
        return d * self.k

    def _target(self, sc):
        pl = self.places
        allp = list(pl.values())
        def center(ps):
            xs = [self.T(p["lon"], p["lat"]) for p in ps]
            cx = (min(x for x, _ in xs) + max(x for x, _ in xs)) / 2
            cy = (min(y for _, y in xs) + max(y for _, y in xs)) / 2
            ext = max(max(x for x, _ in xs) - min(x for x, _ in xs), max(y for _, y in xs) - min(y for _, y in xs))
            return cx, cy, ext
        if not allp:
            return self.TW / 2, self.TH / 2, self.TW * 0.45
        t = sc["type"]
        if t == "pin":
            p = pl[sc["place"]]
            x, y = self.T(p["lon"], p["lat"])
            return x, y, self._deg(2.1)
        if t == "route":
            cx, cy, ext = center([pl[sc["from"]], pl[sc["to"]]])
            return cx, cy - ext * 0.1, max(ext * 1.7, self._deg(1.8))
        if t == "flow":
            cx, cy, ext = center([pl[sc["from"]]] + [pl[x] for x in sc["to"]])
            return cx, cy, max(ext * 1.6, self._deg(2.2))
        if t == "flight":
            _, _, ext = center([sc["from"], pl[sc["to"]]])
            ax, ay = self.T(sc["from"]["lon"], sc["from"]["lat"])
            bx, by = self.T(pl[sc["to"]]["lon"], pl[sc["to"]]["lat"])
            return ax + (bx - ax) * 0.58, ay + (by - ay) * 0.5, max(ext * 1.35, self._deg(1.8))
        cx, cy, ext = center(allp)
        if t == "wide":
            return cx, cy, max(ext * 1.45, self._deg(2.8))
        return cx, cy + ext * 0.2, max(ext * 1.8, self._deg(3.6))   # panels: pulled back, map as backdrop

    def _build_cams(self):
        yaws = [-7, 5, -3, 7, -5, 3, -8, 6]
        self.cams = []
        for i, ln in enumerate(self.lines):
            cx, cy, span = self._target(ln["scene"])
            yaw = 0 if ln["scene"]["type"] == "flight" else yaws[i % len(yaws)]
            self.cams.append((cx, cy, span, yaw))
        if not self.cams:
            self.cams = [(self.TW / 2, self.TH / 2, self.TW * 0.4, 0)]

    def _line_at(self, t):
        i = 0
        for k, (st, _) in enumerate(self.tl):
            if t >= st - 0.25:
                i = k
        return i

    def camera(self, t):
        i = self._line_at(t)
        st = self.tl[i][0] if self.tl else 0
        cur = self.cams[i]
        if i == 0:
            prev = (cur[0], cur[1] + cur[2] * 0.25, cur[2] * 1.35, cur[3] - 6)
            e = ease(t / 2.2)
        else:
            prev = self._cam_final(i - 1)
            e = ease((t - st + 0.25) / 1.7)
        cx = prev[0] + (cur[0] - prev[0]) * e
        cy = prev[1] + (cur[1] - prev[1]) * e
        span = math.exp(math.log(prev[2]) + (math.log(cur[2]) - math.log(prev[2])) * e)
        yaw = prev[3] + (cur[3] - prev[3]) * e
        loc = max(0.0, t - st)
        span *= 1 - 0.014 * min(loc, 8)
        yaw += 1.6 * math.sin(t * 0.21)
        return cx, cy, span, math.radians(yaw)

    def _cam_final(self, i):
        cx, cy, span, yaw = self.cams[i]
        st, en = self.tl[i]
        nxt = self.tl[i + 1][0] if i + 1 < len(self.tl) else en
        loc = max(0.0, nxt - 0.25 - st)
        return cx, cy, span * (1 - 0.014 * min(loc, 8)), yaw

    def project(self, lon, lat, cam):
        cx, cy, span, yaw = cam
        tx, ty = self.T(lon, lat)
        dx, dy = (tx - cx) / span, (ty - cy) / span
        c, s = math.cos(yaw), math.sin(yaw)
        X = dx * c + dy * s
        Y = -dx * s + dy * c
        Yc = Y * math.cos(PITCH)
        denom = FOC + Yc * math.sin(PITCH)
        if denom <= 0.05:
            return None
        v = Yc * FOC * math.cos(PITCH) / denom
        den = FOC * math.cos(PITCH) - v * math.sin(PITCH)
        if den <= 0.06:
            return None
        u = X * den / FOC
        return W / 2 + u * W, H * HORIZ + v * W, FOC * math.cos(PITCH) / den

    def map_layer(self, cam, dim):
        cx, cy, span, yaw = cam
        c, s = math.cos(yaw), math.sin(yaw)
        tx = (cx + (self.RX * c - self.RY * s) * span).astype(np.int32)
        ty = (cy + (self.RX * s + self.RY * c) * span).astype(np.int32)
        ok = self.valid & (tx >= 0) & (tx < self.TW) & (ty >= 0) & (ty < self.TH)
        out = np.empty(tx.shape + (3,), np.float32)
        out[:] = BG
        out[ok] = self.tex[ty[ok], tx[ok]]
        out = BG + (out - BG) * self.fade * dim
        im = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
        return im.resize((W, H), Image.BILINEAR)

    # ---- post ----
    def _build_post(self):
        yy = (np.arange(H)[:, None] - H / 2) / (H / 2)
        xx = (np.arange(W)[None, :] - W / 2) / (W / 2)
        vig = np.clip(1 - 0.32 * (xx ** 2 + 0.6 * yy ** 2), 0.5, 1).astype(np.float32)
        rng = np.random.default_rng(9)
        self.post = [(vig * rng.normal(1, 0.03, (H, W)).astype(np.float32))[..., None] for _ in range(6)]

    def finish(self, im, fi):
        small = im.resize((W // 6, H // 6), Image.BILINEAR).filter(ImageFilter.GaussianBlur(3))
        arr = np.asarray(im, np.float32) + np.asarray(small.resize((W, H), Image.BILINEAR), np.float32) * 0.28
        arr *= self.post[fi % len(self.post)]
        return np.clip(arr, 0, 255).astype(np.uint8)

    # ---- overlay pieces ----
    def revealed(self, t):
        ids = []
        for ln, (st, _) in zip(self.lines, self.tl):
            if t < st + 0.2:
                break
            sc = ln["scene"]
            for k in ("place", "from"):
                if sc.get(k):
                    ids.append(sc[k])
            if sc.get("to"):
                ids += sc["to"] if isinstance(sc["to"], list) else [sc["to"]]
        return list(dict.fromkeys(i for i in ids if isinstance(i, str) and i in self.places))

    def marker(self, d, p, cam, a, name=True, big=False, red=False):
        pr = self.project(p["lon"], p["lat"], cam)
        if not pr:
            return None
        x, y, sc = pr
        if not (-100 < x < W + 100 and -100 < y < H + 100):
            return (x, y)
        r = 9 if big else 6
        if red:
            r = 13
            d.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)], fill=rgba(RED, a))
            for sx in (-1, 1):
                d.line([(x + sx * (r + 6), y), (x + sx * (r + 18), y)], fill=rgba(RED, a * 0.8), width=3)
        else:
            d.rectangle([x - r, y - r, x + r, y + r], fill=rgba(INK, a))
        if name:
            d.text((x + 16, y + 10), p["name"].upper(), font=font("mono", 26), fill=rgba(INK if big else GM, a * 0.95))
        return (x, y)

    def rings(self, d, x, y, t, a, col=RED):
        for k in range(2):
            ph = (t * 0.45 + k * 0.5) % 1
            r = 22 + ph * 70
            d.ellipse([x - r, y - r * 0.62, x + r, y + r * 0.62], outline=rgba(col, a * (1 - ph)), width=3)

    def waypoints(self, d, pts, a, n=3):
        """Small red waypoint ticks along a projected path."""
        if len(pts) < 4:
            return
        for j in range(1, n + 1):
            q = pts[int(j / (n + 1) * (len(pts) - 1))]
            d.rectangle([q[0] - 6, q[1] - 6, q[0] + 6, q[1] + 6], outline=rgba(RED, a), width=3)

    def label_block(self, d, ax, ay, head, notes, src, a, slide):
        fh, fn = font("sans", 64), font("mono", 29)
        rows = [(head, fh, INK)] if head else []
        rows += [(n, fn, GM) for n in notes if n]
        if src:
            rows.append((src, font("mono", 25), GM))
        if not rows:
            return
        wmax = max(f.getlength(r) for r, f, _ in rows)
        hsum = sum((f.size + 12) for _, f, _ in rows)
        right = ax < W * 0.55
        bx = ax + 46 if right else ax - 46 - wmax
        bx = max(56, min(W - 150 - wmax, bx))
        by = ay - 70 - hsum
        by = max(250, min(1250 - hsum, by)) + (1 - slide) * 24
        d.line([(ax, ay - 14), (bx + (0 if right else wmax), by + hsum + 6)], fill=rgba(GM, a * 0.6), width=2)
        y = by
        for r, f, col in rows:
            d.text((bx, y), r, font=f, fill=rgba(col, a))
            y += f.size + 12

    def dots(self, d, x, y, n, cols, unit, col, a, prog, size=16, gap=6):
        squares = int(round(n / unit))
        lit = int(squares * prog)
        for i in range(lit):
            r, c = divmod(i, cols)
            px, py = x + c * (size + gap), y - r * (size + gap)
            d.rectangle([px, py, px + size, py + size], fill=rgba(col, a))
        return squares

    @staticmethod
    def unit_for(n, max_sq=420):
        for u in [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000,
                  100000, 200000, 500000, 1e6, 2e6, 5e6, 1e7]:
            if n / u <= max_sq:
                return int(u)
        return int(1e8)

    @staticmethod
    def count(value, prog):
        nums = re.findall(r"\d[\d,]*\.?\d*", value)
        if not nums or prog >= 1:
            return value
        raw = nums[0]
        n = float(raw.replace(",", ""))
        cur = n * ease(prog)
        dec = len(raw.split(".")[1]) if "." in raw else 0
        s = f"{cur:,.{dec}f}" if "," in raw else f"{cur:.{dec}f}"
        return value.replace(raw, s, 1)

    def subtitle(self, d, text, a):
        f = font("sansr", 44)
        rows = wrap(text, f, 860)
        y0 = 1470 - len(rows) * 58 / 2
        for i, r in enumerate(rows):
            d.text(((W - f.getlength(r)) / 2, y0 + i * 58), r, font=f, fill=rgba(INK, a),
                   stroke_width=3, stroke_fill=rgba(BG, a * 0.6))

    def big_value(self, d, text, y, a, maxw=900, size=150):
        f = font("sans", size)
        while f.getlength(text) > maxw and size > 60:
            size -= 8
            f = font("sans", size)
        d.text((78, y), text, font=f, fill=rgba(INK, a))
        self.accent_bar(d, 84, y + size * 1.12, a)
        return size

    def accent_bar(self, d, x, y, a, w=210):
        """Thin tricolour rule in the story's accent colours (e.g. US red/white/blue)."""
        if not self.accents:
            return
        seg = w / len(self.accents)
        for j, c in enumerate(self.accents):
            d.rectangle([x + j * seg, y, x + (j + 1) * seg - 6, y + 7], fill=rgba(c, a))


    # ---- flag view (detail scenes) ----
    def _build_flags(self):
        """Load each needed flag as a small luminance grid for the green halftone flag."""
        self.flags = {}
        self.flag_rgb = {}
        self.accents = [tuple(int(v) for v in c) for c in (self.s.get("accents") or [])][:3]
        codes = {str(self.s.get("country") or "").upper()}
        codes |= {str(l["scene"].get("flag") or "").upper() for l in self.lines}
        for code in codes:
            if not re.fullmatch(r"[A-Z]{2}", code):
                continue
            img = None
            local = C.ROOT / "assets" / "flags" / f"{code.lower()}.png"
            try:
                if local.exists():
                    img = Image.open(local)
                else:
                    import io
                    import requests
                    r = requests.get(f"https://flagcdn.com/w320/{code.lower()}.png", timeout=15)
                    r.raise_for_status()
                    img = Image.open(io.BytesIO(r.content))
            except Exception as e:
                print(f"[render] flag {code} unavailable: {e}")
                continue
            img = img.convert("RGB")
            cols = 46
            rows = max(18, min(34, int(round(cols * img.height / img.width))))
            g = np.asarray(img.resize((cols, rows), Image.BOX), np.float32) / 255
            # luminance with a push so red/green/blue fields stay distinct from each other
            lum = 0.42 * g[..., 0] + 0.45 * g[..., 1] + 0.13 * g[..., 2]
            lo, hi = float(lum.min()), float(lum.max())
            lum = (lum - lo) / (hi - lo) if hi - lo > 0.05 else lum * 0 + 0.6
            self.flags[code] = lum
            self.flag_rgb[code] = g
        print(f"[render] flags: {sorted(self.flags)}")

    def flag_for(self, sc):
        c = str(sc.get("flag") or self.s.get("country") or "").upper()
        return c if c in self.flags else None

    def draw_flag(self, d, code, t, a):
        lum = self.flags[code]
        rows, cols = lum.shape
        pitch = 24
        fw, fh = cols * pitch, rows * pitch
        x0, y0 = (W - fw) / 2 + 6, 990 - fh / 2
        for r in range(rows):
            for c in range(cols):
                ph = c * 0.32 - t * 2.4 + r * 0.05
                wave = math.sin(ph)
                shade = 0.72 + 0.28 * math.cos(ph)          # light rolling across the folds
                v = lum[r, c]
                rad = (2.2 + v * 8.6) * (0.9 + 0.1 * shade)
                x = x0 + c * pitch + wave * 3
                y = y0 + r * pitch + wave * 13 + (c / cols) * 18
                if self.accents and code in self.flag_rgb:
                    rgb = self.flag_rgb[code][r, c]
                    col = tuple(int(min(255, rgb[i] * 255 * shade * 0.9 + 18)) for i in range(3))
                    rad = 7.5 * (0.9 + 0.1 * shade)
                    d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=rgba(col, a * 0.34))
                    continue
                col = tuple(int(GD[i] + (G[i] - GD[i]) * (0.25 + 0.75 * v) * shade) for i in range(3))
                d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=rgba(col, a * (0.2 + 0.32 * v)))


    # ---- special animations ----
    def plane(self, d, x, y, ang, a, size=34):
        """Top-down aircraft silhouette, nose along angle `ang` (radians)."""
        pts = [(1.0, 0), (0.55, 0.08), (0.1, 0.1), (-0.15, 0.62), (-0.32, 0.62), (-0.2, 0.1), (-0.72, 0.08),
               (-0.9, 0.3), (-1.0, 0.3), (-0.92, 0), (-1.0, -0.3), (-0.9, -0.3), (-0.72, -0.08), (-0.2, -0.1),
               (-0.32, -0.62), (-0.15, -0.62), (0.1, -0.1), (0.55, -0.08)]
        c, s_ = math.cos(ang), math.sin(ang)
        poly = [(x + (px * c - py * s_) * size, y + (px * s_ + py * c) * size) for px, py in pts]
        d.polygon(poly, fill=rgba(INK, a))
        d.ellipse([x + c * size * 0.8 - 4, y + s_ * size * 0.8 - 4, x + c * size * 0.8 + 4, y + s_ * size * 0.8 + 4],
                  fill=rgba(RED, a))

    def person(self, d, x, y, h, phase, col, a, walking=True):
        """Pictogram figure standing at (x, y=feet). Legs and arms swing while walking."""
        head = h * 0.13
        d.ellipse([x - head, y - h, x + head, y - h + head * 2], fill=rgba(col, a))
        neck, hip = y - h + head * 2.2, y - h * 0.45
        w = max(3, int(h * 0.07))
        d.line([(x, neck), (x, hip)], fill=rgba(col, a), width=w)
        sw = math.sin(phase) * (0.35 if walking else 0)
        for sgn in (1, -1):
            d.line([(x, hip), (x + sgn * sw * h * 0.5, y)], fill=rgba(col, a), width=w)
            d.line([(x, neck + h * 0.05), (x - sgn * sw * h * 0.35, hip + h * 0.05)], fill=rgba(col, a), width=w)

    def hall(self, d, sc, loc, dur, t, a):
        """Schematic assembly hall: podium, curved rows of seats, an exit, and delegates walking out."""
        cx, top = W / 2, 520
        d.rectangle([cx - 120, top, cx + 120, top + 46], outline=rgba(GM, a), width=3)
        d.text((cx - font("mono", 24).getlength("PODIUM") / 2, top + 10), "PODIUM", font=font("mono", 24), fill=rgba(GM, a))
        seats = []
        for r in range(5):
            rad = 230 + r * 78
            n = 9 + r * 3
            for k in range(n):
                ang = math.radians(200 + (140 * k / (n - 1)))
                seats.append((cx + math.cos(ang) * rad * 1.05, top + 40 - math.sin(ang) * rad * 0.9))
        walkers = max(1, min(4, int(sc.get("count", 1))))
        chosen = [len(seats) - 6 - j * 2 for j in range(walkers)]
        door = (W - 120, 1210)
        d.rectangle([door[0] - 40, door[1] - 150, door[0] + 40, door[1]], outline=rgba(RED, a), width=4)
        d.text((door[0] - 30, door[1] + 10), "EXIT", font=font("mono", 26), fill=rgba(RED, a))
        for i_, (sx, sy) in enumerate(seats):
            if i_ in chosen:
                d.rectangle([sx - 9, sy - 9, sx + 9, sy + 9], outline=rgba(RED, a * 0.8), width=2)
            else:
                d.rectangle([sx - 8, sy - 8, sx + 8, sy + 8], fill=rgba(GM, a * 0.7))
        for j, idx in enumerate(chosen):
            sx, sy = seats[idx]
            p = ease((loc - 0.3 - j * 0.35) / max(1.2, dur * 0.8))
            if p <= 0:
                self.person(d, sx, sy + 6, 70, 0, RED, a, walking=False)
                continue
            # path: seat -> aisle below the rows -> door
            ay = 1210
            if p < 0.35:
                q = p / 0.35
                x, y = sx, sy + 6 + (ay - sy - 6) * q
            else:
                q = (p - 0.35) / 0.65
                x, y = sx + (door[0] - sx) * q, ay
            fade = 1 - ease((p - 0.9) / 0.1)
            self.person(d, x, y, 96, t * 11 + j, RED, a * fade)
        head = sc.get("label", "")
        if head:
            f = font("sans", 64)
            size = 64
            while f.getlength(head) > 940 and size > 40:
                size -= 4; f = font("sans", size)
            d.text((70, 300), head, font=f, fill=rgba(INK, a))
            self.accent_bar(d, 76, 300 + size * 1.15, a)

    # ---- one frame ----
    def frame(self, t, fi):
        i = self._line_at(t)
        ln = self.lines[i] if self.lines else {"scene": {"type": "wide"}, "text": "", "note": ""}
        sc = ln["scene"]
        st, en = self.tl[i] if self.tl else (0, 1)
        loc = t - st
        cam = self.camera(t)
        # map dim: panels push the map back
        def dim_of(scn):
            k_ = scn["type"]
            if k_ in PANEL_SCENES and self.flag_for(scn):
                return 0.16
            if k_ == "walkout":
                return 0.14
            return {"number": 0.5, "compare": 0.42, "quote": 0.36, "statement": 0.62}.get(k_, 1.0)
        dim = dim_of(sc)
        swap = ease((loc + 0.25) / 0.8)
        if i > 0:
            pd = dim_of(self.lines[i - 1]["scene"])
            dim = pd + (dim - pd) * swap
        if t < COVER_T and self.s.get("cover"):
            dim *= 0.8
        im = self.map_layer(cam, dim).convert("RGBA")
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        a_in = ease((loc - 0.1) / 0.45)
        a_out = 1 - ease((t - (en + 0.25)) / 0.3) if i + 1 < len(self.lines) else 1.0
        a = a_in * a_out
        if self.s.get("cover") and i == 0:
            gate = ease((t - COVER_T) / 0.35)
            a *= gate
            a_in *= gate
        slide = ease((loc - 0.1) / 0.5)
        map_a = 1.0 if sc["type"] in MAP_SCENES else 0.45

        # detail scenes: cut from the map to a waving green flag of the country
        fc = self.flag_for(sc) if sc["type"] in PANEL_SCENES else None
        prev_sc = self.lines[i - 1]["scene"] if i > 0 else None
        pfc = self.flag_for(prev_sc) if prev_sc and prev_sc["type"] in PANEL_SCENES else None
        if fc and not (t < COVER_T + 0.3 and self.s.get("cover")):
            fa = swap if pfc != fc else 1.0
            self.draw_flag(d, fc, t, fa)
            map_a = 0.0
        if pfc and pfc != fc and swap < 1:
            self.draw_flag(d, pfc, t, 1 - swap)

        # context markers for places already mentioned
        active = set()
        if sc["type"] == "pin":
            active = {sc["place"]}
        elif sc["type"] == "route":
            active = {sc["from"], sc["to"]}
        elif sc["type"] == "flow":
            active = {sc["from"], *sc["to"]}
        for pid in self.revealed(t):
            if pid not in active:
                self.marker(d, self.places[pid], cam, map_a * 0.9, name=sc["type"] in MAP_SCENES)

        k = sc["type"]
        if k == "pin":
            p = self.places[sc["place"]]
            pos = self.marker(d, p, cam, max(a_in, 0.3), name=False, big=True, red=True)
            if pos:
                self.rings(d, pos[0], pos[1], t, a_in)
                self.label_block(d, pos[0], pos[1], sc.get("head") or p["name"], sc.get("notes", []),
                                 ln.get("note"), a, slide)
        elif k == "route":
            A, B = self.places[sc["from"]], self.places[sc["to"]]
            prog = ease((loc - 0.15) / 1.0)
            pts = []
            for j in range(41):
                f_ = j / 40 * prog
                pr = self.project(A["lon"] + (B["lon"] - A["lon"]) * f_, A["lat"] + (B["lat"] - A["lat"]) * f_, cam)
                if pr:
                    pts.append(pr[:2])
            for j in range(0, len(pts) - 1, 2):
                d.line([pts[j], pts[j + 1]], fill=rgba(INK, a_in), width=6)
            self.waypoints(d, pts, a_in, 3)
            pa = self.marker(d, A, cam, a_in, big=True, red=True)
            pb = self.marker(d, B, cam, a_in if prog > 0.95 else 0, big=True, red=True)
            if pa and pb:
                mx, my = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
                if sc.get("style") == "cut" and loc > 1.1:
                    e = ease((loc - 1.1) / 0.3) * 24
                    d.line([(mx - e, my - e), (mx + e, my + e)], fill=rgba(RED, a), width=7)
                    d.line([(mx - e, my + e), (mx + e, my - e)], fill=rgba(RED, a), width=7)
                if sc.get("style") == "attack" and pts:
                    ph = (t * 0.6) % 1
                    q = pts[min(len(pts) - 1, int(ph * (len(pts) - 1)))]
                    d.ellipse([q[0] - 9, q[1] - 9, q[0] + 9, q[1] + 9], fill=rgba(G, a))
                self.label_block(d, mx, my, sc.get("label"), [sc.get("note")], ln.get("note"), a, slide)
        elif k == "flow":
            A = self.places[sc["from"]]
            pa = self.marker(d, A, cam, a_in, big=True, red=True)
            if pa:
                self.rings(d, pa[0], pa[1], t, a_in * 0.8)
            for j, dest in enumerate(sc["to"]):
                B = self.places[dest]
                # curved path in geo space
                mlon = (A["lon"] + B["lon"]) / 2 + (B["lat"] - A["lat"]) * 0.18
                mlat = (A["lat"] + B["lat"]) / 2 - (B["lon"] - A["lon"]) * 0.18
                def bez(u):
                    return ((1 - u) ** 2 * A["lon"] + 2 * (1 - u) * u * mlon + u * u * B["lon"],
                            (1 - u) ** 2 * A["lat"] + 2 * (1 - u) * u * mlat + u * u * B["lat"])
                grow = ease((loc - 0.2 - j * 0.25) / 0.9)
                pts = [self.project(*bez(u / 30 * grow), cam) for u in range(31)]
                pts = [p[:2] for p in pts if p]
                if len(pts) > 1:
                    d.line(pts, fill=rgba(GM, a_in * 0.8), width=3)
                    self.waypoints(d, pts, a_in * grow, 2)
                for m in range(7):
                    u = ((t * 0.28 + m / 7 + j * 0.13) % 1) * grow
                    pr = self.project(*bez(u), cam)
                    if pr:
                        d.ellipse([pr[0] - 7, pr[1] - 7, pr[0] + 7, pr[1] + 7], fill=rgba(INK, a_in * 0.9))
                self.marker(d, B, cam, a_in * grow, big=True, red=True)
            if pa:
                self.label_block(d, pa[0], pa[1], sc.get("label"), [], ln.get("note"), a, slide)
        elif k == "flight":
            B = self.places[sc["to"]]
            A = sc["from"]
            prog = ease((loc + 0.2) / max(1.6, (en - st) * 0.8))
            mlon = (A["lon"] + B["lon"]) / 2 - (B["lat"] - A["lat"]) * 0.25
            mlat = (A["lat"] + B["lat"]) / 2 + (B["lon"] - A["lon"]) * 0.25
            def bez(u):
                return ((1 - u) ** 2 * A["lon"] + 2 * (1 - u) * u * mlon + u * u * B["lon"],
                        (1 - u) ** 2 * A["lat"] + 2 * (1 - u) * u * mlat + u * u * B["lat"])
            pts = [self.project(*bez(u / 60 * prog), cam) for u in range(61)]
            pts = [p[:2] for p in pts if p]
            for j in range(0, len(pts) - 1, 2):
                d.line([pts[j], pts[j + 1]], fill=rgba(INK, 0.75), width=6)
            pb = self.marker(d, B, cam, 1.0 if prog > 0.97 else 0.5, name=False, big=True, red=True)
            if pb:
                self.rings(d, pb[0], pb[1], t, 1.0)
            if len(pts) >= 2:
                (x1, y1), (x2, y2) = pts[-2], pts[-1]
                ang = math.atan2(y2 - y1, x2 - x1)
                for g in (3, 2, 1):   # soft glow under the aircraft
                    d.ellipse([x2 - 26 * g, y2 - 26 * g, x2 + 26 * g, y2 + 26 * g], fill=rgba(G, 0.05))
                self.plane(d, x2, y2, ang, 1.0, size=54)
            if pb and prog > 0.9:
                la = ease((prog - 0.9) / 0.1) * a_out
                self.label_block(d, pb[0], pb[1], sc.get("label") or B["name"], [sc.get("sub", "")], ln.get("note"), la, la)
        elif k == "walkout":
            self.hall(d, sc, loc, en - st, t, a)
            if ln.get("note"):
                d.text((76, 400), ln["note"], font=font("mono", 26), fill=rgba(GM, a))
        elif k == "number":
            prog = (loc - 0.1) / 0.9
            self.big_value(d, self.count(sc["value"], prog), 300, a)
            d.text((84, 480), sc.get("label", ""), font=font("mono", 34), fill=rgba(INK, a * 0.9))
            if sc.get("detail"):
                d.text((84, 526), sc["detail"], font=font("mono", 28), fill=rgba(GM, a))
            d.text((84, 566), ln.get("note", ""), font=font("mono", 25), fill=rgba(GM, a * 0.85))
            nums = re.findall(r"\d[\d,]*\.?\d*", sc["value"])
            n = float(nums[0].replace(",", "")) if nums else 0
            if n >= 100 and re.fullmatch(r"[\d,\.]+", sc["value"].strip()):
                unit = self.unit_for(n)
                self.dots(d, 84, 1200, n, 26, unit, INK, a, ease((loc - 0.3) / 1.4), size=18, gap=6)
                d.text((84, 1232), f"1 square = {unit:,}", font=font("mono", 25), fill=rgba(GM, a))
        elif k == "compare":
            A, B = sc["a"], sc["b"]
            r = B["n"] / A["n"] if A["n"] else 0
            head = (f"{r:.1f}".rstrip("0").rstrip(".") + "x") if r >= 1.2 else \
                   ((f"{1 / r:.1f}".rstrip("0").rstrip(".") + "x") if 0 < r < 0.83 else "")
            if head:
                self.big_value(d, head, 300, a, size=140)
                sub = f"{B['label']}  vs  {A['label']}" if r >= 1.2 else f"{A['label']}  vs  {B['label']}"
                d.text((84, 470), sub, font=font("mono", 30), fill=rgba(INK, a * 0.9))
            d.text((84, 514), ln.get("note", ""), font=font("mono", 25), fill=rgba(GM, a * 0.85))
            unit = self.unit_for(max(A["n"], B["n"]), 480)
            pa_ = ease((loc - 0.2) / 0.8)
            pb_ = ease((loc - 0.9) / 1.3)
            self.dots(d, 84, 1120, A["n"], 11, unit, GM, a, pa_, size=15, gap=5)
            self.dots(d, 84 + 11 * 20 + 40, 1120, B["n"], 30, unit, INK, a, pb_, size=15, gap=5)
            fv = font("sans", 50)
            d.text((84, 1150), A["value"], font=fv, fill=rgba(GM, a))
            d.text((84, 1208), A["label"], font=font("mono", 25), fill=rgba(GM, a))
            d.text((344, 1150), B["value"], font=fv, fill=rgba(INK, a * (0.3 + 0.7 * pb_)))
            d.text((344, 1208), B["label"], font=font("mono", 25), fill=rgba(GM, a * (0.3 + 0.7 * pb_)))
            d.text((84, 1246), f"1 square = {unit:,}", font=font("mono", 23), fill=rgba(GM, a * 0.8))
        elif k == "quote":
            f = font("sans", 72)
            rows = wrap("“" + sc["quote"] + "”", f, 900)
            words_total = sum(len(r.split()) for r in rows)
            shown = int(words_total * ease((loc - 0.1) / max(0.8, (en - st) * 0.75))) + 1
            y, cnt = 380, 0
            for r in rows:
                x = 80
                for w in r.split():
                    cnt += 1
                    aw = a if cnt <= shown else a * 0.14
                    d.text((x, y), w, font=f, fill=rgba(INK, aw))
                    x += f.getlength(w + " ")
                y += 90
            d.text((84, y + 30), sc.get("who", ""), font=font("mono", 32), fill=rgba(G, a))
            d.text((84, y + 74), ln.get("note", ""), font=font("mono", 26), fill=rgba(GM, a))
        elif k == "statement":
            size = 100
            while size > 56 and max(font("sans", size).getlength(r) for r in sc["lines"]) > 920:
                size -= 6
            f = font("sans", size)
            for j, r in enumerate(sc["lines"]):
                aj = a_out * ease((loc - 0.1 - j * 0.45) / 0.35)
                if self.s.get("cover") and i == 0:
                    aj *= ease((t - COVER_T) / 0.35)
                col = self.accents[j % len(self.accents)] if self.accents else INK
                d.text((78, 330 + j * int(size * 1.22) + (1 - aj) * 18), r, font=f, fill=rgba(col, aj))
        else:  # wide: every place in the story
            for pid in self.places:
                self.marker(d, self.places[pid], cam, a_in, big=True, red=True)

        # chrome
        d.text((60, 104), C.BRAND, font=font("sans", 34), fill=rgba(G, 1))
        d.text((62, 150), (self.s.get("region") or "").upper(), font=font("mono", 25), fill=rgba(GM, 1))
        d.text((62, 182), self.s.get("date", ""), font=font("mono", 23), fill=rgba(GM, 0.8))

        # subtitle
        cover_on = t < COVER_T and self.s.get("cover")
        if not cover_on and ln.get("text"):
            sa = ease((t - st + 0.05) / 0.2) * (1 - ease((t - en - 0.1) / 0.2))
            if t < COVER_T + 0.3 and i == 0:
                sa *= ease((t - COVER_T) / 0.3)
            self.subtitle(d, ln["text"], sa)

        # cover riddle
        if self.s.get("cover") and t < COVER_T + 0.35:
            ca = 1 - ease((t - COVER_T) / 0.35)
            self.cover(d, ca)

        # outro
        if self.tl and t > self.tl[-1][1] + 0.2:
            oa = ease((t - self.tl[-1][1] - 0.2) / 0.5)
            d.text((62, 1690), "follow  " + C.HANDLE, font=font("mono", 32), fill=rgba(G, oa))

        im = Image.alpha_composite(im, ov).convert("RGB")
        return self.finish(im, fi)

    def cover(self, d, a):
        f = font("mono", 70)
        tag = "  /  ".join(filter(None, [(self.s.get("region") or "").upper(), self.s.get("date", "")]))
        d.rectangle([60, 640, 60 + font("mono", 26).getlength(tag) + 24, 684], fill=rgba(BG, a))
        d.text((72, 648), tag, font=font("mono", 26), fill=rgba(G, a))
        for j, ln in enumerate(self.s["cover"]):
            y = 712 + j * 108
            size = 70
            while font("mono", size).getlength(ln) > 900 and size > 40:
                size -= 4
            fz = font("mono", size)
            box = self.accents[j % len(self.accents)] if self.accents else (126, 246, 166)
            ink = BG if sum(box) > 500 else (255, 255, 255)
            d.rectangle([60, y - 6, 60 + fz.getlength(ln) + 26, y + 92], fill=rgba(box, a))
            d.text((73, y + (70 - size) / 2), ln, font=fz, fill=rgba(ink, a))


def render(script, timeline, total, audio, out, thumb):
    r = Renderer(script, timeline, total)
    script["_terrain"] = r.terrain_src
    Image.fromarray(r.frame(0.7 if script.get("cover") else LEAD + 1.0, 0)).save(thumb)
    n = int(math.ceil(total * FPS))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", str(audio), "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-aspect", "9:16",
           "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}", "-movflags", "+faststart", str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for fi in range(n):
        p.stdin.write(r.frame(fi / FPS, fi).tobytes())
        if fi % 300 == 0:
            print(f"[render] frame {fi}/{n}")
    p.stdin.close()
    p.wait()
    if p.returncode:
        raise RuntimeError("ffmpeg failed")
