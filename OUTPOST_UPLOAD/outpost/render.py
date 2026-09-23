"""OUTPOST renderer: full-frame 1080x1920 (9:16) green phosphor war room.
Big animated map panel with camera moves, country highlight, attack tracks,
kinetic keywords, stat counters and timelines. PIL frames piped into ffmpeg."""
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
BOOT = 2.2
OUTRO = 2.6
GAP = 0.35
CAM_T = 1.4                   # seconds for a camera move between scenes
WORLD = (15.0, 10.0, 150.0)   # (lat, lon, vertical span in degrees)

# layout
PX, PY, PW, PH = 0, 215, W, 960   # visual panel, edge to edge
HEAD_Y = PY + PH + 16
LOG_BOTTOM = 1700
TICK_Y = 1715
PROG_Y = 1800

_VT = C.FONT_DIR / "VT323-Regular.ttf"
_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
_cache = {}


def font(size):
    if size not in _cache:
        if _VT.exists():
            _cache[size] = ImageFont.truetype(str(_VT), size)
        else:
            _cache[size] = ImageFont.truetype(_FALLBACK, int(size * 0.74))
    return _cache[size]


def wrap(text, f, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if f.getlength(test) <= width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def mix(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def _crt_mask():
    y = np.arange(H)[:, None]
    scan = np.where(y % 4 < 2, 1.0, 0.74)
    xx = (np.arange(W)[None, :] - W / 2) / (W / 2)
    yy = (np.arange(H)[:, None] - H / 2) / (H / 2)
    vig = 1 - 0.36 * (xx ** 2 * 0.9 + yy ** 2 * 0.55)
    return (scan * np.clip(vig, 0.35, 1))[..., None].astype(np.float32)


def _static_bg():
    im = Image.new("RGB", (W, H), C.BG)
    d = ImageDraw.Draw(im)
    for x in range(0, W, 60):
        d.line([(x, 0), (x, H)], fill=C.GREEN_FAINT)
    for y in range(0, H, 60):
        d.line([(0, y), (W, y)], fill=C.GREEN_FAINT)
    d.line([(0, PY - 2), (W, PY - 2)], fill=C.GREEN_DIM, width=2)
    d.line([(0, PY + PH + 1), (W, PY + PH + 1)], fill=C.GREEN_DIM, width=2)
    return im


def build_timeline(line_durs):
    t = BOOT
    tl = []
    for d in line_durs:
        tl.append((t, d))
        t += d + GAP
    return tl, t + OUTRO


def _fit(a, b, aspect):
    lat_c, lon_c = (a["lat"] + b["lat"]) / 2, (a["lon"] + b["lon"]) / 2
    span_h = max(abs(a["lat"] - b["lat"]) * 2.2, abs(a["lon"] - b["lon"]) * 2.2 / aspect, 10.0)
    return (lat_c, lon_c, span_h)


class Renderer:
    def __init__(self, script, timeline, total):
        self.s = script
        self.tl = timeline
        self.total = total
        self.lines = script["lines"]
        self.bg = _static_bg()
        self.mask = _crt_mask()
        self.rng = random.Random(42)
        self.start_dt = datetime.fromisoformat(script["generated_utc"])
        self.ticker = "   ///   ".join(t.upper() for t in script.get("ticker", [])) + "   ///   "
        self.countries = geo.countries()
        self.aspect = PW / PH
        self.map_cache = {}
        self._plan()
        self.src_tags = []
        for ln in self.lines:
            if ln.get("context"):
                self.src_tags.append("// BACKGROUND")
            elif ln.get("src"):
                outs = []
                for n in ln["src"]:
                    for s in script.get("sources", []):
                        if s["n"] == n and s["outlet"].upper() not in outs:
                            outs.append(s["outlet"].upper())
                self.src_tags.append("SRC: " + " + ".join(outs[:2]))
            else:
                self.src_tags.append("")

    # ---------- planning ----------
    def _plan(self):
        main = self.s.get("location")
        self.first_cam = (main["lat"], main["lon"], 24.0) if main else WORLD
        self.cams, self.targets, self.hl = [], [], []
        cam = self.first_cam
        for ln in self.lines:
            loc = ln.get("loc") or main
            if ln.get("arc"):
                cam = _fit(ln["arc"]["from"], ln["arc"]["to"], self.aspect)
                tgt = ln["arc"]["to"]
            elif ln.get("loc"):
                cam = (loc["lat"], loc["lon"], 24.0)
                tgt = loc
            else:
                tgt = loc
            self.cams.append(cam)
            self.targets.append(tgt)
            self.hl.append(geo.match_country(tgt["name"]) if tgt else None)

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
            return self._lerp_cam(WORLD, self.first_cam, ease((t - 0.6) / (BOOT + 0.6))), False
        prev = self.cams[i - 1] if i > 0 else self.first_cam
        k = ease((t - self.tl[i][0]) / CAM_T)
        return self._lerp_cam(prev, self.cams[i], k), k < 1

    # ---------- map ----------
    def proj(self, cam):
        lat_c, lon_c, span_h = cam
        span_w = span_h * self.aspect
        x0, y1 = lon_c - span_w / 2, lat_c + span_h / 2

        def P(lon, lat):
            return ((lon - x0) / span_w * PW, (y1 - lat) / span_h * PH)
        return P, x0, span_w

    def draw_map(self, cam, highlight):
        key = (round(cam[0], 3), round(cam[1], 3), round(cam[2], 3), highlight)
        if key in self.map_cache:
            return self.map_cache[key].copy()
        p = Image.new("RGB", (PW, PH), C.BG)
        d = ImageDraw.Draw(p)
        P, x0, span_w = self.proj(cam)
        span_h = cam[2]
        step = 30 if span_h > 70 else (10 if span_h > 25 else 5)
        for lon in range(int(math.floor(x0 / step) * step), int(x0 + span_w) + step, step):
            x, _ = P(lon, 0)
            d.line([(x, 0), (x, PH)], fill=C.GREEN_FAINT)
        for lat in range(-90, 91, step):
            _, y = P(0, lat)
            d.line([(0, y), (PW, y)], fill=C.GREEN_FAINT)
        width = 1 if span_h > 70 else 2
        label = None
        for name, rings in self.countries:
            hot = name == highlight
            for r in rings:
                xs = [q[0] for q in r]
                if max(xs) < x0 - 1 or min(xs) > x0 + span_w + 1:
                    continue
                pts = [P(lon, lat) for lon, lat in r]
                if len(pts) < 3:
                    continue
                d.polygon(pts, fill=mix(C.GREEN, 0.22 if hot else 0.07))
                d.line(pts + [pts[0]], fill=C.GREEN if hot else C.GREEN_MID, width=width + (1 if hot else 0))
            if hot:
                big = max(rings, key=len)
                label = (sum(q[0] for q in big) / len(big), sum(q[1] for q in big) / len(big), name)
        if label and span_h < 70:
            x, y = P(label[0], label[1])
            f = font(40)
            d.text((x - f.getlength(label[2]) / 2, y + 40), label[2], font=f, fill=C.GREEN)
        if len(self.map_cache) > 300:
            self.map_cache.clear()
        self.map_cache[key] = p
        return p.copy()

    def marker(self, d, P, loc, t, lock):
        x, y = P(loc["lon"], loc["lat"])
        for r_i in range(3):
            ph = (t * 0.9 + r_i / 3) % 1.0
            rr = 12 + ph * 110
            d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=mix(C.AMBER if lock else C.GREEN, (1 - ph) * 0.9), width=3)
        d.ellipse([x - 9, y - 9, x + 9, y + 9], fill=C.AMBER if lock else C.GREEN)
        gap = 30
        for a, b in [((0, y), (x - gap, y)), ((x + gap, y), (PW, y)), ((x, 0), (x, y - gap)), ((x, y + gap), (x, PH))]:
            d.line([a, b], fill=C.GREEN_DIM)
        return x, y

    def label(self, d, x, y, title, sub, k):
        f1, f2 = font(46), font(34)
        title, sub = title[: int(len(title) * k)], sub[: int(len(sub) * k)]
        if not title:
            return
        lx = min(max(x + 60, 24), PW - f1.getlength(title) - 30)
        ly = y + 70 if y < PH - 200 else y - 170
        d.rectangle([lx - 10, ly - 6, lx + max(f1.getlength(title), f2.getlength(sub)) + 10, ly + 96],
                    fill=C.BG, outline=C.GREEN_DIM)
        d.text((lx, ly), title, font=f1, fill=C.GREEN)
        d.text((lx, ly + 52), sub, font=f2, fill=C.GREEN_MID)

    @staticmethod
    def coord(loc):
        la, lo = loc["lat"], loc["lon"]
        return f"{abs(la):06.2f}{'N' if la >= 0 else 'S'}  {abs(lo):06.2f}{'E' if lo >= 0 else 'W'}"

    # ---------- attack / movement tracks ----------
    def arc_scene(self, d, P, arc, ts):
        a, b = arc["from"], arc["to"]
        ax, ay = P(a["lon"], a["lat"])
        bx, by = P(b["lon"], b["lat"])
        dist = math.hypot(bx - ax, by - ay)
        kind = arc["type"]
        lift = 0 if kind in ("troops", "naval") else max(90, dist * 0.35)
        mx, my = (ax + bx) / 2, (ay + by) / 2 - lift

        def pt(u):
            return ((1 - u) ** 2 * ax + 2 * (1 - u) * u * mx + u * u * bx,
                    (1 - u) ** 2 * ay + 2 * (1 - u) * u * my + u * u * by)
        for j in range(0, 60, 2):
            d.line([pt(j / 60), pt((j + 1) / 60)], fill=C.GREEN_DIM, width=2)
        d.rectangle([ax - 10, ay - 10, ax + 10, ay + 10], outline=C.GREEN, width=3)
        d.text((ax - 60, ay + 18), a["name"].split(",")[0], font=font(34), fill=C.GREEN_MID)
        launch, fly = 0.5, 2.4
        u = ease((ts - launch) / fly)
        hot = kind in ("missile", "drone", "airstrike", "artillery")
        if ts > launch:
            n = max(2, int(60 * u))
            trail = [pt(j / 60) for j in range(n)] + [pt(u)]
            for j in range(len(trail) - 1):
                fade = (j + 1) / len(trail)
                d.line([trail[j], trail[j + 1]], fill=mix(C.AMBER if hot else C.GREEN, 0.3 + 0.7 * fade), width=5)
            hx, hy = pt(u)
            if u < 1:
                d.ellipse([hx - 10, hy - 10, hx + 10, hy + 10], fill=(255, 230, 160))
                d.ellipse([hx - 22, hy - 22, hx + 22, hy + 22], outline=C.AMBER, width=2)
            if kind == "troops":
                ang = math.atan2(by - ay, bx - ax)
                for s in (-0.5, 0.5):
                    d.line([(hx, hy), (hx - 30 * math.cos(ang + s), hy - 30 * math.sin(ang + s))], fill=C.GREEN, width=5)
        imp = ts - (launch + fly)
        if imp > 0 and hot:
            for r_i in range(3):
                rr = (imp * 160 + r_i * 45) % 200
                d.ellipse([bx - rr, by - rr, bx + rr, by + rr], outline=mix(C.AMBER, max(0, 1 - rr / 200)), width=4)
            if imp < 0.25:
                fl = 60 * (1 - imp / 0.25)
                d.ellipse([bx - fl, by - fl, bx + fl, by + fl], fill=(255, 240, 200))
        tag = {"missile": "MISSILE TRACK", "drone": "DRONE TRACK", "airstrike": "AIRSTRIKE",
               "artillery": "ARTILLERY", "naval": "NAVAL MOVEMENT", "troops": "TROOP MOVEMENT"}[kind] + " (REPORTED)"
        f = font(40)
        d.rectangle([24, 104, 40 + f.getlength(tag) + 10, 152], fill=C.BG, outline=C.AMBER)
        d.text((34, 108), tag, font=f, fill=C.AMBER)
        d.text((34, 158), "PATH ILLUSTRATIVE", font=font(28), fill=C.GREEN_DIM)

    # ---------- overlays ----------
    def stat_overlay(self, d, stat, ts, src):
        raw = stat["value"]
        m = re.search(r"[\d,.]+", raw)
        k = ease(ts / 1.4)
        shown = raw
        if m:
            num_s = m.group(0).replace(",", "")
            try:
                cur = float(num_s) * k
                body = f"{cur:,.1f}" if "." in num_s else (f"{int(round(cur)):,}" if "," in m.group(0) else str(int(round(cur))))
                shown = raw[: m.start()] + body + raw[m.end():]
            except ValueError:
                pass
        big = font(300)
        y0 = PH / 2 - 210
        d.text(((PW - big.getlength(shown)) / 2, y0), shown, font=big, fill=C.GREEN)
        lab, fl = stat.get("label", ""), font(64)
        d.text(((PW - fl.getlength(lab)) / 2, y0 + 300), lab, font=fl, fill=C.GREEN)
        if src:
            s2 = f"SRC: {src}"
            d.text(((PW - font(36).getlength(s2)) / 2, y0 + 378), s2, font=font(36), fill=C.GREEN_MID)
        b = 70 + (1 - k) * 60
        for cx, cy, dx, dy in [(b, b, 1, 1), (PW - b, b, -1, 1), (b, PH - b, 1, -1), (PW - b, PH - b, -1, -1)]:
            d.line([(cx, cy), (cx + dx * 70, cy)], fill=C.GREEN, width=5)
            d.line([(cx, cy), (cx, cy + dy * 70)], fill=C.GREEN, width=5)

    def timeline_overlay(self, d, year, ts):
        now_y = self.start_dt.year + self.start_dt.timetuple().tm_yday / 366
        k = ease(ts / 1.6)
        x0, x1, y = 90, PW - 90, PH / 2 + 60
        span = max(now_y - year, 0.5)
        d.line([(x0, y), (x1, y)], fill=C.GREEN_DIM, width=5)
        yrs = int(span) + 1
        for j in range(yrs + 1):
            yx = x0 + (x1 - x0) * min(1, j / span)
            if yx <= x1:
                d.line([(yx, y - 18), (yx, y + 18)], fill=C.GREEN_MID, width=3)
                if yrs <= 12 or j % 2 == 0:
                    d.text((yx - 34, y + 28), str(year + j), font=font(36), fill=C.GREEN_MID)
        px = x0 + (x1 - x0) * k
        d.line([(x0, y), (px, y)], fill=C.AMBER, width=10)
        d.ellipse([px - 16, y - 16, px + 16, y + 16], fill=C.AMBER)
        d.text((x0, y - 290), str(year), font=font(200), fill=C.GREEN)
        d.text((x0 + font(200).getlength(str(year)) + 24, y - 200), "START", font=font(56), fill=C.GREEN_MID)
        dur = span * k
        lab = f"{dur:.1f} YEARS" if dur < 10 else f"{int(dur)} YEARS"
        d.text((x1 - font(80).getlength(lab), y - 150), lab, font=font(80), fill=C.AMBER)
        d.text((x1 - font(36).getlength("TO PRESENT"), y - 66), "TO PRESENT", font=font(36), fill=C.GREEN_MID)

    def keyword_overlay(self, d, kw, ts, t):
        f = font(150 if len(kw) <= 12 else 110)
        lines = wrap(kw, f, PW - 120)[:2]
        k = min(1.0, max(0.0, (ts - 0.9) / 0.7))
        if k <= 0:
            return
        glyphs = "#$%&@*+=<>/|01"
        rnd = random.Random(int(t * 20))
        y = PH - 70 - len(lines) * (f.size + 6)
        for ln in lines:
            n_ok = int(len(ln) * k)
            shown = ln[:n_ok] + "".join(rnd.choice(glyphs) if c != " " else " " for c in ln[n_ok:])
            w = f.getlength(ln)
            x = (PW - w) / 2
            d.rectangle([x - 20, y - 4, x + w + 20, y + f.size + 8], fill=C.BG)
            d.text((x, y), shown, font=f, fill=C.GREEN if n_ok == len(ln) else C.GREEN_MID)
            y += f.size + 6

    def radar(self, d, t):
        cx, cy, R = PW / 2, PH / 2 + 20, 330
        for k in (1, 2, 3):
            d.ellipse([cx - R * k / 3, cy - R * k / 3, cx + R * k / 3, cy + R * k / 3], outline=C.GREEN_DIM, width=2)
        d.line([(cx - R, cy), (cx + R, cy)], fill=C.GREEN_DIM)
        d.line([(cx, cy - R), (cx, cy + R)], fill=C.GREEN_DIM)
        sweep = (t * 110) % 360
        for k in range(30):
            a = math.radians(sweep - k * 1.6)
            d.line([(cx, cy), (cx + R * math.cos(a), cy + R * math.sin(a))], fill=mix(C.GREEN, (1 - k / 30) ** 2 * 0.9), width=4)

    # ---------- panel ----------
    def panel(self, im, t):
        i = self._active(t)
        cam, moving = self.camera(t)
        ln = self.lines[i] if i >= 0 else {}
        main = self.s.get("location")
        highlight = self.hl[i] if i >= 0 else (geo.match_country(main["name"]) if main else None)
        have_map = bool(self.countries)
        overlay = "stat" if ln.get("stat") else ("timeline" if ln.get("context") and ln.get("year") else None)
        if have_map:
            p = self.draw_map(cam, highlight)
            if overlay:
                p = Image.blend(p, Image.new("RGB", p.size, C.BG), 0.72)
        else:
            p = Image.new("RGB", (PW, PH), C.BG)
        d = ImageDraw.Draw(p)
        ts = t - self.tl[i][0] if i >= 0 else t
        P, _, _ = self.proj(cam)
        tgt = self.targets[i] if i >= 0 else main
        if overlay == "stat":
            self.stat_overlay(d, ln["stat"], ts, self.src_tags[i].replace("SRC: ", ""))
        elif overlay == "timeline":
            self.timeline_overlay(d, ln["year"], ts)
        elif have_map:
            if ln.get("arc"):
                self.arc_scene(d, P, ln["arc"], ts)
                x, y = P(ln["arc"]["to"]["lon"], ln["arc"]["to"]["lat"])
                self.label(d, x, y, ln["arc"]["to"]["name"], self.coord(ln["arc"]["to"]), ease((ts - 2.9) / 0.6))
            elif tgt:
                x, y = self.marker(d, P, tgt, t, not moving)
                self.label(d, x, y, tgt["name"], self.coord(tgt), ease((ts - 1.0) / 0.7))
            if ln.get("keyword"):
                self.keyword_overlay(d, ln["keyword"], ts, t)
            sx = (t * 300) % (PW + 300) - 150
            for j in range(26):
                d.line([(sx - j * 3, 60), (sx - j * 3, PH)], fill=mix(C.GREEN, 0.16 * (1 - j / 26)))
            d.text((PW - 270, PH - 40), "APPROX. POSITIONS", font=font(28), fill=C.GREEN_DIM)
            d.text((24, 64), f"ZOOM x{WORLD[2] / cam[2]:4.1f}   {self.coord({'lat': cam[0], 'lon': cam[1]})}",
                   font=font(30), fill=C.GREEN_DIM)
        else:
            self.radar(d, t)
            if ln.get("keyword"):
                self.keyword_overlay(d, ln["keyword"], ts, t)
        d.rectangle([0, 0, PW, 52], fill=C.BG)
        strip = (f"SIG {self.s.get('signal_count', 0)} // OUTLETS {self.s.get('outlet_count', 0)}"
                 f" // CITED {len(self.s.get('sources', []))}")
        d.text((24, 10), strip, font=font(34), fill=C.GREEN_MID)
        reg = self.s.get("region", "GLOBAL")[:20]
        d.text((PW - 24 - font(34).getlength(reg), 10), reg, font=font(34), fill=C.GREEN)
        if ln.get("context"):
            tag = " BACKGROUND BRIEF "
            tw = font(38).getlength(tag)
            d.rectangle([PW - 24 - tw, 60, PW - 24, 104], fill=C.AMBER)
            d.text((PW - 24 - tw, 62), tag, font=font(38), fill=C.BG)
        im.paste(p, (PX, PY))

    # ---------- chrome ----------
    def header(self, d, t):
        d.text((40, 58), C.BRAND, font=font(104), fill=C.GREEN)
        blink = int(t * 2) % 2 == 0
        d.ellipse([W - 230, 84, W - 206, 108], fill=C.AMBER if blink else mix(C.AMBER, 0.3))
        d.text((W - 192, 66), "LIVE", font=font(58), fill=C.AMBER)
        d.text((W - 270, 128), C.HANDLE, font=font(36), fill=C.GREEN_MID)
        now = self.start_dt + timedelta(seconds=t)
        d.text((42, 162), f"{C.TAGLINE}  //  {now.strftime('%d %b %Y  %H:%M:%S').upper()}Z", font=font(38), fill=C.GREEN_MID)

    def headline(self, d):
        d.text((40, HEAD_Y), f"> SITREP // {self.s['stamp']}", font=font(36), fill=C.GREEN_MID)
        hl = wrap(self.s["headline"], font(72), W - 80)[:2]
        for i, l in enumerate(hl):
            d.text((40, HEAD_Y + 38 + i * 64), l, font=font(72), fill=C.GREEN)
        return HEAD_Y + 38 + len(hl) * 64 + 14

    def log(self, d, t, top):
        f, fs = font(48), font(32)
        blocks = []
        for i, ((st, du), ln) in enumerate(zip(self.tl, self.lines)):
            if t < st:
                break
            full = "> " + ln["text"]
            k = min(1.0, (t - st) / max(0.4, min(du * 0.8, len(full) * 0.04)))
            shown = full[: int(len(full) * k)]
            active = i == len(self.lines) - 1 or t < self.tl[i + 1][0]
            rows = wrap(shown, f, W - 90) or [""]
            blocks.append((rows, self.src_tags[i] if k >= 1 else "", active, k < 1 or active))
        heights = [len(r) * 50 + (36 if tag else 6) + 10 for r, tag, _, _ in blocks]
        while blocks and top + sum(heights) > LOG_BOTTOM:
            blocks.pop(0); heights.pop(0)
        y = top
        for (rows, tag, active, cur), hgt in zip(blocks, heights):
            for j, r in enumerate(rows):
                d.text((40, y + j * 50), r, font=f, fill=C.GREEN if active else C.GREEN_DIM)
            if active and cur and int(t * 2.5) % 2 == 0:
                lx = 40 + f.getlength(rows[-1]) + 6
                ly = y + (len(rows) - 1) * 50
                d.rectangle([lx, ly + 8, lx + 22, ly + 46], fill=C.GREEN)
            if tag:
                tcol = C.AMBER if tag.startswith("//") else C.GREEN_MID
                d.text((64, y + len(rows) * 50 + 2), tag, font=fs, fill=tcol if active else mix(tcol, 0.5))
            y += hgt

    def ticker_bar(self, im, t):
        f = font(40)
        x0, y0, y1 = 170, TICK_Y, TICK_Y + 58
        strip = Image.new("RGB", (W - x0, y1 - y0), C.BG)
        sd = ImageDraw.Draw(strip)
        tw = max(f.getlength(self.ticker), 1)
        x = 12 - (t * 140) % tw
        while x < strip.width:
            sd.text((x, 8), self.ticker, font=f, fill=C.GREEN_MID)
            x += tw
        im.paste(strip, (x0, y0))
        d = ImageDraw.Draw(im)
        d.rectangle([0, y0, 168, y1], fill=C.GREEN_DIM)
        d.text((30, y0 + 8), "WIRE", font=f, fill=C.GREEN)

    def progress(self, d, t):
        p = min(1, t / self.total)
        d.rectangle([40, PROG_Y, W - 40, PROG_Y + 10], outline=C.GREEN_DIM)
        d.rectangle([40, PROG_Y, 40 + (W - 80) * p, PROG_Y + 10], fill=C.GREEN_MID)
        tt = f"TX {int(t)//60:02d}:{int(t)%60:02d} / {int(self.total)//60:02d}:{int(self.total)%60:02d}"
        d.text((40, PROG_Y + 16), tt, font=font(30), fill=C.GREEN_DIM)
        d.text((W - 300, PROG_Y + 16), "SOURCES IN CAPTION", font=font(30), fill=C.GREEN_DIM)

    def boot(self, im, d, t):
        if self.countries:
            cam, _ = self.camera(t)
            p = self.draw_map(cam, None)
            p = Image.blend(Image.new("RGB", p.size, C.BG), p, min(1, t / BOOT) * 0.8)
            im.paste(p, (PX, PY))
        msgs = ["OUTPOST TERMINAL v0.2", "ESTABLISHING UPLINK ...... OK", "DECRYPTING WIRE FEED ..... OK",
                f"SIGNALS ACQUIRED ......... {self.s.get('signal_count', 0)}", "BEGIN TRANSMISSION"]
        f = font(52)
        for i, m in enumerate(msgs):
            st = 0.15 + i * 0.38
            if t < st:
                break
            txt = m[: int(len(m) * min(1, (t - st) / 0.3))]
            d.text((80, 1200 + i * 70), txt, font=f, fill=C.GREEN)

    def outro(self, d, t):
        k = min(1, (t - (self.total - OUTRO)) / 0.4)
        d.rectangle([110, 640, W - 110, 1040], fill=C.BG, outline=C.GREEN, width=3)
        y = 700
        for m, sz, col in [("END TRANSMISSION", 84, C.GREEN), ("", 20, C.GREEN),
                           (f"FOLLOW {C.HANDLE.upper()}", 52, C.GREEN_MID), ("SOURCES IN CAPTION", 44, C.GREEN_MID)]:
            f = font(sz)
            d.text(((W - f.getlength(m)) / 2, y), m[: int(len(m) * k)], font=f, fill=col)
            y += sz + 26

    def glitch(self, arr, t):
        hit = None
        for st, _ in self.tl:
            if 0 <= t - st < 0.22:
                hit = t - st
        if hit is None:
            return arr
        rng = np.random.default_rng(int(t * 1000))
        s = 1 - hit / 0.22
        out = arr.copy()
        for _ in range(int(10 * s) + 3):
            y0 = int(rng.integers(0, H - 40))
            hh = int(rng.integers(6, 60))
            out[y0:y0 + hh] = np.roll(out[y0:y0 + hh], int(rng.integers(-60, 60) * s), axis=1)
        noise = rng.random((H // 8, W // 8, 1)).repeat(8, 0).repeat(8, 1)
        return out * (1 + 0.25 * s) + noise * 40 * s * np.array([0.3, 1.0, 0.4])

    def frame(self, t):
        im = self.bg.copy()
        d = ImageDraw.Draw(im)
        self.header(d, t)
        if t < BOOT:
            self.boot(im, d, t)
        else:
            self.panel(im, t)
            d = ImageDraw.Draw(im)
            top = self.headline(d)
            self.log(d, t, top)
            self.ticker_bar(im, t)
            d = ImageDraw.Draw(im)
            self.progress(d, t)
            if t > self.total - OUTRO:
                k = min(1, (t - (self.total - OUTRO)) / 0.3)
                im = Image.blend(im, Image.new("RGB", (W, H), C.BG), 0.78 * k)
                self.outro(ImageDraw.Draw(im), t)
        small = im.resize((W // 4, H // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(5))
        glow = np.asarray(small.resize((W, H), Image.BILINEAR), dtype=np.float32)
        arr = (np.asarray(im, dtype=np.float32) + glow * 0.7) * self.mask * (0.96 + 0.04 * self.rng.random())
        band_y = int((t * 420) % (H + 300)) - 150
        y0, y1 = max(0, band_y), min(H, band_y + 120)
        if y1 > y0:
            arr[y0:y1] *= 1.08
        arr = self.glitch(arr, t)
        return np.clip(arr, 0, 255).astype(np.uint8)


def render(script, timeline, total, audio: Path, out: Path, thumb: Path):
    r = Renderer(script, timeline, total)
    n = int(total * FPS)
    proc = subprocess.Popen([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", str(audio),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE)
    thumb_t = timeline[0][0] + 3.0 if timeline else BOOT + 1
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
