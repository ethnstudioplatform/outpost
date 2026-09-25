"""OUTPOST 'map room' style (opt-in per script with "style": "maproom").

A dark 1940s-style war room: the relief map is a lamp-lit table, a hanging lamp throws a dusty
cone of light onto it, and a wall of green CRT monitors sits behind (radar sweep, mission clock,
teletype of the sources). Detail scenes cut to a close-up of a monitor (bezel + scanlines).
Film finish: heavier grain, lamp flicker, gentle gate weave, the odd dust speck.
Subtitles type on like a teletype. Everything stays in the OUTPOST green palette.
"""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 1080, 1920
BG = (4, 10, 7)
INK = (218, 255, 228)
G = (96, 236, 146)
GM = (74, 166, 110)
GD = (26, 66, 42)
RED = (255, 74, 62)
WARM = (255, 214, 150)
GLASS = (6, 24, 13)

WALL_END = 430          # wall fully opaque above this
WALL_FADE = 600         # ...and gone below this
SCREENS = [(56, 132, 356, 346), (390, 132, 690, 346), (724, 132, 1024, 346)]
LAMP_X, LAMP_Y = 540, 1060   # centre of the pool of light on the table


def _a(c, a):
    return (c[0], c[1], c[2], int(max(0, min(1, a)) * 255))


class MapRoom:
    def __init__(self, r):
        self.r = r
        rng = np.random.default_rng(21)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)

        # ---- lamp pool on the table (multiplier for the map layer) ----
        d2 = ((xx - LAMP_X) / 620) ** 2 + ((yy - LAMP_Y) / 760) ** 2
        pool = 0.30 + 1.05 * np.exp(-d2 * 1.25)
        tint = np.stack([1 + 0.10 * np.exp(-d2 * 2), 1 + 0.02 * np.exp(-d2 * 2), 1 - 0.10 * np.exp(-d2 * 2)], -1)
        self.pool = (pool[..., None] * tint).astype(np.float32)

        # ---- wall layer (static) ----
        wall = np.zeros((H, W, 4), np.float32)
        base = np.array([9, 15, 12], np.float32)
        tex = rng.normal(0, 1, (H // 8, W // 8)).astype(np.float32)
        tex = np.asarray(Image.fromarray(((tex * 20 + 128).clip(0, 255)).astype(np.uint8)).resize((W, H), Image.BICUBIC), np.float32) / 255 - 0.5
        panels = (np.abs(((xx + 30) % 270) - 0.5) < 2.5).astype(np.float32)   # panel seams
        shade = 0.75 + 0.35 * np.exp(-(((xx - LAMP_X) / 700) ** 2)) * np.clip(yy / 500, 0, 1)
        col = base[None, None, :] * shade[..., None] * (1 + tex[..., None] * 0.5) - panels[..., None] * 5
        alpha = np.clip((WALL_FADE - yy) / (WALL_FADE - WALL_END), 0, 1)
        wall[..., :3] = col
        wall[..., 3] = alpha * 255
        wim = Image.fromarray(np.clip(wall, 0, 255).astype(np.uint8), "RGBA")
        d = ImageDraw.Draw(wim)
        # table rim catching the lamp
        for k, aa in ((0, 0.55), (2, 0.25), (-2, 0.25)):
            d.line([(0, 470 + k), (W, 470 + k)], fill=_a((150, 128, 84), aa), width=2)
        # monitor bezels
        for x0, y0, x1, y1 in SCREENS:
            d.rounded_rectangle([x0 - 16, y0 - 16, x1 + 16, y1 + 30], 26, fill=_a((22, 26, 24), 1))
            d.rounded_rectangle([x0 - 16, y0 - 16, x1 + 16, y1 + 30], 26, outline=_a((52, 60, 56), 1), width=2)
            d.rounded_rectangle([x0, y0, x1, y1], 18, fill=_a(GLASS, 1))
            d.ellipse([x1 - 34, y1 + 10, x1 - 24, y1 + 20], fill=_a((40, 120, 70), 1))  # power lamp
        # hanging lamp: flex, conical shade
        d.line([(LAMP_X, 0), (LAMP_X, 10)], fill=_a((30, 34, 32), 1), width=4)
        d.polygon([(LAMP_X - 30, 10), (LAMP_X + 30, 10), (LAMP_X + 130, 96), (LAMP_X - 130, 96)], fill=_a((20, 24, 22), 1))
        d.line([(LAMP_X - 130, 96), (LAMP_X + 130, 96)], fill=_a(WARM, 1), width=4)
        self.wall = wim

        # ---- light cone with dust (static part) ----
        cone = Image.new("L", (W, H), 0)
        ImageDraw.Draw(cone).polygon([(LAMP_X - 120, 98), (LAMP_X + 120, 98), (LAMP_X + 540, 1500), (LAMP_X - 540, 1500)], fill=255)
        cone = np.asarray(cone.filter(ImageFilter.GaussianBlur(60)), np.float32) / 255
        cone *= np.clip(1 - (yy - 98) / 1500, 0, 1) ** 1.3
        self.cone = (cone[..., None] * np.array(WARM, np.float32) * 0.13).astype(np.float32)
        self.motes = [(rng.uniform(-1, 1), rng.uniform(0.05, 0.95), rng.uniform(0.6, 2.2), rng.uniform(0, 6.28))
                      for _ in range(70)]

        # ---- monitor close-up layer (for detail scenes) ----
        bz = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bd = ImageDraw.Draw(bz)
        bd.rectangle([0, 0, W, H], fill=_a((26, 30, 28), 1))
        bd.rounded_rectangle([20, 56, W - 20, H - 56], 80, outline=_a((70, 80, 74), 1), width=3)
        bd.rounded_rectangle([34, 70, W - 34, H - 70], 70, fill=(0, 0, 0, 0))
        glow = Image.new("L", (W, H), 0)
        ImageDraw.Draw(glow).rounded_rectangle([34, 70, W - 34, H - 70], 70, outline=255, width=10)
        glow = glow.filter(ImageFilter.GaussianBlur(22))
        gl = Image.new("RGBA", (W, H), _a(G, 1))
        gl.putalpha(glow.point(lambda v: int(v * 0.45)))
        self.bezel = Image.alpha_composite(bz, gl)
        sl = np.zeros((H, W, 4), np.uint8)
        sl[::4, :, 3] = 60
        sl[1::4, :, 3] = 25
        self.scan = Image.fromarray(sl, "RGBA")
        sl2 = np.zeros((240, 320, 4), np.uint8)
        sl2[::3, :, 3] = 70
        self.scan_small = Image.fromarray(sl2, "RGBA")

        # ---- film ----
        self.grain = []
        for _ in range(10):
            g = rng.normal(0, 1, (H // 2, W // 2)).astype(np.float32)
            g = np.asarray(Image.fromarray(((g * 40 + 128).clip(0, 255)).astype(np.uint8)).resize((W, H), Image.BILINEAR), np.float32)
            self.grain.append(((g - 128) / 40 * 7.5)[..., None].astype(np.float32))
        yv = (np.arange(H)[:, None] - H * 0.52) / (H / 2)
        xv = (np.arange(W)[None, :] - W / 2) / (W / 2)
        self.vig = np.clip(1 - 0.5 * (xv ** 2 + 0.55 * yv ** 2), 0.28, 1).astype(np.float32)[..., None]
        self.rng = np.random.default_rng(3)

    # ---------------------------------------------------------------- per frame
    def table(self, im, t, room_a):
        """Light the map like a lamp-lit table and put the wall behind it."""
        arr = np.asarray(im.convert("RGB"), np.float32)
        flick = 1 + 0.025 * math.sin(t * 7.3) * math.sin(t * 2.9) + 0.012 * math.sin(t * 23.0)
        lit = arr * (1 + (self.pool * flick - 1) * room_a) + self.cone * room_a * flick
        out = Image.fromarray(np.clip(lit, 0, 255).astype(np.uint8)).convert("RGBA")
        if room_a > 0.01:
            wall = self.wall if room_a > 0.99 else self._fade(self.wall, room_a)
            out = Image.alpha_composite(out, wall)
        return out

    @staticmethod
    def _fade(img, a):
        r, g, b, al = img.split()
        return Image.merge("RGBA", (r, g, b, al.point(lambda v: int(v * a))))

    def screens(self, ov, t, room_a, line_i, lines, places):
        if room_a < 0.02:
            return
        # dust in the beam
        d = ImageDraw.Draw(ov)
        for u, v, s, ph in self.motes:
            y = 110 + v * 1250 + 30 * math.sin(t * 0.3 * s + ph)
            half = 120 + (y - 98) / 1400 * 420
            x = LAMP_X + u * half * 0.9 + 22 * math.sin(t * 0.21 * s + ph * 2)
            tw = 0.5 + 0.5 * math.sin(t * s * 1.7 + ph)
            d.ellipse([x - s, y - s, x + s, y + s], fill=_a(WARM, room_a * 0.22 * tw))

        for k, (x0, y0, x1, y1) in enumerate(SCREENS):
            w, h = x1 - x0, y1 - y0
            sc = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            sd = ImageDraw.Draw(sc)
            if k == 0:
                self._radar(sd, w, h, t, len(places))
            elif k == 1:
                self._clock(sd, w, h, t, line_i, len(lines))
            else:
                self._teletype(sd, w, h, t, line_i, lines)
            glow = sc.filter(ImageFilter.GaussianBlur(6))
            sc = Image.alpha_composite(glow, sc)
            sc = Image.alpha_composite(sc, self.scan_small.crop((0, 0, w, h)))
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], 18, fill=int(255 * room_a * (0.9 + 0.1 * math.sin(t * 11 + k))))
            al = Image.fromarray(np.minimum(np.asarray(sc.split()[3]), np.asarray(mask)))
            sc.putalpha(al)
            ov.alpha_composite(sc, (x0, y0))

    def _radar(self, d, w, h, t, n):
        cx, cy, R = w / 2, h / 2 + 4, min(w, h) / 2 - 18
        for rr in (R, R * 0.66, R * 0.33):
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=_a(GD, 1), width=2)
        d.line([(cx - R, cy), (cx + R, cy)], fill=_a(GD, 1), width=1)
        d.line([(cx, cy - R), (cx, cy + R)], fill=_a(GD, 1), width=1)
        ang = t * 1.6
        for j in range(22):
            aj = ang - j * 0.035
            d.line([(cx, cy), (cx + R * math.cos(aj), cy + R * math.sin(aj))], fill=_a(G, 0.55 * (1 - j / 22)), width=3)
        rng = np.random.default_rng(4)
        for j in range(max(3, min(n + 2, 8))):
            ba, br = rng.uniform(0, 6.28), rng.uniform(0.25, 0.9) * R
            since = (ang - ba) % (2 * math.pi)
            al = max(0.0, 1 - since / 4.0)
            col = RED if j == 0 else G
            d.ellipse([cx + br * math.cos(ba) - 5, cy + br * math.sin(ba) - 5, cx + br * math.cos(ba) + 5, cy + br * math.sin(ba) + 5],
                      fill=_a(col, 0.25 + 0.75 * al))

    def _clock(self, d, w, h, t, i, n):
        f_big = self.r.font_("mono", 48)
        f_sm = self.r.font_("mono", 22)
        s = f"T+{int(t // 60):02d}:{t % 60:04.1f}"
        d.text(((w - f_big.getlength(s)) / 2, 52), s, font=f_big, fill=_a(G, 1))
        lab = "OUTPOST / MAP ROOM"
        d.text(((w - f_sm.getlength(lab)) / 2, 22), lab, font=f_sm, fill=_a(GM, 1))
        # progress ticks: one per line of the story
        tw = (w - 60) / max(1, n)
        for j in range(n):
            x = 30 + j * tw
            on = j <= i
            d.rectangle([x + 2, 140, x + tw - 4, 154], fill=_a(RED if j == i else (G if on else GD), 1 if on else 0.8))
        rg = (self.r.s.get("region") or "").upper()[:18]
        d.text(((w - f_sm.getlength(rg)) / 2, 168), rg, font=f_sm, fill=_a(GM, 1))

    def _teletype(self, d, w, h, t, i, lines):
        f = self.r.font_("mono", 19)
        rows = []
        for j in range(i + 1):
            nt = (lines[j].get("note") or "").upper()
            if nt:
                rows.append(f"> {nt}"[:24])
        rows = rows[-7:]
        y = 16
        for j, rw in enumerate(rows):
            last = j == len(rows) - 1
            d.text((14, y), rw, font=f, fill=_a(G if last else GM, 1 if last else 0.75))
            y += 25
        if int(t * 2.2) % 2 == 0:
            d.rectangle([14, y + 2, 26, y + 20], fill=_a(G, 1))

    def monitor(self, ov, amt):
        """Detail scenes: we are looking at a monitor, close up."""
        if amt < 0.02:
            return
        ov.alpha_composite(self.scan if amt > 0.99 else self._fade(self.scan, amt))
        ov.alpha_composite(self.bezel if amt > 0.99 else self._fade(self.bezel, amt))

    def film(self, arr, fi, t):
        arr = arr * self.vig + self.grain[fi % len(self.grain)]
        # gentle gate weave
        dx = int(round(1.6 * math.sin(t * 1.7) + 0.8 * math.sin(t * 5.1)))
        dy = int(round(1.2 * math.sin(t * 1.3 + 1)))
        if dx or dy:
            arr = np.roll(arr, (dy, dx), (0, 1))
        # the odd dust speck / hair
        rr = np.random.default_rng(fi * 7 + 1)
        if rr.random() < 0.10:
            x, y = int(rr.uniform(80, W - 80)), int(rr.uniform(120, H - 120))
            s = int(rr.uniform(2, 5))
            arr[y - s:y + s, x - s:x + s] *= 0.35
        if rr.random() < 0.035:
            x = int(rr.uniform(100, W - 100))
            arr[:, x:x + 2] = arr[:, x:x + 2] * 0.7 + 30
        return arr


def typewriter(d, text, prog, a, font_, ink, bg):
    """Teletype subtitles: characters land as the line is spoken."""
    f = font_("mono", 40)
    from .render import wrap
    rows = wrap(text, f, 900)
    total = sum(len(r) for r in rows)
    shown = int(round(total * max(0.0, min(1.0, prog))))
    y0 = 1470 - len(rows) * 56 / 2
    left = shown
    for i, r in enumerate(rows):
        part = r[:max(0, left)]
        left -= len(r) + 0
        x = (W - f.getlength(r)) / 2
        if part:
            d.text((x, y0 + i * 56), part, font=f, fill=_a(ink, a), stroke_width=3, stroke_fill=_a(bg, a * 0.7))
        if 0 < len(part) < len(r) or (i == len(rows) - 1 and shown >= total and False):
            cx = x + f.getlength(part) + 4
            d.rectangle([cx, y0 + i * 56 + 6, cx + 20, y0 + i * 56 + 46], fill=_a(G, a))
        if left <= 0:
            break
