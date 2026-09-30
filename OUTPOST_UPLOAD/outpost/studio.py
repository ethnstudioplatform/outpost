"""OUTPOST TONIGHT: anchor-desk bulletin scenes (opt-in per line).

Scene types:
  "studio"  first line only: wide green war room, camera pushes in from frame 0,
            scan lines tighten, passes through the centre screen, lands on the desk.
  "desk"    the OUTPOST desk with an illustrated cartoon anchor (original character),
            an over-the-shoulder box and the OUTPOST TONIGHT lower third.
Line key "ticker": text for the lower-third strap, kept on screen (also over map
scenes) until the next line that sets one.
"""
import math
import sys

from PIL import Image, ImageDraw, ImageFilter

PUSH_END = 2.7      # the wide shot is gone before 3s
SS = 2              # supersampling for the illustrated parts

SKIN = (222, 176, 142)
SKIN_D = (196, 146, 114)
HAIR = (44, 30, 26)
HAIR_L = (70, 50, 42)
SUIT = (20, 64, 44)
SUIT_L = (32, 96, 66)
SHIRT = (214, 236, 222)


def _e(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


class Studio:
    def __init__(self, r):
        self.r = r
        self.R = sys.modules[r.__class__.__module__]
        self.W, self.H = self.R.W, self.R.H
        self._room = None
        self._desk = None
        self._ots_cache = {}
        self.tickers = []
        cur = ""
        for ln in r.lines:
            if ln.get("ticker"):
                cur = ln["ticker"]
            self.tickers.append(cur)

    # ---------------- helpers ----------------
    def c(self, name):
        return getattr(self.R, name)

    def font(self, kind, size):
        return self.R.font(kind, size)

    def rgba(self, col, a):
        return self.R.rgba(col, a)

    # ---------------- the wide war room ----------------
    def _build_room(self):
        W, H = self.W, self.H
        BG, G, GM, GD, INK = (self.c(k) for k in ("BG", "G", "GM", "GD", "INK"))
        im = Image.new("RGB", (W * SS, H * SS), BG)
        d = ImageDraw.Draw(im)
        s = SS
        # back wall glow
        for k in range(26):
            a = k / 26
            col = tuple(int(BG[j] + (GD[j] - BG[j]) * (1 - a) * 0.55) for j in range(3))
            d.ellipse([(540 - 900 + k * 30) * s, (820 - 700 + k * 22) * s, (540 + 900 - k * 30) * s, (820 + 700 - k * 22) * s], fill=col)
        # floor with perspective lines
        fy = 1260
        d.rectangle([0, fy * s, W * s, H * s], fill=tuple(int(v * 0.7) for v in BG))
        for k in range(-9, 10):
            d.line([(540 * s, (fy - 60) * s), ((540 + k * 260) * s, H * s)], fill=GD, width=2 * s)
        for k in range(8):
            y = fy + (k / 8) ** 1.7 * (H - fy)
            d.line([(0, y * s), (W * s, y * s)], fill=GD, width=2 * s)
        # monitor wall: 3 rows x 3 columns, centre screen is the big one
        self.screens = []
        for row in range(3):
            for col in range(3):
                if row == 1 and col == 1:
                    continue
                x0 = 60 + col * 330
                y0 = 380 + row * 300
                self.screens.append((x0, y0, x0 + 300, y0 + 250))
        self.centre = (350, 690, 730, 950)
        for (x0, y0, x1, y1) in self.screens + [self.centre]:
            d.rounded_rectangle([(x0 - 10) * s, (y0 - 10) * s, (x1 + 10) * s, (y1 + 10) * s], radius=14 * s, fill=(14, 22, 18))
            d.rectangle([x0 * s, y0 * s, x1 * s, y1 * s], fill=tuple(int(v * 0.55) for v in GD))
        # static screen content: map lines, bars, text rows
        for j, (x0, y0, x1, y1) in enumerate(self.screens):
            kind = j % 4
            if kind == 0:
                for q in range(6):
                    y = y0 + 30 + q * 34
                    d.rectangle([(x0 + 20) * s, y * s, (x0 + 20 + (60 + (q * 53) % 180)) * s, (y + 12) * s], fill=GM)
            elif kind == 1:
                for q in range(9):
                    h = 30 + (q * 37) % 150
                    d.rectangle([(x0 + 22 + q * 29) * s, (y1 - 20 - h) * s, (x0 + 40 + q * 29) * s, (y1 - 20) * s], fill=GM)
            elif kind == 2:
                pts = [((x0 + 15 + q * 27) * s, (y0 + 125 + 60 * math.sin(q * 0.9 + j)) * s) for q in range(11)]
                d.line(pts, fill=G, width=4 * s)
            else:
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                for rr in (40, 80, 110):
                    d.ellipse([(cx - rr) * s, (cy - rr) * s, (cx + rr) * s, (cy + rr) * s], outline=GM, width=2 * s)
        # consoles in the foreground, operators as plain silhouettes
        for k, cx in enumerate((170, 540, 910)):
            top = 1330 + (40 if k != 1 else 0)
            d.rounded_rectangle([(cx - 190) * s, top * s, (cx + 190) * s, (top + 260) * s], radius=18 * s, fill=(10, 18, 14))
            d.rectangle([(cx - 170) * s, (top + 16) * s, (cx + 170) * s, (top + 24) * s], fill=GM)
            if k != 1:
                hx, hy = cx + 30, top - 130
                d.ellipse([(hx - 42) * s, (hy - 50) * s, (hx + 42) * s, (hy + 40) * s], fill=(8, 14, 11))
                d.rounded_rectangle([(hx - 90) * s, (hy + 30) * s, (hx + 90) * s, (top + 30) * s], radius=40 * s, fill=(8, 14, 11))
        # ceiling strip lights
        for k in range(5):
            x = 120 + k * 210
            d.rectangle([(x - 60) * s, 250 * s, (x + 60) * s, 262 * s], fill=G)
        self._room = im.resize((W, H), Image.LANCZOS)

    def _room_frame(self, t):
        W, H = self.W, self.H
        G, GM, INK, RED = (self.c(k) for k in ("G", "GM", "INK", "RED"))
        base = self._room.copy()
        d = ImageDraw.Draw(base, "RGBA")
        # centre screen: radar sweep and blips
        x0, y0, x1, y1 = self.centre
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for rr in (40, 80, 120):
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=self.rgba(GM, 0.9), width=2)
        ang = t * 2.4
        for k in range(14):
            a2 = ang - k * 0.05
            d.line([(cx, cy), (cx + 124 * math.cos(a2), cy + 124 * math.sin(a2))], fill=self.rgba(G, 0.8 * (1 - k / 14)), width=4)
        for k, (bx, by) in enumerate(((50, -30), (-70, 40), (20, 80))):
            ph = (t * 0.7 + k * 0.33) % 1
            d.ellipse([cx + bx - 7, cy + by - 7, cx + bx + 7, cy + by + 7], fill=self.rgba(RED, 1 - ph))
        d.text((x0 + 14, y0 + 10), "OUTPOST", font=self.font("mono", 26), fill=self.rgba(G, 0.9))
        # blinking tell-tales on the side screens
        for j, (sx0, sy0, sx1, sy1) in enumerate(self.screens):
            if int(t * 3 + j) % 4 == 0:
                d.rectangle([sx1 - 26, sy0 + 12, sx1 - 12, sy0 + 26], fill=self.rgba(RED, 0.9))
        # push in on the centre screen
        p = _e(t / PUSH_END) ** 1.6
        scale = 1 + 3.6 * p
        fx = W / 2 + (cx - W / 2) * _e(t / (PUSH_END * 0.7))
        fy = H / 2 + (cy - H / 2) * _e(t / (PUSH_END * 0.7))
        cw, ch = W / scale, H / scale
        fx = min(max(fx, cw / 2), W - cw / 2)
        fy = min(max(fy, ch / 2), H - ch / 2)
        box = (fx - cw / 2, fy - ch / 2, fx + cw / 2, fy + ch / 2)
        im = base.resize((W, H), Image.BILINEAR, box=box)
        # scan lines tighten as we close in, then a green wash as we pass through the glass
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        gap = 16 - 11 * p
        la = 0.10 + 0.35 * p
        y = (t * 60) % gap
        while y < H:
            od.line([(0, y), (W, y)], fill=self.rgba((0, 0, 0), la), width=2)
            y += gap
        wash = _e((t - PUSH_END + 0.45) / 0.45)
        if wash > 0:
            od.rectangle([0, 0, W, H], fill=self.rgba(G, 0.55 * wash))
        return Image.alpha_composite(im.convert("RGBA"), ov)

    # ---------------- the desk ----------------
    def _build_desk(self):
        W, H = self.W, self.H
        BG, G, GM, GD, INK = (self.c(k) for k in ("BG", "G", "GM", "GD", "INK"))
        s = SS
        im = Image.new("RGB", (W * s, H * s), BG)
        d = ImageDraw.Draw(im)
        # soft back wall of monitors (out of focus)
        for row in range(4):
            for col in range(4):
                x0, y0 = -40 + col * 290, 250 + row * 230
                d.rectangle([x0 * s, y0 * s, (x0 + 260) * s, (y0 + 200) * s], fill=tuple(int(v * 0.6) for v in GD))
                d.rectangle([(x0 + 20) * s, (y0 + 30) * s, (x0 + 20 + (80 + (row * 70 + col * 50) % 160)) * s, (y0 + 44) * s], fill=GM)
        im = im.filter(ImageFilter.GaussianBlur(7 * s))
        d = ImageDraw.Draw(im)
        # rim light behind the anchor
        for k in range(18):
            rr = 420 - k * 18
            col = tuple(int(BG[j] * 0.3 + GD[j] * 0.7 + (G[j] - GD[j]) * 0.02 * k) for j in range(3))
            d.ellipse([(400 - rr) * s, (800 - rr) * s, (400 + rr) * s, (800 + rr) * s], fill=col)
        im = im.filter(ImageFilter.GaussianBlur(3 * s))
        d = ImageDraw.Draw(im)
        # chair back
        d.rounded_rectangle([230 * s, 800 * s, 570 * s, 1150 * s], radius=70 * s, fill=(12, 26, 20))
        # body: shoulders and blazer
        d.rounded_rectangle([200 * s, 930 * s, 600 * s, 1200 * s], radius=110 * s, fill=SUIT)
        d.polygon([(330 * s, 935 * s), (470 * s, 935 * s), (400 * s, 1090 * s)], fill=SHIRT)
        d.polygon([(318 * s, 935 * s), (352 * s, 935 * s), (400 * s, 1100 * s), (360 * s, 1110 * s)], fill=SUIT_L)
        d.polygon([(482 * s, 935 * s), (448 * s, 935 * s), (400 * s, 1100 * s), (440 * s, 1110 * s)], fill=SUIT_L)
        d.ellipse([418 * s, 1050 * s, 434 * s, 1066 * s], fill=G)       # small green lapel pin
        # neck
        d.rectangle([368 * s, 870 * s, 432 * s, 945 * s], fill=SKIN_D)
        # desk: top surface and curved front with the logo
        d.polygon([(0, 1120 * s), (W * s, 1120 * s), (W * s, 1170 * s), (0, 1170 * s)], fill=(30, 52, 42))
        d.rectangle([0, 1170 * s, W * s, 1420 * s], fill=(12, 22, 17))
        d.rectangle([0, 1170 * s, W * s, 1178 * s], fill=G)
        # hands and papers on the desk
        d.rounded_rectangle([250 * s, 1100 * s, 560 * s, 1150 * s], radius=6 * s, fill=(236, 240, 232))
        d.ellipse([240 * s, 1098 * s, 320 * s, 1146 * s], fill=SKIN)
        d.ellipse([470 * s, 1098 * s, 550 * s, 1146 * s], fill=SKIN)
        d.rounded_rectangle([200 * s, 1106 * s, 262 * s, 1140 * s], radius=14 * s, fill=SUIT)
        d.rounded_rectangle([538 * s, 1106 * s, 600 * s, 1140 * s], radius=14 * s, fill=SUIT)
        # mug
        d.rounded_rectangle([860 * s, 1060 * s, 920 * s, 1130 * s], radius=8 * s, fill=(26, 60, 44))
        d.arc([900 * s, 1075 * s, 945 * s, 1115 * s], -90, 90, fill=(26, 60, 44), width=8 * s)
        self._desk = im.resize((W, H), Image.LANCZOS)

    def _head(self, t, speaking, loc):
        """Head drawn per frame: blink, mouth, a small nod. Returns (image, x, y)."""
        s = SS
        w, h = 300, 330
        im = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        cx, cy = 150, 170
        # hair back (bob)
        d.rounded_rectangle([(cx - 112) * s, (cy - 120) * s, (cx + 112) * s, (cy + 120) * s], radius=90 * s, fill=HAIR)
        # face
        d.ellipse([(cx - 84) * s, (cy - 100) * s, (cx + 84) * s, (cy + 104) * s], fill=SKIN)
        # ears + earpiece
        d.ellipse([(cx - 96) * s, (cy - 6) * s, (cx - 72) * s, (cy + 30) * s], fill=SKIN_D)
        d.ellipse([(cx + 72) * s, (cy - 6) * s, (cx + 96) * s, (cy + 30) * s], fill=SKIN_D)
        d.ellipse([(cx + 80) * s, (cy + 4) * s, (cx + 92) * s, (cy + 16) * s], fill=(30, 30, 30))
        # fringe
        d.chord([(cx - 100) * s, (cy - 136) * s, (cx + 104) * s, (cy + 10) * s], 180, 360, fill=HAIR)
        d.polygon([(cx - 20) * s, (cy - 62) * s, (cx + 104) * s, (cy - 62) * s, (cx + 104) * s, (cy - 30) * s], fill=HAIR)
        d.line([(cx - 60) * s, (cy - 90) * s, (cx + 20) * s, (cy - 70) * s], fill=HAIR_L, width=4 * s)
        # brows
        lift = 4 if speaking and math.sin(t * 1.7) > 0.7 else 0
        for sx in (-1, 1):
            d.line([(cx + sx * 22) * s, (cy - 26 - lift) * s, (cx + sx * 54) * s, (cy - 30 - lift) * s], fill=HAIR, width=6 * s)
        # eyes, blink every few seconds
        blink = (t % 3.7) < 0.13
        for sx in (-1, 1):
            ex = cx + sx * 38
            if blink:
                d.line([(ex - 13) * s, (cy + 2) * s, (ex + 13) * s, (cy + 2) * s], fill=HAIR, width=4 * s)
            else:
                d.ellipse([(ex - 13) * s, (cy - 10) * s, (ex + 13) * s, (cy + 12) * s], fill=(250, 250, 246))
                d.ellipse([(ex - 7) * s, (cy - 6) * s, (ex + 7) * s, (cy + 9) * s], fill=(40, 60, 50))
                d.ellipse([(ex - 1) * s, (cy - 4) * s, (ex + 3) * s, (cy) * s], fill=(255, 255, 255))
        # nose
        d.line([(cx + 2) * s, (cy + 12) * s, (cx - 6) * s, (cy + 40) * s, (cx + 6) * s, (cy + 42) * s], fill=SKIN_D, width=4 * s)
        # mouth: opens and closes while the narrator speaks
        if speaking:
            o = abs(math.sin(t * 12.5)) * (0.55 + 0.45 * math.sin(t * 3.3 + 1)) * _e(loc / 0.15)
        else:
            o = 0.0
        mh = 3 + 20 * o
        d.rounded_rectangle([(cx - 26) * s, (cy + 60 - mh / 2) * s, (cx + 26) * s, (cy + 60 + mh / 2) * s], radius=int(min(12, mh / 2 + 1) * s), fill=(120, 44, 44))
        if mh > 8:
            d.rectangle([(cx - 18) * s, (cy + 60 - mh / 2) * s, (cx + 18) * s, (cy + 60 - mh / 2 + 5) * s], fill=(250, 250, 246))
        im = im.resize((w, h), Image.LANCZOS)
        nod = 3 * math.sin(t * 2.1) if speaking else 1.5 * math.sin(t * 0.8)
        return im, int(400 - w / 2), int(700 - h / 2 + nod)

    def _ots(self, i, sc):
        """Over-the-shoulder box: a still of the map at the story's place, with a live pin."""
        pid = sc.get("ots")
        if not pid or pid not in self.r.places:
            return None
        if pid not in self._ots_cache:
            p = self.r.places[pid]
            x, y = self.r.T(p["lon"], p["lat"])
            cam = (x, y, self.r._deg(float(sc.get("ots_span", 5.0))), 0.0)
            full = self.r.map_layer(cam, 1.0)
            bw, bh = 440, 300
            top = int(self.H * self.R.HORIZ - 380)
            crop = full.crop((0, top, self.W, top + int(self.W * bh / bw))).resize((bw, bh), Image.LANCZOS)
            pr = self.r.project(p["lon"], p["lat"], cam)
            px = pr[0] * bw / self.W if pr else bw / 2
            py = (pr[1] - top) * bh / (self.W * bh / bw) if pr else bh / 2
            self._ots_cache[pid] = (crop, px, py)
        return self._ots_cache[pid]

    def desk(self, t, i, loc, sc, speaking, a_in):
        W, H = self.W, self.H
        G, GM, INK, RED, BG = (self.c(k) for k in ("G", "GM", "INK", "RED", "BG"))
        if self._desk is None:
            self._build_desk()
        im = self._desk.copy().convert("RGBA")
        head, hx, hy = self._head(t, speaking, loc)
        im.alpha_composite(head, (hx, hy))
        d = ImageDraw.Draw(im, "RGBA")
        # over-the-shoulder box
        ots = self._ots(i, sc)
        if ots:
            crop, px, py = ots
            e = _e((loc - 0.05) / 0.35)
            bx, by = 600, 380 + (1 - e) * 20
            d.rectangle([bx - 8, by - 8, bx + 448, by + 308], fill=self.rgba(G, e))
            box = crop.copy().convert("RGBA")
            bd = ImageDraw.Draw(box, "RGBA")
            ph = (t * 0.8) % 1
            rr = 10 + ph * 34
            bd.ellipse([px - rr, py - rr * 0.65, px + rr, py + rr * 0.65], outline=self.rgba(RED, 1 - ph), width=3)
            bd.polygon([(px, py - 11), (px + 11, py), (px, py + 11), (px - 11, py)], fill=self.rgba(RED, 1))
            box.putalpha(int(255 * e))
            im.alpha_composite(box, (int(bx), int(by)))
            lab = (sc.get("ots_label") or "").upper()
            if lab:
                fl = self.font("sans", 38)
                while fl.getlength(lab) > 410 and fl.size > 24:
                    fl = self.font("sans", fl.size - 2)
                d.rectangle([bx, by + 250, bx + fl.getlength(lab) + 28, by + 300], fill=self.rgba(BG, 0.85 * e))
                d.text((bx + 14, by + 254), lab, font=fl, fill=self.rgba(INK, e))
        return im

    # ---------------- lower third ----------------
    def strap(self, d, t, i, st, a=1.0, studio_line=False):
        """OUTPOST TONIGHT bug plus the story ticker, kept over maps as well."""
        if i >= len(self.tickers):
            return
        text = self.tickers[i]
        G, INK, RED, BG = (self.c(k) for k in ("G", "INK", "RED", "BG"))
        y = 1180
        f1, f2 = self.font("sans", 34), self.font("sans", 44)
        bug = "OUTPOST TONIGHT"
        e = _e((t - PUSH_END - 0.1) / 0.35) if studio_line else 1.0
        e *= a
        if e <= 0:
            return
        w1 = f1.getlength(bug) + 30
        d.rectangle([40, y, 40 + w1, y + 50], fill=self.rgba(RED, e))
        d.text((55, y + 6), bug, font=f1, fill=self.rgba((255, 255, 255), e))
        live = 0.6 + 0.4 * math.sin(t * 5)
        d.ellipse([52 + w1, y + 16, 70 + w1, y + 34], fill=self.rgba(RED, e * live))
        if text:
            sz = 44
            while f2.getlength(text) > 960 and sz > 30:
                sz -= 2
                f2 = self.font("sans", sz)
            fresh = self.tickers[i - 1] != text if i > 0 else True
            k = _e((t - st + 0.1) / 0.35) if fresh else 1.0
            w2 = min(1000, f2.getlength(text) + 36)
            d.rectangle([40, y + 50, 40 + w2 * k, y + 116], fill=self.rgba((244, 247, 244), e))
            if k > 0.6:
                d.text((58, y + 58 + (44 - sz) / 2), text, font=f2, fill=self.rgba(BG, e * _e((k - 0.6) / 0.4)))
            d.rectangle([40, y + 116, 40 + w2 * k, y + 122], fill=self.rgba(G, e))

    # ---------------- entry point ----------------
    def frame(self, t, i, st, en, sc):
        """Full-frame RGBA image for a studio or desk line."""
        loc = t - st
        speaking = st <= t <= en
        if sc["type"] == "studio":
            if self._room is None:
                self._build_room()
            if t < PUSH_END:
                return self._room_frame(t)
            im = self.desk(t, i, t - PUSH_END, sc, speaking, 1.0)
            flash = 1 - _e((t - PUSH_END) / 0.3)
            if flash > 0:
                ov = Image.new("RGBA", (self.W, self.H), self.rgba(self.c("G"), 0.55 * flash))
                im = Image.alpha_composite(im, ov)
            return im
        return self.desk(t, i, loc, sc, speaking, 1.0)
