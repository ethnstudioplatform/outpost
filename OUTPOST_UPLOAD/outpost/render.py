"""OUTPOST renderer v4: green war-room terminal, full-frame 1080x1920, built for fast scrolling.

Hook on frame one, a new visual beat every line, constant camera motion, word captions,
glitch cuts. Beats: hook, lock, track, readout, stat, timeline, quote, wide."""
import math
import random
import re
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config as C
from . import geo

W, H, FPS = C.W, C.H, C.FPS
LEAD = 0.15      # hook starts almost instantly
GAP = 0.08
OUTRO = 1.4
CAM_T = 0.7
BOOT = LEAD      # kept for run.py compatibility

BG = (3, 9, 5)
G = (70, 255, 130)
GM = (40, 170, 85)
GD = (18, 70, 36)
GF = (9, 30, 16)
AMB = (255, 180, 40)
REDDOT = (255, 70, 55)

CAP_Y = 1190
OUTLETS = {"apnews.com": "AP", "reuters.com": "REUTERS", "bbc.co.uk": "BBC", "bbc.com": "BBC",
           "aljazeera.com": "AL JAZEERA", "theguardian.com": "THE GUARDIAN", "dw.com": "DW",
           "france24.com": "FRANCE 24", "npr.org": "NPR", "news.sky.com": "SKY NEWS",
           "news.un.org": "UN NEWS", "reliefweb.int": "RELIEFWEB", "cnn.com": "CNN",
           "nytimes.com": "NYT", "kyivindependent.com": "KYIV INDEPENDENT", "timesofisrael.com": "TIMES OF ISRAEL"}
_fonts = {}
_VT = C.FONT_DIR / "VT323-Regular.ttf"


def font(size):
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(str(_VT), size) if _VT.exists() else \
            ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", int(size * 0.72))
    return _fonts[size]


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


def ease(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def pop(ts, at, dur=0.25, amt=0.18):
    """Scale overshoot when something slams in."""
    u = (ts - at) / dur
    if u < 0:
        return 0.0
    if u > 1:
        return 1.0
    return 1 + amt * math.sin(u * math.pi) if u > 0.35 else u / 0.35


def build_timeline(line_durs):
    t = LEAD
    tl = []
    for d in line_durs:
        tl.append((t, d))
        t += d + GAP
    return tl, t + OUTRO


def _beat(ln, i):
    b = ln.get("beat")
    if i == 0:
        return "hook"
    if b:
        if b == "stat" and not ln.get("stat"):
            b = None
        elif b == "track" and not ln.get("arc"):
            b = None
        elif b == "timeline" and not ln.get("year"):
            b = None
        elif b == "readout" and not ln.get("readout"):
            b = None
    if b:
        return b
    if ln.get("arc"):
        return "track"
    if ln.get("stat"):
        return "stat"
    if ln.get("year"):
        return "timeline"
    if ln.get("loc"):
        return "lock"
    return "readout" if ln.get("readout") else "wide"


def sfx_events(script, timeline):
    ev = []
    for i, ((st, du), ln) in enumerate(zip(timeline, script["lines"])):
        b = _beat(ln, i)
        if i > 0:
            ev.append((st - 0.05, "whoosh"))
        if b == "hook":
            ev.append((st + 0.45, "boom"))
        elif b == "lock":
            ev.append((st + 0.55, "lock"))
        elif b == "stat":
            ev.append((st + 1.0, "boom"))
        elif b == "track":
            ev.append((st + 2.0, "boom"))
        elif b in ("readout", "quote"):
            ev.append((st + 0.1, "type"))
    return ev


class Renderer:
    def __init__(self, script, timeline, total):
        self.s = script
        self.tl = timeline
        self.total = total
        self.lines = script["lines"]
        self.start_dt = datetime.fromisoformat(script["generated_utc"])
        self.countries = geo.countries()
        self.beats = [_beat(ln, i) for i, ln in enumerate(self.lines)]
        self.rng = random.Random(11)
        # places in the story
        self.places = []
        for p in [script.get("location")] + [ln.get("loc") for ln in self.lines] + \
                 [x for ln in self.lines if ln.get("arc") for x in (ln["arc"]["from"], ln["arc"]["to"])]:
            if p and p["name"] not in [q["name"] for q in self.places]:
                self.places.append(p)
        self.main = script.get("location") or (self.places[0] if self.places else None)
        self.hot = geo.match_country(self.main["name"]) if self.main else None
        self._cams()
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
                            nm = OUTLETS.get(s["outlet"], s["outlet"].split(".")[0].upper())
                            if nm not in outs:
                                outs.append(nm)
                self.src_tags.append("SRC: " + " + ".join(outs[:2]))
            else:
                self.src_tags.append("")
        # post effects
        yy = (np.arange(H)[:, None] - H / 2) / (H / 2)
        xx = (np.arange(W)[None, :] - W / 2) / (W / 2)
        vig = np.clip(1 - 0.45 * (xx ** 2 * 0.8 + yy ** 2 * 0.5), 0.3, 1)
        scan = np.where(np.arange(H)[:, None] % 4 < 2, 1.0, 0.72)
        self.mask = (scan * vig)[..., None].astype(np.float32)
        g = np.random.default_rng(5)
        self.grain = [g.normal(1, 0.05, (H // 2, W // 2)).repeat(2, 0).repeat(2, 1)[..., None].astype(np.float32)
                      for _ in range(4)]

    # ---------- planning ----------
    def _cams(self):
        m = self.main
        base = (m["lat"], m["lon"], 14.0) if m else (30.0, 30.0, 90.0)
        self.cams = []
        cam = base
        for ln, b in zip(self.lines, self.beats):
            if b == "hook":
                cam = base
            elif b == "lock":
                loc = ln.get("loc") or m
                if loc:
                    cam = (loc["lat"], loc["lon"], 5.5)
            elif b == "track":
                a, c = ln["arc"]["from"], ln["arc"]["to"]
                cam = ((a["lat"] + c["lat"]) / 2, (a["lon"] + c["lon"]) / 2,
                       max(abs(a["lat"] - c["lat"]) * 2.4, abs(a["lon"] - c["lon"]) * 1.4, 6.0))
            elif b == "wide" and self.places:
                lats = [p["lat"] for p in self.places]
                lons = [p["lon"] for p in self.places]
                cam = ((max(lats) + min(lats)) / 2, (max(lons) + min(lons)) / 2,
                       max((max(lats) - min(lats)) * 2.2, (max(lons) - min(lons)) * 1.3, 8.0))
            self.cams.append(cam)

    def _captions(self):
        self.caps = []
        for (st, du), ln in zip(self.tl, self.lines):
            words = ln["text"].upper().split()
            wts = [len(w) + 2 for w in words]
            tot = sum(wts) or 1
            t, timed = st, []
            for w, wt in zip(words, wts):
                d = du * wt / tot
                timed.append((t, t + d, w))
                t += d
            chunks, cur, n = [], [], 0
            for it in timed:
                if cur and (len(cur) >= 3 or n + len(it[2]) > 16):
                    chunks.append(cur)
                    cur, n = [], 0
                cur.append(it)
                n += len(it[2]) + 1
            if cur:
                chunks.append(cur)
            self.caps.append(chunks)

    def _active(self, t):
        idx = 0
        for i, (st, _) in enumerate(self.tl):
            if t >= st:
                idx = i
        return idx

    def camera(self, t):
        i = self._active(t)
        prev = self.cams[i - 1] if i > 0 else (self.cams[0][0], self.cams[0][1], self.cams[0][2] * 1.6)
        k = ease((t - self.tl[i][0]) / CAM_T) if self.tl else 1
        c = self.cams[i]
        lat = prev[0] + (c[0] - prev[0]) * k
        lon = prev[1] + (c[1] - prev[1]) * k
        span = prev[2] * (c[2] / prev[2]) ** k
        span *= 1 - 0.035 * (t - self.tl[i][0])          # constant push-in
        lon += 0.004 * span * math.sin(t * 0.7)           # drift
        return (lat, lon, max(span, 2.5))

    # ---------- map ----------
    def proj(self, cam):
        lat_c, lon_c, span_h = cam
        cl = math.cos(math.radians(lat_c))
        span_w = span_h * (W / H) / cl

        def P(lon, lat):
            return ((lon - lon_c) / span_w * W + W / 2, (lat_c - lat) / span_h * H + H / 2)
        return P, lon_c - span_w / 2, span_w

    def draw_map(self, cam, dim=1.0):
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im)
        P, x0, span_w = self.proj(cam)
        step = 10 if cam[2] > 25 else (2 if cam[2] < 8 else 5)
        for lon in range(int(math.floor(x0 / step) * step), int(x0 + span_w) + step, step):
            x, _ = P(lon, 0)
            d.line([(x, 0), (x, H)], fill=GF)
        for lat in range(-90, 91, step):
            _, y = P(0, lat)
            d.line([(0, y), (W, y)], fill=GF)
        for name, rings in self.countries:
            hot = name == self.hot
            for r in rings:
                xs = [q[0] for q in r]
                if max(xs) < x0 - 2 or min(xs) > x0 + span_w + 2:
                    continue
                pts = [P(a, b) for a, b in r]
                if len(pts) < 3:
                    continue
                d.polygon(pts, fill=mix(G, (0.15 if hot else 0.05) * dim))
                d.line(pts + [pts[0]], fill=mix(G if hot else GM, dim), width=4 if hot else 2)
        # every place in the story as a small dot
        for p in self.places:
            x, y = P(p["lon"], p["lat"])
            d.ellipse([x - 7, y - 7, x + 7, y + 7], fill=mix(AMB, dim))
        return im, d, P

    # ---------- common overlays ----------
    def hud(self, d, t):
        d.text((60, 140), "OUTPOST", font=font(72), fill=G)
        if int(t * 2) % 2 == 0:
            d.ellipse([318, 162, 340, 184], fill=REDDOT)
        d.text((352, 146), "LIVE", font=font(60), fill=REDDOT)
        sub = f"{self.start_dt.strftime('%d %b %Y').upper()} // {self.s.get('region', '')[:18]}"
        d.text((62, 212), sub, font=font(42), fill=GM)

    def reticle(self, d, x, y, r, t, label=None, k=1.0):
        for j in range(3):
            ph = (t * 0.9 + j / 3) % 1
            rr = 20 + ph * 170
            d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=mix(AMB, 1 - ph), width=4)
        d.ellipse([x - 12, y - 12, x + 12, y + 12], fill=AMB)
        for sx, sy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            cx, cy = x + sx * r, y + sy * r
            d.line([(cx, cy), (cx - sx * 42, cy)], fill=G, width=6)
            d.line([(cx, cy), (cx, cy - sy * 42)], fill=G, width=6)
        for a, b in [((0, y), (x - r - 24, y)), ((x + r + 24, y), (W, y)), ((x, 0), (x, y - r - 24)), ((x, y + r + 24), (x, H))]:
            d.line([a, b], fill=GD, width=2)
        if label:
            name, coord = label
            name, coord = name[: int(len(name) * k)], coord[: int(len(coord) * k)]
            if name:
                f1, f2 = font(64), font(48)
                bw = max(f1.getlength(name), f2.getlength(coord)) + 40
                bx = min(max(x + 60, 40), W - 60 - bw)
                by = y - r - 180 if y - r - 180 > 280 else y + r + 40
                d.rectangle([bx, by, bx + bw, by + 124], fill=BG, outline=G, width=3)
                d.text((bx + 20, by + 4), name, font=f1, fill=G)
                d.text((bx + 20, by + 66), coord, font=f2, fill=GM)

    @staticmethod
    def coord(p):
        return f"{abs(p['lat']):.2f}{'N' if p['lat'] >= 0 else 'S'} {abs(p['lon']):.2f}{'E' if p['lon'] >= 0 else 'W'}"

    def captions(self, d, t, i, y=CAP_Y):
        cur = None
        for ch in self.caps[i]:
            if ch[0][0] <= t:
                cur = ch
        if cur is None or t > cur[-1][1] + 0.4:
            return y
        f = font(118)
        rows, row = [], []
        for idx, it in enumerate(cur):
            if row and f.getlength(" ".join(x[2] for x in row + [it])) > 900:
                rows.append(row)
                row = []
            row.append(it)
        rows.append(row)
        # small pop when a new chunk appears
        age = t - cur[0][0]
        dy = int(18 * max(0, 1 - age / 0.12))
        for r in rows:
            line = " ".join(x[2] for x in r)
            x = (W - f.getlength(line)) / 2
            for st, en, w in r:
                col = AMB if st <= t < en + 0.04 else G
                d.text((x, y + dy), w, font=f, fill=col, stroke_width=7, stroke_fill=BG)
                x += f.getlength(w + " ")
            y += 116
        tag = self.src_tags[i]
        if tag:
            fs = font(44)
            tw = fs.getlength(tag)
            col = AMB if tag == "BACKGROUND" else GM
            d.rectangle([(W - tw) / 2 - 18, y + 14, (W + tw) / 2 + 18, y + 70], fill=BG, outline=col, width=2)
            d.text(((W - tw) / 2, y + 16), tag, font=fs, fill=col)
        return y

    # ---------- beats ----------
    def beat_hook(self, im, d, P, t, ts):
        if self.main:
            x, y = P(self.main["lon"], self.main["lat"])
            self.reticle(d, x, y, 90 + 160 * (1 - ease(ts / 0.6)), t)
        f = font(124)
        rows = wrap(self.s.get("headline") or self.lines[0]["text"].upper(), f, 940)[:3]
        rows = wrap(self.lines[0]["text"].upper(), f, 940)[:4]
        k = min(1.0, ts / 0.35)
        y = 380
        for r in rows:
            s = r[: max(1, int(len(r) * k))]
            d.text((60, y), s, font=f, fill=G, stroke_width=8, stroke_fill=BG)
            y += 112
        bar = self.s.get("hook_bar")
        if bar and ts > 0.4:
            sc = pop(ts, 0.4)
            base = 150
            while font(base).getlength(bar) > 920 and base > 60:
                base -= 6
            fb = font(max(20, int(base * sc)))
            tw = fb.getlength(bar)
            by = y + 30
            d.rectangle([50, by, 50 + tw + 40, by + fb.size + 10], fill=AMB)
            d.text((70, by - 2), bar, font=fb, fill=BG)
        return True   # hook draws its own text, captions hidden

    def beat_lock(self, im, d, P, t, ts, ln):
        loc = ln.get("loc") or self.main
        if not loc:
            return False
        x, y = P(loc["lon"], loc["lat"])
        r = 110 + 220 * (1 - ease(ts / 0.6))
        self.reticle(d, x, y, r, t, (loc["name"].split(",")[0], self.coord(loc)), ease((ts - 0.5) / 0.4))
        return False

    def beat_track(self, im, d, P, t, ts, ln):
        arc = ln["arc"]
        a, b = arc["from"], arc["to"]
        ax, ay = P(a["lon"], a["lat"])
        bx, by = P(b["lon"], b["lat"])
        dist = math.hypot(bx - ax, by - ay)
        lift = 0 if arc["type"] in ("troops", "naval") else max(160, dist * 0.45)
        mx, my = (ax + bx) / 2, (ay + by) / 2 - lift

        def pt(u):
            return ((1 - u) ** 2 * ax + 2 * (1 - u) * u * mx + u * u * bx,
                    (1 - u) ** 2 * ay + 2 * (1 - u) * u * my + u * u * by)
        for j in range(0, 80, 3):
            d.line([pt(j / 80), pt((j + 1.4) / 80)], fill=GD, width=3)
        d.rectangle([ax - 14, ay - 14, ax + 14, ay + 14], outline=G, width=4)
        d.text((ax + 24, ay - 30), a["name"].split(",")[0], font=font(56), fill=G, stroke_width=4, stroke_fill=BG)
        u = ease((ts - 0.3) / 1.7)
        if ts > 0.3:
            n = max(2, int(80 * u))
            tr = [pt(j / 80) for j in range(n)] + [pt(u)]
            for j in range(len(tr) - 1):
                d.line([tr[j], tr[j + 1]], fill=mix(AMB, 0.3 + 0.7 * (j + 1) / len(tr)), width=9)
            hx, hy = pt(u)
            if u < 1:
                d.ellipse([hx - 16, hy - 16, hx + 16, hy + 16], fill=(255, 240, 200))
        imp = ts - 2.0
        if imp > 0:
            for j in range(3):
                rr = (imp * 300 + j * 70) % 330
                d.ellipse([bx - rr, by - rr, bx + rr, by + rr], outline=mix(AMB, max(0, 1 - rr / 330)), width=6)
            if imp < 0.25:
                fl = 150 * (1 - imp / 0.25)
                d.ellipse([bx - fl, by - fl, bx + fl, by + fl], fill=(255, 240, 200))
        tag = {"missile": "MISSILE TRACK", "drone": "DRONE TRACK", "airstrike": "AIRSTRIKE",
               "artillery": "ARTILLERY", "naval": "NAVAL MOVE", "troops": "TROOP MOVE"}[arc["type"]]
        d.rectangle([60, 290, 60 + font(64).getlength(tag) + 30, 362], fill=AMB)
        d.text((74, 292), tag, font=font(64), fill=BG)
        d.text((62, 370), "REPORTED // PATH ILLUSTRATIVE", font=font(40), fill=GM)
        return False

    def beat_readout(self, im, d, t, ts, ln):
        txt = "> " + ln["readout"]
        f = font(96)
        rows = wrap(txt, f, 900)
        k = min(1.0, ts / 0.5)
        total = sum(len(r) for r in rows)
        shown = int(total * k)
        y = 560
        d.text((60, y - 70), "INCOMING DATA //", font=font(52), fill=GM)
        for r in rows:
            s = r[: max(0, shown)]
            shown -= len(r)
            d.rectangle([50, y - 6, W - 50, y + 102], fill=BG, outline=GD)
            d.text((70, y), s, font=f, fill=G)
            if 0 < len(s) < len(r) or (s == r and r is rows[-1] and int(t * 3) % 2 == 0):
                cx = 70 + f.getlength(s) + 8
                d.rectangle([cx, y + 12, cx + 40, y + 92], fill=G)
            y += 112
        return False

    def beat_stat(self, im, d, t, ts, ln, i):
        st = ln["stat"]
        raw = st["value"]
        m = re.search(r"[\d,.]+", raw)
        k = ease(ts / 0.9)
        shown = raw
        if m:
            s_ = m.group(0).replace(",", "")
            try:
                cur = float(s_) * k
                body = f"{cur:,.1f}" if "." in s_ else (f"{int(round(cur)):,}" if "," in m.group(0) else str(int(round(cur))))
                shown = raw[: m.start()] + body + raw[m.end():]
            except ValueError:
                pass
        size = 480 if len(shown) <= 3 else (340 if len(shown) <= 6 else 240)
        f = font(int(size * (1 + 0.1 * max(0, 1 - abs(ts - 0.95) / 0.2))))
        d.text(((W - f.getlength(shown)) / 2, 380), shown, font=f, fill=G, stroke_width=10, stroke_fill=BG)
        lab = st.get("label", "")
        fl = font(110)
        lw = fl.getlength(lab)
        d.rectangle([(W - lw) / 2 - 30, 900, (W + lw) / 2 + 30, 1010], fill=AMB)
        d.text(((W - lw) / 2, 900), lab, font=fl, fill=BG)
        for j in range(20):
            h = 20 + 80 * abs(math.sin(t * 4 + j * 0.7)) * k
            d.rectangle([90 + j * 46, 1560 - h, 120 + j * 46, 1560], fill=GD)
        return False

    def beat_timeline(self, im, d, t, ts, ln):
        year = ln["year"]
        now = self.start_dt.year + self.start_dt.timetuple().tm_yday / 366
        span = max(now - year, 0.5)
        k = ease(ts / 1.2)
        d.text((60, 330), str(year), font=font(300), fill=G, stroke_width=8, stroke_fill=BG)
        lab = f"{span * k:.1f} YEARS"
        d.text((W - 60 - font(120).getlength(lab), 660), lab, font=font(120), fill=AMB, stroke_width=6, stroke_fill=BG)
        x0, x1, y = 80, W - 80, 880
        d.line([(x0, y), (x1, y)], fill=GD, width=8)
        px = x0 + (x1 - x0) * k
        d.line([(x0, y), (px, y)], fill=AMB, width=12)
        d.ellipse([px - 20, y - 20, px + 20, y + 20], fill=AMB)
        for j in range(int(span) + 1):
            yx = x0 + (x1 - x0) * min(1, j / span)
            d.line([(yx, y - 22), (yx, y + 22)], fill=GM, width=3)
        d.text((x1 - font(60).getlength("NOW"), y + 30), "NOW", font=font(60), fill=G)
        return False

    def beat_quote(self, im, d, t, ts, ln):
        who = ln.get("who") or "OFFICIAL CLAIM"
        d.rectangle([60, 360, 60 + font(64).getlength("CLAIM // " + who) + 30, 432], fill=AMB)
        d.text((74, 362), "CLAIM // " + who, font=font(64), fill=BG)
        f = font(108)
        rows = wrap('"' + ln["text"] + '"', f, 940)
        k = min(1.0, ts / 0.8)
        total = sum(len(r) for r in rows)
        shown = int(total * k)
        y = 480
        for r in rows[:6]:
            d.text((60, y), r[: max(0, shown)], font=f, fill=G, stroke_width=6, stroke_fill=BG)
            shown -= len(r)
            y += 104
        tag = self.src_tags[self._active(t)]
        if tag:
            d.text((62, y + 20), tag, font=font(48), fill=GM)
        return True   # quote replaces captions

    def beat_wide(self, im, d, P, t, ts, final):
        for j, p in enumerate(self.places):
            x, y = P(p["lon"], p["lat"])
            ph = (t * 0.8 + j * 0.3) % 1
            for q in range(2):
                rr = 18 + ((ph + q / 2) % 1) * 120
                d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=mix(AMB, 1 - ((ph + q / 2) % 1)), width=5)
            d.ellipse([x - 13, y - 13, x + 13, y + 13], fill=AMB)
            nm = p["name"].split(",")[0]
            fl = font(58)
            lx = min(max(x + 28, 40), W - 50 - fl.getlength(nm))
            d.text((lx, y - 70), nm, font=fl, fill=G, stroke_width=5, stroke_fill=BG)
        return False

    def glitch(self, arr, t, s):
        rng = np.random.default_rng(int(t * 1000))
        out = arr.copy()
        for _ in range(int(12 * s) + 3):
            y0 = int(rng.integers(0, H - 60))
            hh = int(rng.integers(10, 100))
            out[y0:y0 + hh] = np.roll(out[y0:y0 + hh], int(rng.integers(-110, 110) * s), axis=1)
        out[..., 1] = np.roll(out[..., 1], int(8 * s), axis=1)
        return out * (1 + 0.35 * s)

    # ---------- frame ----------
    def frame(self, t):
        i = self._active(t)
        ln = self.lines[i]
        b = self.beats[i]
        ts = t - self.tl[i][0]
        cam = self.camera(t)
        dim = 0.35 if b in ("stat", "timeline", "quote", "readout") else 1.0
        im, d, P = self.draw_map(cam, dim)
        hide_caps = False
        if b == "hook":
            hide_caps = self.beat_hook(im, d, P, t, ts)
        elif b == "lock":
            self.beat_lock(im, d, P, t, ts, ln)
        elif b == "track":
            self.beat_track(im, d, P, t, ts, ln)
        elif b == "readout":
            self.beat_readout(im, d, t, ts, ln)
        elif b == "stat":
            self.beat_stat(im, d, t, ts, ln, i)
        elif b == "timeline":
            self.beat_timeline(im, d, t, ts, ln)
        elif b == "quote":
            hide_caps = self.beat_quote(im, d, t, ts, ln)
        else:
            self.beat_wide(im, d, P, t, ts, i == len(self.lines) - 1)
        self.hud(d, t)
        if not hide_caps:
            self.captions(d, t, i, 1300 if b in ("stat", "timeline") else CAP_Y)
        end = self.tl[-1][0] + self.tl[-1][1]
        if t > end:
            msg = "> FOLLOW @OUTPOST.FEED"
            k = min(1.0, (t - end) / 0.5)
            d.text((60, 1560), msg[: int(len(msg) * k)], font=font(72), fill=G, stroke_width=5, stroke_fill=BG)
        # CRT treatment
        small = im.resize((W // 4, H // 4)).filter(ImageFilter.GaussianBlur(6))
        arr = np.asarray(im, np.float32) + np.asarray(small.resize((W, H)), np.float32) * 0.75
        arr = arr * self.mask * self.grain[int(t * FPS) % 4] * (0.95 + 0.05 * self.rng.random())
        band = int((t * 520) % (H + 300)) - 150
        y0, y1 = max(0, band), min(H, band + 140)
        if y1 > y0:
            arr[y0:y1] *= 1.1
        for st, _ in self.tl[1:]:
            if 0 <= t - st < 0.14:
                arr = self.glitch(arr, t, 1 - (t - st) / 0.14)
        if b == "hook" and 0.4 <= ts < 0.6:     # slam shake
            s = int(16 * (1 - (ts - 0.4) / 0.2))
            arr = np.roll(np.roll(arr, self.rng.randint(-s, s), 0), self.rng.randint(-s, s), 1)
        if b == "track" and 2.0 <= ts < 2.3:
            s = int(22 * (1 - (ts - 2.0) / 0.3))
            arr = np.roll(np.roll(arr, self.rng.randint(-s, s), 0), self.rng.randint(-s, s), 1)
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
    thumb_t = LEAD + 0.9
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
