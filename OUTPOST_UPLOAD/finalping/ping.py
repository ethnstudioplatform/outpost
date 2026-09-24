"""thefinalping: a ball drops through a hand-built path, every bounce plays the next note,
and the last landing completes the tune. No text. Timing is exact by construction:
we pick the note times first, then solve the arcs and place each pad where the ball must be.

    python ping.py melody.json out.mp4
"""
import json
import math
import subprocess
import sys
import wave

import numpy as np

W, H, FPS = 1080, 1920, 60
SR = 48000
G = 5200.0          # gravity, px/s^2
R = 26              # ball radius
BG_TOP = np.array([10, 12, 22], np.float32)
BG_BOT = np.array([4, 5, 10], np.float32)


# ------------------------------------------------------------------ colour
def hue(h, s=0.55, v=1.0):
    h = (h % 1) * 6
    i = int(h); f = h - i
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    return np.array([(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i % 6], np.float32) * 255


THEME = "night"


def pitch_colour(m):
    if THEME == "autumn":     # ember red through amber to gold
        return hue(0.005 + ((m * 5) % 12) / 12 * 0.125, s=0.78)
    return hue(((m - 60) % 12) / 12 * 0.85 + 0.02)


def spark_colour(q):
    return hue(0.01 + (q % 7) / 7 * 0.12, s=0.8) if THEME == "autumn" else hue(q)


# ------------------------------------------------------------------ course
def build(spec):
    global G
    G = float(spec.get("gravity", 5200))
    bpm = spec["bpm"]
    beat = 60 / bpm
    bpm_end = spec.get("bpm_end", bpm)
    notes = spec["notes"]                       # [[midi, beats], ...]; last = final landing
    lo, hi = min(n[0] for n in notes), max(n[0] for n in notes)
    xmap = lambda m: 190 + (m - lo) / max(1, hi - lo) * 700
    t0 = spec.get("preroll", 0.55)
    times, pts = [], []
    t, y = t0, 0.0
    start = (spec.get("start_x", 300), -0.5 * G * t0 * t0)   # released from rest above the first pad
    def arc(p0, p1, dt, k=18):
        vx, vy = (p1[0] - p0[0]) / dt, (p1[1] - p0[1] - 0.5 * G * dt * dt) / dt
        return [(p0[0] + vx * dt * u / k, p0[1] + vy * dt * u / k + 0.5 * G * (dt * u / k) ** 2) for u in range(1, k)]

    for i, nb in enumerate(notes):
        m, b = nb[0], nb[1]
        opt = nb[2] if len(nb) > 2 else {}
        if i == 0:
            x = spec.get("start_x", 300)
        elif m == notes[i - 1][0] and i < len(notes) - 1:
            x, y = pts[-1]                        # repeated note: bounce on the same pad
        else:
            dt = times[-1] and (t - times[-1])
            y_new = (max(p[1] for p in pts) + spec.get("final_drop", 420)) if i == len(notes) - 1 \
                else y + opt.get("dy", 175)
            best, bx = -1e9, None
            cands = [W / 2] if i == len(notes) - 1 else range(170, 911, 15)
            for cx in cands:
                if i < len(notes) - 1 and not (150 <= abs(cx - pts[-1][0]) <= 560):
                    continue
                dmin = min([math.hypot(cx - px, y_new - py) for px, py in pts[:-1]] + [999])
                path = arc(pts[-1], (cx, y_new), t - times[-1])
                clear = min([math.hypot(ax - px, ay - py) for ax, ay in path for px, py in pts[:-1]] + [999])
                score = min(dmin, 260) + min(clear, 120) * 1.5 - abs(cx - W / 2) * 0.05
                if score > best:
                    best, bx = score, cx
            x, y = bx, y_new
        times.append(t)
        pts.append((x, y))
        cur = bpm + (bpm_end - bpm) * i / max(1, len(notes) - 1)   # optional accelerando
        t += b * 60 / cur
    # velocities between contacts
    vel = []
    for i in range(len(pts) - 1):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        dt = times[i + 1] - times[i]
        vel.append(((x1 - x0) / dt, (y1 - y0 - 0.5 * G * dt * dt) / dt))
    pads = []
    for i, (x, y) in enumerate(pts[:-1]):
        vin = (0.0, G * t0) if i == 0 else (vel[i - 1][0], vel[i - 1][1] + G * (times[i] - times[i - 1]))
        vout = vel[i]
        nx, ny = vout[0] - vin[0], vout[1] - vin[1]
        L = math.hypot(nx, ny) or 1
        nx, ny = nx / L, ny / L
        pads.append({"x": x, "y": y, "nx": nx, "ny": ny, "m": notes[i][0], "hits": [times[i]]})
    # merge repeated notes on the same spot into one pad
    merged = []
    for p in pads:
        if merged and abs(merged[-1]["x"] - p["x"]) < 1 and abs(merged[-1]["y"] - p["y"]) < 1:
            merged[-1]["hits"] += p["hits"]
            a, b = merged[-1], p        # average the normal so one pad serves both bounces
            nx, ny = a["nx"] + b["nx"], a["ny"] + b["ny"]
            L = math.hypot(nx, ny) or 1
            a["nx"], a["ny"] = nx / L, ny / L
        else:
            merged.append(p)
    final = {"x": pts[-1][0], "y": pts[-1][1], "t": times[-1], "m": notes[-1][0]}
    end = times[-1] + spec.get("outro", 2.6)
    return {"times": times, "pts": pts, "vel": vel, "pads": merged, "final": final, "start": start,
            "t0": t0, "end": end, "notes": notes, "beat": beat}


def ball_at(c, t):
    times, pts, vel = c["times"], c["pts"], c["vel"]
    if t <= 0:
        return c["start"]
    if t < times[0]:
        return (c["start"][0], c["start"][1] + 0.5 * G * t * t)
    for i in range(len(times) - 1):
        if t < times[i + 1]:
            dt = t - times[i]
            return (pts[i][0] + vel[i][0] * dt, pts[i][1] + vel[i][1] * dt + 0.5 * G * dt * dt)
    return pts[-1]


# ------------------------------------------------------------------ drawing (SDF, anti-aliased)
def blend(buf, x0, y0, mask, col):
    h, w = mask.shape
    X0, Y0 = max(0, x0), max(0, y0)
    X1, Y1 = min(W, x0 + w), min(H, y0 + h)
    if X1 <= X0 or Y1 <= Y0:
        return
    m = mask[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0, None]
    reg = buf[Y0:Y1, X0:X1]
    reg += (col - reg) * m


def add(buf, x0, y0, mask, col):
    h, w = mask.shape
    X0, Y0 = max(0, x0), max(0, y0)
    X1, Y1 = min(W, x0 + w), min(H, y0 + h)
    if X1 <= X0 or Y1 <= Y0:
        return
    buf[Y0:Y1, X0:X1] += mask[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0, None] * col


def capsule(buf, ax, ay, bx, by, rad, col, glow=0.0, glow_r=60, alpha=1.0):
    pad = int(rad + glow_r * (1.4 if glow > 0 else 0) + 3)
    x0, x1 = int(min(ax, bx)) - pad, int(max(ax, bx)) + pad
    y0, y1 = int(min(ay, by)) - pad, int(max(ay, by)) + pad
    if x1 < 0 or y1 < 0 or x0 > W or y0 > H:
        return
    ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    px, py = xs - ax, ys - ay
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy or 1e-6
    h = np.clip((px * dx + py * dy) / L2, 0, 1)
    d = np.hypot(px - dx * h, py - dy * h) - rad
    if glow > 0:
        add(buf, x0, y0, np.exp(-(np.maximum(d, 0) / glow_r) ** 2 * 4.5) * glow, col)
    blend(buf, x0, y0, np.clip(0.5 - d, 0, 1) * alpha, col)


def ring(buf, cx, cy, rad, width, col, a):
    pad = int(rad + width + 3)
    x0, y0 = int(cx) - pad, int(cy) - pad
    ys, xs = np.mgrid[y0:y0 + 2 * pad, x0:x0 + 2 * pad].astype(np.float32)
    d = np.abs(np.hypot(xs - cx, ys - cy) - rad) - width / 2
    add(buf, x0, y0, np.clip(0.5 - d, 0, 1) * a, col)


# ------------------------------------------------------------------ frames
def render(spec, out):
    global THEME
    THEME = spec.get("theme", "night")
    autumn = THEME == "autumn"
    c = build(spec)
    n = int(c["end"] * FPS)
    ts = np.arange(n) / FPS
    by = np.array([ball_at(c, t)[1] for t in ts])
    # camera: a smooth, slightly anticipating follow of the ball
    k = np.exp(-0.5 * (np.arange(-40, 41) / 16) ** 2); k /= k.sum()
    cam = np.convolve(np.pad(by, 40, mode="edge"), k, mode="same")[40:-40]
    ys = [p[1] for p in c["pts"]] + [c["start"][1]]
    top, bot = min(ys) - 260, max(ys) + 260
    fit = min(1.0, (H * 0.86) / (bot - top))
    bg_top, bg_bot = (np.array([30, 14, 8], np.float32), np.array([9, 5, 4], np.float32)) if autumn else (BG_TOP, BG_BOT)
    grad = (bg_top[None, :] * (1 - np.linspace(0, 1, H)[:, None]) + bg_bot[None, :] * np.linspace(0, 1, H)[:, None])
    base = np.repeat(grad[:, None, :], W, axis=1).astype(np.float32)
    rng = np.random.default_rng(3)
    stars = [(rng.uniform(0, W), rng.uniform(-400, bot + 1600), rng.uniform(1, 2.4)) for _ in range(140)]
    leaves = [(rng.uniform(0, W), rng.uniform(-600, bot + 1600), rng.uniform(3.5, 5.5), rng.uniform(0, 6.3),
               rng.uniform(40, 110), rng.integers(0, 7), rng.uniform(0.35, 0.8)) for _ in range(34)]
    span_y = bot + 2200
    grain = [rng.normal(0, 2.2, (H, W, 1)).astype(np.float32) for _ in range(4)]
    ft = c["final"]["t"]

    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium",
                             "-crf", "18", "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
    trail = []
    for fi, t in enumerate(ts):
        # end: pull back to reveal the whole path, then fade for a clean loop
        z = 0.0 if t < ft + 0.35 else min(1.0, (t - ft - 0.35) / 1.2)
        z = z * z * (3 - 2 * z)
        s = 1 + (fit - 1) * z
        cy = cam[fi] * (1 - z) + ((top + bot) / 2) * z
        def S(x, y):
            return (W / 2 + (x - W / 2) * s, H * 0.42 + (y - cy) * s)
        buf = base.copy()
        if autumn:                      # falling leaves, drifting and turning, with parallax
            for lx0, ly0, lr, ph, spd, hq, dep in leaves:
                ly = (ly0 + spd * t + 600) % span_y - 600
                X = lx0 + 46 * math.sin(t * 1.1 + ph)
                Y = H * 0.42 + (ly - cy * dep) * s
                if -30 < Y < H + 30:
                    ang = ph + t * (1.2 + 0.4 * math.sin(ph))
                    L = lr * 1.4 * s
                    capsule(buf, X - math.cos(ang) * L, Y - math.sin(ang) * L * 0.6, X + math.cos(ang) * L,
                            Y + math.sin(ang) * L * 0.6, lr * 0.75 * s, spark_colour(hq) * 0.7, alpha=0.22 + 0.3 * dep)
        else:
            for sx, sy, sr in stars:    # faint parallax dust
                X, Y = sx, H * 0.42 + (sy - cy * 0.35) * s
                if -5 < Y < H + 5:
                    capsule(buf, X, Y, X, Y, sr * s, np.array([70, 80, 110], np.float32), alpha=0.5)
        bx, byy = ball_at(c, t)
        # pads
        for p in c["pads"]:
            px, py = p["x"] - p["nx"] * (R + 14), p["y"] - p["ny"] * (R + 14)
            tx, ty = -p["ny"], p["nx"]
            hl = 104 - (p["m"] - 55) * 3
            last = max([h for h in p["hits"] if h <= t], default=None)
            col = pitch_colour(p["m"])
            if last is None:
                lit, squash = 0.0, 0.0
            else:
                e = t - last
                lit = 0.35 + 0.65 * math.exp(-e * 5)
                squash = math.exp(-e * 18) * math.sin(e * 60) * 7
            ox, oy = px - p["nx"] * squash, py - p["ny"] * squash
            a1, a2 = S(ox + tx * hl, oy + ty * hl), S(ox - tx * hl, oy - ty * hl)
            dim = col * (0.28 + 0.72 * lit)
            capsule(buf, *a1, *a2, 15 * s, dim, glow=0.55 * lit, glow_r=90 * s)
            if last is not None and t - last < 0.6:
                e = t - last
                cxx, cyy = S(p["x"], p["y"])
                ring(buf, cxx, cyy, (30 + e * 260) * s, 4 * s, col, (1 - e / 0.6) * 0.8)
        # final cup
        fx, fy = c["final"]["x"], c["final"]["y"]
        landed = t >= ft
        cupcol = pitch_colour(c["final"]["m"])
        lit = 0.3 if not landed else 0.4 + 0.6 * math.exp(-(t - ft) * 1.5)
        for a, b in (((-120, -40), (-86, 46)), ((-86, 46), (86, 46)), ((86, 46), (120, -40))):
            capsule(buf, *S(fx + a[0], fy + a[1] + R - 20), *S(fx + b[0], fy + b[1] + R - 20), 13 * s,
                    cupcol * (0.35 + 0.65 * lit), glow=0.7 * lit, glow_r=120 * s)
        if landed:
            e = t - ft
            for k_, sp in enumerate((420, 700, 1000)):
                if e < 1.6:
                    ring(buf, *S(fx, fy), (40 + e * sp) * s, (7 - k_ * 2) * s, cupcol, max(0, 1 - e / 1.6))
            # every pad re-lights in a sweep: the whole tune, visible at once
            for j, p in enumerate(c["pads"]):
                dtj = e - 0.05 * (len(c["pads"]) - j)
                if 0 < dtj < 1.2:
                    px, py = S(p["x"], p["y"])
                    capsule(buf, px, py, px, py, 20 * s, pitch_colour(p["m"]), glow=0.6 * math.exp(-dtj * 3),
                            glow_r=140 * s, alpha=0)
            # sparks
            rs = np.random.default_rng(11)
            for q in range(26):
                ang = rs.uniform(-math.pi, 0); v = rs.uniform(500, 1100)
                sx, sy = fx + math.cos(ang) * v * e, fy + math.sin(ang) * v * e + 0.5 * G * 0.4 * e * e
                if e < 1.3:
                    X, Y = S(sx, sy)
                    capsule(buf, X, Y, X, Y, 5 * s, spark_colour(q) if autumn else hue(q / 26), glow=0.4, glow_r=30 * s, alpha=max(0, 1 - e / 1.3))
        # trail + ball
        trail.append((bx, byy))
        trail = trail[-14:]
        for j, (tx_, ty_) in enumerate(trail[:-1]):
            X, Y = S(tx_, ty_)
            capsule(buf, X, Y, X, Y, R * s * (0.3 + 0.6 * j / len(trail)),
                    np.array([255, 196, 140] if autumn else [200, 215, 255], np.float32),
                    alpha=0.18 * j / len(trail))
        X, Y = S(bx, byy)
        if t < 0.35:          # the ball fades in at the top before it drops
            a = t / 0.35
        else:
            a = 1.0
        capsule(buf, X, Y, X, Y, R * s, np.array([255, 244, 226] if autumn else [255, 255, 255], np.float32), glow=0.5 * a, glow_r=80 * s, alpha=a)
        # grade + grain + loop fade
        f = 1.0
        if t > c["end"] - 0.45:
            f = max(0.0, (c["end"] - t) / 0.45)
        frame = np.clip((buf + grain[fi % 4]) * f, 0, 255).astype(np.uint8)
        proc.stdin.write(frame.tobytes())
        if fi % 120 == 0:
            print(f"frame {fi}/{n}")
    proc.stdin.close(); proc.wait()
    return c


# ------------------------------------------------------------------ audio
def mallet(freq, dur, vel=1.0, bright=1.0):
    t = np.arange(int(dur * SR)) / SR
    env = np.minimum(1, t / 0.003)
    x = (np.sin(2 * np.pi * freq * t) * np.exp(-t * 2.6)
         + 0.35 * bright * np.sin(2 * np.pi * freq * 3.93 * t) * np.exp(-t * 9)
         + 0.12 * bright * np.sin(2 * np.pi * freq * 9.2 * t) * np.exp(-t * 22))
    click = np.random.default_rng(int(freq)).normal(0, 1, len(t)) * np.exp(-t * 400) * 0.06
    return (x * env + click) * vel


def midi(m):
    return 440 * 2 ** ((m - 69) / 12)


def audio(c, path):
    n = int((c["end"] + 0.5) * SR)
    mix = np.zeros(n, np.float32)
    for nb, t in zip(c["notes"][:-1], c["times"][:-1]):
        m = nb[0]
        s = mallet(midi(m), 2.2, 0.55)
        o = int(t * SR); mix[o:o + len(s)] += s[:n - o]
    # the final ping: tonic chord, a low root and a bell on top
    ft = c["final"]["t"]; fm = c["final"]["m"]
    for mm, v, br in ((fm, 0.55, 1), (fm + 4, 0.35, 1), (fm + 7, 0.35, 1), (fm + 12, 0.4, 1.4), (fm - 12, 0.5, 0.4)):
        s = mallet(midi(mm), 3.5, v, br)
        o = int(ft * SR); mix[o:o + len(s)] += s[:n - o]
    tb = np.arange(int(3.5 * SR)) / SR
    bell = 0.22 * np.sin(2 * np.pi * midi(fm + 24) * tb) * np.exp(-tb * 1.2)
    o = int(ft * SR); mix[o:o + len(bell)] += bell[:n - o]
    # small room: decaying noise impulse response
    ir_t = np.arange(int(1.6 * SR)) / SR
    ir = np.random.default_rng(5).normal(0, 1, len(ir_t)) * np.exp(-ir_t * 3.2)
    ir = np.convolve(ir, np.ones(12) / 12, mode="same")
    ir /= np.abs(ir).sum() / 6
    wet = np.fft.irfft(np.fft.rfft(mix, 2 * n) * np.fft.rfft(ir, 2 * n))[:n]
    out = mix + 0.22 * wet
    fade = np.clip((c["end"] - np.arange(n) / SR) / 0.45, 0, 1)
    out = out * fade
    out = out / (np.abs(out).max() + 1e-9) * 0.9
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR)
        wf.writeframes((out * 32767).astype("<i2").tobytes())


if __name__ == "__main__":
    spec = json.load(open(sys.argv[1]))
    out = sys.argv[2]
    import os
    tmp = os.path.dirname(os.path.abspath(out))
    v, a = os.path.join(tmp, "_fp_video.mp4"), os.path.join(tmp, "_fp_audio.wav")
    c = render(spec, v)
    audio(c, a)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", v, "-i", a, "-af", "loudnorm=I=-14:TP=-1.5", "-c:v", "copy", "-c:a", "aac",
                    "-b:a", "192k", "-shortest", "-movflags", "+faststart", out], check=True)
    os.remove(v); os.remove(a)
    print("done", out, round(c["end"], 2), "s")
