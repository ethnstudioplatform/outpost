"""OUTPOST renderer v3: full-bleed 1080x1920 vertical video built for TikTok / Reels / Shorts.

Opens in the green war-room terminal (brand hook), then glitches into a full-colour
cinematic map that fills the whole screen: camera moves, country highlight, attack
tracks with impact shake, giant stat counters, word-by-word captions."""
import math
import random
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config as C
from . import geo

W, H, FPS = C.W, C.H, C.FPS
BOOT = 1.6            # green terminal intro
SWITCH_AFTER = 1.2    # seconds into line 1 before the cut to colour
OUTRO = 2.8
GAP = 0.3
CAM_T = 1.3
WORLD = (20.0, 20.0, 170.0)   # lat, lon, vertical span (deg)

# colour palette (post-intro)
OCEAN = (7, 12, 22)
LAND = (26, 34, 46)
BORDER = (84, 98, 118)
GRID = (16, 24, 38)
HOT_FILL = (92, 22, 26)
HOT_EDGE = (255, 72, 60)
RED = (255, 59, 48)
YELLOW = (255, 214, 10)
WHITE = (245, 245, 245)
GREY = (160, 170, 185)

# TikTok safe area: keep key text inside x 60..900, y 140..1560
CAP_Y = 1240

_fonts = {}
_VT = C.FONT_DIR / "VT323-Regular.ttf"
_BOLD = C.FONT_DIR / "Anton-Regular.ttf"


def mono(size):
    k = ("m", size)
    if k not in _fonts:
        _fonts[k] = ImageFont.truetype(str(_VT), size) if _VT.exists() else \
            ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", int(size * 0.74))
    return _fonts[k]


def bold(size):
    k = ("b", size)
    if k not in _fonts:
        _fonts[k] = ImageFont.truetype(str(_BOLD), size) if _BOLD.exists() else \
            ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", int(size * 0.82))
    return _fonts[k]


def wrap(text, f, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if f.getlength(t) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def mix(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def lerp_c(a, b, k):
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def build_timeline(line_durs):
    t = BOOT
    tl = []
    for d in line_durs:
        tl.append((t, d))
        t += d + GAP
    return tl, t + OUTRO


def _fit(a, b, aspect):
    lat_c, lon_c = (a["lat"] + b["lat"]) / 2, (a["lon"] + b["lon"]) / 2
    span_h = max(abs(a["lat"] - b["lat"]) * 2.6, abs(a["lon"] - b["lon"]) * 2.6 / aspect, 12.0)
    return (lat_c, lon_c, span_h)


def text_stroke(d, xy, txt, f, fill, stroke=6, sfill=(0, 0, 0)):
    d.text(xy, txt, font=f, fill=fill, stroke_width=stroke, stroke_fill=sfill)


class Renderer:
    def __init__(self, script, timeline, total):
        self.s = script
        self.tl = timeline
        self.total = total
        self.lines = script["lines"]
        self.start_dt = datetime.fromisoformat(script["generated_utc"])
        self.countries = geo.countries()
        self.aspect = W / H
        self.cache = {}
        self.rng = random.Random(7)
        self.switch_t = (timeline[0][0] + SWITCH_AFTER) if timeline else BOOT
        yy = (np.arange(H)[:, None] - H / 2) / (H / 2)
        xx = (np.arange(W)[None, :] - W / 2) / (W / 2)
        vig = np.clip(1 - 0.42 * (xx ** 2 * 0.7 + yy ** 2 * 0.5), 0.3, 1)
        self.vig = vig[..., None].astype(np.float32)
        scan = np.where(np.arange(H)[:, None] % 4 < 2, 1.0, 0.72)
        self.crt = (scan * vig)[..., None].astype(np.float32)
        top = np.ones((H, 1), np.float32)
        top[:520, 0] = 0.35 + 0.65 * (np.arange(520) / 520) ** 1.5   # darker top for legibility
        self.vig = self.vig * top[..., None]
        self._plan()
        self._captions()
        self.src_tags = []
        for ln in self.lines:
            if ln.get("context"):
                self.src_tags.append("BACKGROUND")
            elif ln.get("src"):
                outs = []
                for n in ln["src"]:
                    for s in script.get("sources", []):
                        if s["n"] == n and s["outlet"].upper() not in outs:
                            outs.append(s["outlet"].upper())
                self.src_tags.append("SOURCE: " + " + ".join(outs[:2]))
            else:
                self.src_tags.append("")

    # ---------------- planning ----------------
    def _plan(self):
        main = self.s.get("location")
        self.first_cam = (main["lat"], main["lon"], 26.0) if main else WORLD
        self.cams, self.targets, self.hl = [], [], []
        cam = self.first_cam
        for ln in self.lines:
            loc = ln.get("loc") or main
            if ln.get("arc"):
                cam = _fit(ln["arc"]["from"], ln["arc"]["to"], self.aspect)
                tgt = ln["arc"]["to"]
            elif ln.get("loc"):
                cam = (loc["lat"], loc["lon"], 26.0)
                tgt = loc
            else:
                tgt = loc
            self.cams.append(cam)
            self.targets.append(tgt)
            self.hl.append(geo.match_country(tgt["name"]) if tgt else None)

    def _captions(self):
        """Word timings per line (proportional to word length) grouped into short chunks."""
        self.caps = []
        for (st, du), ln in zip(self.tl, self.lines):
            words = ln["text"].split()
            weights = [len(w) + 2 for w in words]
            tot = sum(weights) or 1
            t, timed = st, []
            for w, wt in zip(words, weights):
                d = du * wt / tot
                timed.append((t, t + d, w))
                t += d
            chunks, cur, clen = [], [], 0
            for item in timed:
                if cur and (len(cur) >= 4 or clen + len(item[2]) > 18):
                    chunks.append(cur)
                    cur, clen = [], 0
                cur.append(item)
                clen += len(item[2]) + 1
            if cur:
                chunks.append(cur)
            self.caps.append(chunks)

    def _active(self, t):
        idx = -1
        for i, (st, _) in enumerate(self.tl):
            if t >= st:
                idx = i
        return idx

    @staticmethod
    def _lerp_cam(a, b, k):
        return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] * (b[2] / a[2]) ** k)

    def camera(self, t):
        i = self._active(t)
        if i < 0:
            cam = self._lerp_cam(WORLD, self.first_cam, ease(t / (BOOT + 1.0)))
        else:
            prev = self.cams[i - 1] if i > 0 else self.first_cam
            cam = self._lerp_cam(prev, self.cams[i], ease((t - self.tl[i][0]) / CAM_T))
        # constant slow push-in keeps the frame alive
        return (cam[0], cam[1], cam[2] * (1 - 0.0045 * (t % 20)))

    # ---------------- map ----------------
    def proj(self, cam):
        lat_c, lon_c, span_h = cam
        span_w = span_h * self.aspect
        x0, y1 = lon_c - span_w / 2, lat_c + span_h / 2

        def P(lon, lat):
            return ((lon - x0) / span_w * W, (y1 - lat) / span_h * H)
        return P, x0, span_w

    def draw_map(self, cam, highlight, green):
        key = (round(cam[0], 2), round(cam[1], 2), round(cam[2], 2), highlight, green)
        if key in self.cache:
            return self.cache[key].copy()
        if green:
            ocean, land, edge, grid, hfill, hedge = C.BG, mix(C.GREEN, 0.08), C.GREEN_MID, C.GREEN_FAINT, mix(C.GREEN, 0.25), C.GREEN
        else:
            ocean, land, edge, grid, hfill, hedge = OCEAN, LAND, BORDER, GRID, HOT_FILL, HOT_EDGE
        im = Image.new("RGB", (W, H), ocean)
        d = ImageDraw.Draw(im)
        P, x0, span_w = self.proj(cam)
        span_h = cam[2]
        step = 30 if span_h > 80 else (10 if span_h > 30 else 5)
        for lon in range(int(math.floor(x0 / step) * step), int(x0 + span_w) + step, step):
            x, _ = P(lon, 0)
            d.line([(x, 0), (x, H)], fill=grid)
        for lat in range(-90, 91, step):
            _, y = P(0, lat)
            d.line([(0, y), (W, y)], fill=grid)
        label = None
        wid = 2 if span_h > 80 else 3
        for name, rings in self.countries:
            hot = name == highlight
            for r in rings:
                xs = [q[0] for q in r]
                if max(xs) < x0 - 1 or min(xs) > x0 + span_w + 1:
                    continue
                pts = [P(lon, lat) for lon, lat in r]
                if len(pts) < 3:
                    continue
                d.polygon(pts, fill=hfill if hot else land)
                d.line(pts + [pts[0]], fill=hedge if hot else edge, width=wid + (2 if hot else 0))
            if hot:
                big = max(rings, key=len)
                label = (sum(q[0] for q in big) / len(big), sum(q[1] for q in big) / len(big), name)
        if label and span_h < 80:
            x, y = P(label[0], label[1])
            f = bold(64) if not green else mono(56)
            tw = f.getlength(label[2])
            x = min(max(x - tw / 2, 60), 900 - tw)
            text_stroke(d, (x, y + 60), label[2], f, (255, 255, 255) if not green else C.GREEN, 5,
                        (60, 10, 10) if not green else C.BG)
        if len(self.cache) > 200:
            self.cache.clear()
        self.cache[key] = im
        return im.copy()

    # ---------------- elements ----------------
    def marker(self, d, P, loc, t, green):
        x, y = P(loc["lon"], loc["lat"])
        col = C.AMBER if green else RED
        for r_i in range(3):
            ph = (t * 0.9 + r_i / 3) % 1.0
            rr = 14 + ph * 150
            d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=mix(col, (1 - ph)), width=5)
        d.ellipse([x - 13, y - 13, x + 13, y + 13], fill=col, outline=WHITE, width=3)
        return x, y

    def place_label(self, d, x, y, loc, k, green):
        name = loc["name"].split(",")[0]
        name = name[: int(len(name) * k)]
        if not name:
            return
        f = bold(58) if not green else mono(54)
        tw = f.getlength(name)
        lx = min(max(x + 40, 60), 880 - tw)
        ly = y - 120 if y > 400 else y + 50
        if green:
            d.rectangle([lx - 12, ly - 4, lx + tw + 12, ly + 62], fill=C.BG, outline=C.GREEN)
            d.text((lx, ly), name, font=f, fill=C.GREEN)
        else:
            d.rounded_rectangle([lx - 16, ly - 6, lx + tw + 16, ly + 80], radius=10, fill=RED)
            d.text((lx, ly), name, font=f, fill=WHITE)

    def arc_scene(self, d, P, arc, ts):
        """Returns shake amount."""
        a, b = arc["from"], arc["to"]
        ax, ay = P(a["lon"], a["lat"])
        bx, by = P(b["lon"], b["lat"])
        kind = arc["type"]
        dist = math.hypot(bx - ax, by - ay)
        lift = 0 if kind in ("troops", "naval") else max(140, dist * 0.45)
        mx, my = (ax + bx) / 2, (ay + by) / 2 - lift

        def pt(u):
            return ((1 - u) ** 2 * ax + 2 * (1 - u) * u * mx + u * u * bx,
                    (1 - u) ** 2 * ay + 2 * (1 - u) * u * my + u * u * by)
        for j in range(0, 80, 3):
            d.line([pt(j / 80), pt((j + 1.4) / 80)], fill=(120, 130, 150), width=3)
        d.ellipse([ax - 14, ay - 14, ax + 14, ay + 14], outline=WHITE, width=4)
        f = bold(44)
        o = a["name"].split(",")[0]
        text_stroke(d, (min(max(ax - f.getlength(o) / 2, 40), 900 - f.getlength(o)), ay + 26), o, f, WHITE, 5)
        launch, fly = 0.4, 2.2
        u = ease((ts - launch) / fly)
        hot = kind in ("missile", "drone", "airstrike", "artillery")
        shake = 0.0
        if ts > launch:
            n = max(2, int(80 * u))
            trail = [pt(j / 80) for j in range(n)] + [pt(u)]
            for j in range(len(trail) - 1):
                fade = (j + 1) / len(trail)
                d.line([trail[j], trail[j + 1]], fill=lerp_c((90, 20, 20), RED if hot else YELLOW, fade), width=9)
            hx, hy = pt(u)
            if u < 1:
                d.ellipse([hx - 30, hy - 30, hx + 30, hy + 30], fill=(120, 30, 20))
                d.ellipse([hx - 14, hy - 14, hx + 14, hy + 14], fill=(255, 235, 200))
        imp = ts - (launch + fly)
        if imp > 0 and hot:
            for r_i in range(4):
                rr = (imp * 260 + r_i * 60) % 320
                d.ellipse([bx - rr, by - rr, bx + rr, by + rr], outline=mix(RED, max(0, 1 - rr / 320)), width=7)
            if imp < 0.3:
                fl = 140 * (1 - imp / 0.3)
                d.ellipse([bx - fl, by - fl, bx + fl, by + fl], fill=(255, 230, 190))
                shake = 22 * (1 - imp / 0.3)
        tag = {"missile": "MISSILE STRIKE", "drone": "DRONE ATTACK", "airstrike": "AIRSTRIKE",
               "artillery": "SHELLING", "naval": "NAVAL MOVE", "troops": "TROOP ADVANCE"}[kind]
        return shake, tag

    # ---------------- overlays ----------------
    def top_bar(self, d, t, green):
        if green:
            d.text((60, 150), C.BRAND, font=mono(96), fill=C.GREEN)
            return
        f = bold(56)
        d.text((60, 146), "OUTPOST", font=f, fill=WHITE)
        x = 60 + f.getlength("OUTPOST") + 22
        pill = "LIVE"
        blink = int(t * 2) % 2 == 0
        d.rounded_rectangle([x, 156, x + 118, 204], radius=8, fill=RED if blink else (150, 30, 25))
        d.text((x + 18, 154), pill, font=bold(40), fill=WHITE)
        now = self.start_dt + timedelta(seconds=t)
        d.text((60, 216), now.strftime("%d %b %Y  %H:%M UTC").upper(), font=bold(32), fill=GREY)

    def headline_card(self, d, t):
        f = bold(84)
        lines = wrap(self.s["headline"], f, 820)[:2]
        y = 270
        for ln in lines:
            tw = f.getlength(ln)
            d.rectangle([52, y - 2, 60 + tw + 24, y + 100], fill=YELLOW)
            d.text((66, y), ln, font=f, fill=(0, 0, 0))
            y += 108

    def caption(self, d, t, i):
        if i < 0:
            return
        chunks = self.caps[i]
        cur = None
        for ch in chunks:
            if ch[0][0] <= t:
                cur = ch
        if cur is None or t > cur[-1][1] + 0.6:
            return
        f = bold(92)
        words = [w for _, _, w in cur]
        text = " ".join(words)
        rows = wrap(text, f, 820)
        y = CAP_Y - (len(rows) - 1) * 52
        wi = 0
        for row in rows:
            rw = row.split()
            x = (W - 120 - f.getlength(row)) / 2 + 60
            for w in rw:
                st, en, _ = cur[wi]
                active = st <= t < en + 0.05
                pop = 1.0
                col = YELLOW if active else WHITE
                text_stroke(d, (x, y), w, f, col, 8)
                x += f.getlength(w + " ")
                wi += 1
            y += 108
        tag = self.src_tags[i]
        if tag:
            fs = bold(34)
            tw = fs.getlength(tag)
            tx = (W - tw) / 2
            yb = y + 10
            col = (200, 120, 0) if tag == "BACKGROUND" else (30, 30, 30)
            d.rounded_rectangle([tx - 16, yb, tx + tw + 16, yb + 50], radius=8, fill=col)
            d.text((tx, yb + 2), tag, font=fs, fill=WHITE)

    def stat_screen(self, im, stat, ts, src):
        dark = Image.new("RGB", (W, H), (0, 0, 0))
        im = Image.blend(im, dark, 0.78)
        d = ImageDraw.Draw(im)
        raw = stat["value"]
        m = re.search(r"[\d,.]+", raw)
        k = ease(ts / 1.2)
        shown = raw
        if m:
            num_s = m.group(0).replace(",", "")
            try:
                cur = float(num_s) * k
                body = f"{cur:,.1f}" if "." in num_s else (f"{int(round(cur)):,}" if "," in m.group(0) else str(int(round(cur))))
                shown = raw[: m.start()] + body + raw[m.end():]
            except ValueError:
                pass
        size = 330 if len(shown) <= 5 else 240
        f = bold(size)
        pop = 1 + 0.12 * max(0, 1 - abs(ts - 1.2) / 0.25)
        f = bold(int(size * pop))
        tw = f.getlength(shown)
        text_stroke(d, ((W - tw) / 2, 560), shown, f, WHITE, 10, (120, 10, 10))
        lab = stat.get("label", "")
        fl = bold(80)
        d.rounded_rectangle([(W - fl.getlength(lab)) / 2 - 24, 560 + size + 40, (W + fl.getlength(lab)) / 2 + 24, 560 + size + 150],
                            radius=12, fill=RED)
        d.text(((W - fl.getlength(lab)) / 2, 560 + size + 44), lab, font=fl, fill=WHITE)
        if src:
            s2 = f"SOURCE: {src}"
            d.text(((W - bold(38).getlength(s2)) / 2, 560 + size + 170), s2, font=bold(38), fill=GREY)
        return im

    def timeline_screen(self, im, year, ts):
        im = Image.blend(im, Image.new("RGB", (W, H), (0, 0, 0)), 0.78)
        d = ImageDraw.Draw(im)
        now_y = self.start_dt.year + self.start_dt.timetuple().tm_yday / 366
        span = max(now_y - year, 0.5)
        k = ease(ts / 1.5)
        dur = span * k
        big = f"{dur:.1f}" if dur < 10 else f"{int(dur)}"
        f = bold(330)
        text_stroke(d, ((W - f.getlength(big)) / 2, 470), big, f, WHITE, 10, (120, 10, 10))
        lab = "YEARS OF CONFLICT"
        d.text(((W - bold(76).getlength(lab)) / 2, 850), lab, font=bold(76), fill=YELLOW)
        x0, x1, y = 110, 850, 1040
        d.line([(x0, y), (x1, y)], fill=(80, 80, 90), width=8)
        px = x0 + (x1 - x0) * k
        d.line([(x0, y), (px, y)], fill=RED, width=12)
        d.ellipse([px - 20, y - 20, px + 20, y + 20], fill=RED, outline=WHITE, width=4)
        d.text((x0 - 10, y + 36), str(year), font=bold(56), fill=WHITE)
        d.text((x1 - bold(56).getlength("NOW"), y + 36), "NOW", font=bold(56), fill=WHITE)
        return im

    def boot(self, im, t):
        d = ImageDraw.Draw(im)
        msgs = ["OUTPOST // LIVE CONFLICT MONITOR", "UPLINK ........ OK",
                f"SIGNALS ....... {self.s.get('signal_count', 0)}", "INCOMING TRANSMISSION"]
        f = mono(58)
        y = 700
        for i, m in enumerate(msgs):
            st = 0.1 + i * 0.3
            if t < st:
                break
            txt = m[: int(len(m) * min(1, (t - st) / 0.25))]
            d.rectangle([54, y - 4, 70 + f.getlength(txt), y + 62], fill=C.BG)
            d.text((60, y), txt, font=f, fill=C.GREEN)
            y += 76

    def green_hook(self, im, t):
        """First moments of line 1 in terminal style: headline typed large."""
        d = ImageDraw.Draw(im)
        f = mono(110)
        rows = wrap(self.s["headline"], f, 900)[:3]
        k = min(1, (t - self.tl[0][0]) / 0.6)
        y = 700
        for r in rows:
            s = r[: int(len(r) * k)]
            d.rectangle([54, y - 6, 70 + f.getlength(s), y + 110], fill=C.BG)
            d.text((60, y), s, font=f, fill=C.GREEN)
            y += 118

    def outro(self, im, t):
        k = ease((t - (self.total - OUTRO)) / 0.4)
        im = Image.blend(im, Image.new("RGB", (W, H), (0, 0, 0)), 0.8 * k)
        d = ImageDraw.Draw(im)
        f1, f2, f3 = bold(110), bold(64), bold(44)
        for txt, f, col, y in [("FOLLOW", f1, WHITE, 640), (C.HANDLE.upper(), f2, YELLOW, 780),
                               ("TWICE-DAILY CONFLICT BRIEFINGS", f3, WHITE, 880), ("SOURCES IN CAPTION", f3, GREY, 950)]:
            d.text(((W - f.getlength(txt)) / 2, y), txt[: int(len(txt) * k)], font=f, fill=col)
        return im

    def glitch(self, arr, t, s):
        rng = np.random.default_rng(int(t * 1000))
        out = arr.copy()
        for _ in range(int(14 * s) + 4):
            y0 = int(rng.integers(0, H - 60))
            hh = int(rng.integers(8, 90))
            out[y0:y0 + hh] = np.roll(out[y0:y0 + hh], int(rng.integers(-90, 90) * s), axis=1)
        out[..., 0] = np.roll(out[..., 0], int(10 * s), axis=1)
        out[..., 2] = np.roll(out[..., 2], -int(10 * s), axis=1)
        return out

    # ---------------- frame ----------------
    def frame(self, t):
        i = self._active(t)
        green = t < self.switch_t
        cam = self.camera(t)
        ln = self.lines[i] if i >= 0 else {}
        hl = self.hl[i] if i >= 0 else None
        im = self.draw_map(cam, hl, green) if self.countries else Image.new("RGB", (W, H), OCEAN)
        d = ImageDraw.Draw(im)
        P, _, _ = self.proj(cam)
        ts = t - self.tl[i][0] if i >= 0 else t
        shake, tag = 0.0, None
        if green:
            if t < BOOT:
                self.boot(im, t)
            else:
                self.green_hook(im, t)
            self.top_bar(d, t, True)
        else:
            if ln.get("stat"):
                im = self.stat_screen(im, ln["stat"], ts, self.src_tags[i].replace("SOURCE: ", ""))
                d = ImageDraw.Draw(im)
            elif ln.get("context") and ln.get("year"):
                im = self.timeline_screen(im, ln["year"], ts)
                d = ImageDraw.Draw(im)
            elif ln.get("arc"):
                shake, tag = self.arc_scene(d, P, ln["arc"], ts)
                x, y = P(ln["arc"]["to"]["lon"], ln["arc"]["to"]["lat"])
                self.place_label(d, x, y, ln["arc"]["to"], ease((ts - 2.7) / 0.4), False)
            elif self.targets[i] if i >= 0 else None:
                x, y = self.marker(d, P, self.targets[i], t, False)
                self.place_label(d, x, y, self.targets[i], ease((ts - 0.5) / 0.5), False)
            self.top_bar(d, t, False)
            self.headline_card(d, t)
            if tag:
                f = bold(52)
                tw = f.getlength(tag)
                d.rounded_rectangle([60, 520, 60 + tw + 40, 590], radius=10, fill=RED)
                d.text((80, 522), tag, font=f, fill=WHITE)
                d.text((64, 598), "REPORTED. PATH ILLUSTRATIVE", font=bold(30), fill=GREY)
            self.caption(d, t, i)
            # progress bar (bottom edge)
            p = min(1, t / self.total)
            d.rectangle([0, H - 12, W * p, H], fill=RED)
            if t > self.total - OUTRO:
                im = self.outro(im, t)
        arr = np.asarray(im, dtype=np.float32)
        if green:
            small = im.resize((W // 4, H // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(5))
            arr = (arr + np.asarray(small.resize((W, H), Image.BILINEAR), dtype=np.float32) * 0.7) * self.crt
        else:
            arr = arr * self.vig
        # cut from green to colour, and line changes: glitch
        g = 0.0
        if 0 <= t - self.switch_t < 0.35:
            g = 1 - (t - self.switch_t) / 0.35
        elif not green:
            for st, _ in self.tl[1:]:
                if 0 <= t - st < 0.15:
                    g = 0.6 * (1 - (t - st) / 0.15)
        if g > 0:
            arr = self.glitch(arr, t, g)
        if shake > 0:
            dx, dy = int(self.rng.uniform(-shake, shake)), int(self.rng.uniform(-shake, shake))
            arr = np.roll(np.roll(arr, dy, axis=0), dx, axis=1)
        return np.clip(arr, 0, 255).astype(np.uint8)


def render(script, timeline, total, audio: Path, out: Path, thumb: Path):
    r = Renderer(script, timeline, total)
    n = int(total * FPS)
    proc = subprocess.Popen([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", str(audio),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-aspect", "9:16", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE)
    thumb_t = timeline[0][0] + 2.5 if timeline else BOOT + 1
    for i in range(n):
        t = i / FPS
        fr = r.frame(t)
        proc.stdin.write(fr.tobytes())
        if abs(t - thumb_t) < 0.5 / FPS:
            Image.fromarray(fr).save(thumb)
        if i % (FPS * 5) == 0:
            print(f"[render] {t:5.1f}s / {total:.1f}s")
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg encode failed")
