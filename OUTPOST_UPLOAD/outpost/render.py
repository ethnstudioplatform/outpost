"""Green phosphor war-room renderer. PIL frames piped straight into ffmpeg."""
import math
import random
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config as C

W, H, FPS = C.W, C.H, C.FPS
BOOT = 2.2
OUTRO = 2.6
GAP = 0.35

# ---------- fonts ----------
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
    return tuple(int(v * k) for v in c)


# ---------- static layers ----------
def _crt_mask():
    y = np.arange(H)[:, None]
    scan = np.where(y % 4 < 2, 1.0, 0.72)
    xx = (np.arange(W)[None, :] - W / 2) / (W / 2)
    yy = (np.arange(H)[:, None] - H / 2) / (H / 2)
    vig = 1 - 0.38 * (xx ** 2 * 0.9 + yy ** 2 * 0.6)
    return (scan * np.clip(vig, 0.35, 1))[..., None].astype(np.float32)


def _static_bg():
    im = Image.new("RGB", (W, H), C.BG)
    d = ImageDraw.Draw(im)
    for x in range(0, W, 60):
        d.line([(x, 0), (x, H)], fill=C.GREEN_FAINT)
    for y in range(0, H, 60):
        d.line([(0, y), (W, y)], fill=C.GREEN_FAINT)
    # frames
    d.rectangle([40, 120, W - 40, 1560], outline=C.GREEN_DIM, width=2)
    d.line([(40, 290), (W - 40, 290)], fill=C.GREEN_DIM, width=2)
    d.line([(40, 720), (W - 40, 720)], fill=C.GREEN_DIM, width=2)
    d.line([(40, 1410), (W - 40, 1410)], fill=C.GREEN_DIM, width=2)
    # corner ticks
    for (x, y) in [(40, 120), (W - 40, 120), (40, 1560), (W - 40, 1560)]:
        d.rectangle([x - 6, y - 6, x + 6, y + 6], fill=C.GREEN_MID)
    d.text((70, 300), "SWEEP", font=font(30), fill=C.GREEN_DIM)
    d.text((500, 300), "STATUS BOARD", font=font(30), fill=C.GREEN_DIM)
    return im


# ---------- timeline ----------
def build_timeline(line_durs):
    t = BOOT
    tl = []
    for d in line_durs:
        tl.append((t, d))
        t += d + GAP
    return tl, t + OUTRO


# ---------- frame ----------
class Renderer:
    def __init__(self, script, timeline, total):
        self.s = script
        self.tl = timeline
        self.total = total
        self.bg = _static_bg()
        self.mask = _crt_mask()
        self.rng = random.Random(42)
        self.start_dt = datetime.fromisoformat(script["generated_utc"])
        blips = min(max(script.get("signal_count", 6), 4), 14)
        r = random.Random(script.get("headline", ""))
        self.blips = [(r.uniform(0, 360), r.uniform(0.2, 0.95)) for _ in range(blips)]
        self.ticker = "   ///   ".join(t.upper() for t in script.get("ticker", [])) + "   ///   "
        self.lines = script["lines"]
        self.src_tags = []
        for ln in self.lines:
            if ln.get("context"):
                self.src_tags.append("BACKGROUND CONTEXT")
            elif ln["src"]:
                outlets = []
                for n in ln["src"]:
                    for s in script["sources"]:
                        if s["n"] == n and s["outlet"].upper() not in outlets:
                            outlets.append(s["outlet"].upper())
                self.src_tags.append("SRC: " + " + ".join(outlets[:2]))
            else:
                self.src_tags.append("")

    # --- pieces ---
    def header(self, d, t):
        d.text((70, 135), C.BRAND, font=font(110), fill=C.GREEN)
        blink = int(t * 2) % 2 == 0
        d.ellipse([W - 250, 168, W - 226, 192], fill=C.AMBER if blink else mix(C.AMBER, 0.3))
        d.text((W - 212, 150), "LIVE", font=font(58), fill=C.AMBER)
        d.text((W - 300, 212), C.HANDLE, font=font(36), fill=C.GREEN_MID)
        now = self.start_dt + timedelta(seconds=t)
        d.text((72, 240), f"{C.TAGLINE}  //  {now.strftime('%d %b %Y  %H:%M:%S').upper()}Z",
               font=font(38), fill=C.GREEN_MID)

    def radar(self, d, t):
        cx, cy, R = 265, 520, 165
        for k in (1, 2, 3):
            d.ellipse([cx - R * k / 3, cy - R * k / 3, cx + R * k / 3, cy + R * k / 3],
                      outline=C.GREEN_DIM, width=2)
        d.line([(cx - R, cy), (cx + R, cy)], fill=C.GREEN_DIM)
        d.line([(cx, cy - R), (cx, cy + R)], fill=C.GREEN_DIM)
        for deg in range(0, 360, 15):
            a = math.radians(deg)
            r0 = R - (10 if deg % 45 else 18)
            d.line([(cx + r0 * math.cos(a), cy + r0 * math.sin(a)),
                    (cx + R * math.cos(a), cy + R * math.sin(a))], fill=C.GREEN_MID)
        sweep = (t * 110) % 360
        for k in range(28):
            a = math.radians(sweep - k * 1.6)
            col = mix(C.GREEN, (1 - k / 28) ** 2 * 0.9)
            d.line([(cx, cy), (cx + R * math.cos(a), cy + R * math.sin(a))], fill=col, width=3)
        for ang, rad in self.blips:
            since = (sweep - ang) % 360
            glow = max(0.0, 1 - since / 300)
            if glow > 0.05:
                x = cx + R * rad * math.cos(math.radians(ang))
                y = cy + R * rad * math.sin(math.radians(ang))
                s = 5 + 4 * glow
                d.ellipse([x - s, y - s, x + s, y + s], fill=mix(C.GREEN, 0.25 + 0.75 * glow))
        d.text((cx - R, cy + R + 10), f"BRG {int(sweep):03d}", font=font(30), fill=C.GREEN_MID)

    def status(self, d, t):
        x, y = 500, 350
        rows = [
            ("REGION", self.s.get("region", "GLOBAL")[:20]),
            ("SIGNALS", str(self.s.get("signal_count", 0))),
            ("OUTLETS", str(self.s.get("outlet_count", 0))),
            ("CITED", str(len(self.s.get("sources", [])))),
            ("STATUS", "MONITORING" if int(t * 1.5) % 2 == 0 else ""),
        ]
        for i, (k, v) in enumerate(rows):
            yy = y + i * 58
            d.text((x, yy), k, font=font(44), fill=C.GREEN_MID)
            dots = "." * 12
            d.text((x + 150, yy), dots, font=font(44), fill=C.GREEN_DIM)
            d.text((x + 290, yy), v, font=font(44), fill=C.GREEN)
        bars = 10
        lvl = 6 + int(3 * (0.5 + 0.5 * math.sin(t * 3.1))) + (1 if self.rng.random() > 0.8 else 0)
        yy = y + 5 * 58 + 6
        d.text((x, yy), "UPLINK", font=font(44), fill=C.GREEN_MID)
        for b in range(bars):
            bx = x + 180 + b * 30
            d.rectangle([bx, yy + 10, bx + 20, yy + 38], fill=C.GREEN if b < lvl else C.GREEN_DIM)

    def headline(self, d):
        d.text((70, 735), f"> SITREP  //  {self.s['stamp']}", font=font(40), fill=C.GREEN_MID)
        hl = wrap(self.s["headline"], font(76), W - 140)[:2]
        for i, l in enumerate(hl):
            d.text((70, 780 + i * 70), l, font=font(76), fill=C.GREEN)
        return 780 + len(hl) * 70 + 20

    def log(self, d, t, top):
        f, fs = font(50), font(32)
        blocks = []
        for i, ((st, du), ln) in enumerate(zip(self.tl, self.lines)):
            if t < st:
                break
            full = "> " + ln["text"]
            type_dur = max(0.4, min(du * 0.8, len(full) * 0.04))
            k = min(1.0, (t - st) / type_dur)
            shown = full[: int(len(full) * k)]
            active = (i == len(self.lines) - 1 or t < self.tl[i + 1][0]) and t < st + du + GAP
            rows = wrap(shown, f, W - 150) or [""]
            blocks.append((rows, self.src_tags[i] if k >= 1 else "", active, k < 1 or active))
        # layout from bottom-limited area, drop oldest if overflow
        bottom = 1395
        heights = [len(r) * 54 + (40 if tag else 8) + 14 for r, tag, _, _ in blocks]
        while blocks and top + sum(heights) > bottom:
            blocks.pop(0); heights.pop(0)
        y = top
        for (rows, tag, active, cur), hgt in zip(blocks, heights):
            col = C.GREEN if active else C.GREEN_MID
            for j, r in enumerate(rows):
                d.text((70, y + j * 54), r, font=f, fill=col)
            if active and cur and int(t * 2.5) % 2 == 0:
                lx = 70 + f.getlength(rows[-1]) + 6
                ly = y + (len(rows) - 1) * 54
                d.rectangle([lx, ly + 8, lx + 24, ly + 50], fill=C.GREEN)
            if tag:
                d.text((100, y + len(rows) * 54 + 2), tag, font=fs, fill=C.GREEN_DIM if not active else C.GREEN_MID)
            y += hgt

    def ticker_bar(self, im, t):
        f = font(40)
        x0, x1, y0, y1 = 202, W - 42, 1412, 1470
        strip = Image.new("RGB", (x1 - x0, y1 - y0), C.BG)
        sd = ImageDraw.Draw(strip)
        tw = max(f.getlength(self.ticker), 1)
        x = 12 - (t * 140) % tw
        while x < strip.width:
            sd.text((x, 6), self.ticker, font=f, fill=C.GREEN_MID)
            x += tw
        im.paste(strip, (x0, y0))
        d = ImageDraw.Draw(im)
        d.rectangle([40, y0, 200, y1], fill=C.GREEN_DIM, outline=C.GREEN_DIM, width=2)
        d.text((62, 1418), "WIRE", font=f, fill=C.GREEN)

    def progress(self, d, t):
        p = min(1, t / self.total)
        d.rectangle([70, 1500, W - 70, 1512], outline=C.GREEN_DIM)
        d.rectangle([70, 1500, 70 + (W - 140) * p, 1512], fill=C.GREEN_MID)
        d.text((70, 1518), f"TX {int(t)//60:02d}:{int(t)%60:02d} / {int(self.total)//60:02d}:{int(self.total)%60:02d}",
               font=font(30), fill=C.GREEN_DIM)
        d.text((W - 390, 1518), "SOURCES IN CAPTION", font=font(30), fill=C.GREEN_DIM)

    def boot(self, d, t):
        msgs = ["OUTPOST TERMINAL v0.1", "ESTABLISHING UPLINK ...... OK",
                "DECRYPTING WIRE FEED ..... OK", f"SIGNALS ACQUIRED ......... {self.s.get('signal_count', 0)}",
                "BEGIN TRANSMISSION"]
        f = font(52)
        for i, m in enumerate(msgs):
            st = 0.15 + i * 0.38
            if t < st:
                break
            k = min(1, (t - st) / 0.3)
            d.text((90, 700 + i * 70), m[: int(len(m) * k)], font=f, fill=C.GREEN)

    def outro(self, d, t):
        k = min(1, (t - (self.total - OUTRO)) / 0.4)
        box = [110, 760, W - 110, 1160]
        d.rectangle(box, fill=C.BG, outline=C.GREEN, width=3)
        msgs = [("END TRANSMISSION", 84, C.GREEN), ("", 20, C.GREEN),
                (f"FOLLOW {C.HANDLE.upper()}", 52, C.GREEN_MID), ("SOURCES IN CAPTION", 44, C.GREEN_MID)]
        y = 820
        for m, sz, col in msgs:
            f = font(sz)
            shown = m[: int(len(m) * k)]
            d.text(((W - f.getlength(m)) / 2, y), shown, font=f, fill=col)
            y += sz + 26

    def frame(self, t):
        im = self.bg.copy() if t >= BOOT else Image.new("RGB", (W, H), C.BG)
        d = ImageDraw.Draw(im)
        if t < BOOT:
            self.boot(d, t)
        else:
            self.header(d, t)
            self.radar(d, t)
            self.status(d, t)
            top = self.headline(d)
            self.log(d, t, top)
            self.ticker_bar(im, t)
            self.progress(d, t)
            if t > self.total - OUTRO:
                k = min(1, (t - (self.total - OUTRO)) / 0.3)
                im = Image.blend(im, Image.new("RGB", (W, H), C.BG), 0.78 * k)
                self.outro(ImageDraw.Draw(im), t)
        # phosphor glow
        small = im.resize((W // 4, H // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(5))
        glow = np.asarray(small.resize((W, H), Image.BILINEAR), dtype=np.float32)
        arr = np.asarray(im, dtype=np.float32) + glow * 0.75
        flick = 0.96 + 0.04 * self.rng.random()
        if t < BOOT + 0.25 and t >= BOOT:  # power-on flash
            flick *= 0.6 + 1.6 * (t - BOOT)
        arr = arr * self.mask * flick
        # rolling refresh band
        band_y = int((t * 420) % (H + 300)) - 150
        y0, y1 = max(0, band_y), min(H, band_y + 120)
        if y1 > y0:
            arr[y0:y1] *= 1.08
        return np.clip(arr, 0, 255).astype(np.uint8)


def render(script, timeline, total, audio: Path, out: Path, thumb: Path):
    r = Renderer(script, timeline, total)
    n = int(total * FPS)
    proc = subprocess.Popen([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", str(audio),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE)
    thumb_t = timeline[min(2, len(timeline) - 1)][0] + 2.5 if timeline else BOOT + 1
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
