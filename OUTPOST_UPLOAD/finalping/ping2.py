"""thefinalping MULTI: one ball that splits into 2, 4, then 8 mirrored balls while the tune
accelerates, then all of them converge into a single cup on the final chord.

Timing is exact by construction, like ping.py: note times first, then the course.
    python ping.py spec.json out.mp4      (ping.py hands specs with "engine": "multi" to this file)
"""
import math
import subprocess
import wave

import numpy as np

from ping import W, H, FPS, SR, hue, capsule, ring, mallet, midi

R0 = 26


def expand(spec):
    """Turn the phrase + stage list into a flat list of notes with a lane count each."""
    notes, lanes = [], []
    for st in spec["stages"]:
        for _ in range(st.get("repeat", 1)):
            for nb in spec["phrase"]:
                notes.append(list(nb))
                lanes.append(st["balls"])
    return notes, lanes


def build(spec):
    G = float(spec.get("gravity", 8200))
    notes, lanes = expand(spec)
    total_beats = sum(n[1] for n in notes)
    b0, b1 = spec["bpm"], spec["bpm_end"]
    # exponential accelerando over the whole piece
    times, t, acc = [], spec.get("preroll", 0.7), 0.0
    for n in notes:
        times.append(t)
        bpm = b0 * (b1 / b0) ** (acc / total_beats)
        t += n[1] * 60 / bpm
        acc += n[1]
    t_final = t + spec.get("final_gap", 0.55)
    # course in full-width "design" space
    pts = []
    y = 0.0
    for i, n in enumerate(notes):
        opt = n[2] if len(n) > 2 else {}
        if i == 0:
            pts.append((spec.get("start_x", 320.0), 0.0))
            continue
        y_new = y + opt.get("dy", 175)
        best, bx = -1e9, None
        for cx in range(190, 891, 15):
            if not (150 <= abs(cx - pts[-1][0]) <= 520):
                continue
            recent = pts[-14:-1]
            dmin = min([math.hypot(cx - px, y_new - py) for px, py in recent] + [999])
            score = min(dmin, 240) - abs(cx - 540) * 0.04 + (7 if (cx > pts[-1][0]) == (i % 2 == 0) else 0)
            if score > best:
                best, bx = score, cx
        pts.append((bx, y_new))
        y = y_new
    final_y = max(p[1] for p in pts) + spec.get("final_drop", 520)
    t0 = times[0]
    return {"G": G, "notes": notes, "lanes": lanes, "times": times, "pts": pts, "t0": t0,
            "t_final": t_final, "final_y": final_y, "nmax": max(lanes), "end": t_final + spec.get("outro", 3.4)}


def lane_x(xd, lane, n):
    """Map a design-space x (full width) into lane `lane` of `n`, mirroring every other lane."""
    if n == 1:
        return xd
    lw = W / n
    u = (xd - 150) / 780.0
    if lane % 2 == 1:
        u = 1 - u
    return lane * lw + lw * (0.14 + 0.72 * u)


def ball_positions(c, k):
    """Contact points for ball k (0..nmax-1): its lane changes as the balls split."""
    out = []
    for (xd, yd), n in zip(c["pts"], c["lanes"]):
        lane = k * n // c["nmax"]
        out.append((lane_x(xd, lane, n), yd))
    out.append((W / 2, c["final_y"]))      # everyone meets in the cup
    return out


def pos_at(c, P, t):
    """Ballistic position along contact points P at time t."""
    G, times = c["G"], c["times"] + [c["t_final"]]
    t0 = times[0]
    if t < t0:
        return P[0][0], P[0][1] - 0.5 * G * (t0 - t) ** 2
    for i in range(len(times) - 1):
        if t < times[i + 1]:
            dt = times[i + 1] - times[i]
            (x0, y0), (x1, y1) = P[i], P[i + 1]
            vy = (y1 - y0 - 0.5 * G * dt * dt) / dt
            s = t - times[i]
            return x0 + (x1 - x0) * s / dt, y0 + vy * s + 0.5 * G * s * s
    return P[-1]


def velocity_in_out(c, P, i):
    G, times = c["G"], c["times"] + [c["t_final"]]
    def v(j):   # launch velocity of arc j
        dt = times[j + 1] - times[j]
        return (P[j + 1][0] - P[j][0]) / dt, (P[j + 1][1] - P[j][1] - 0.5 * G * dt * dt) / dt
    vout = v(i)
    if i == 0:
        vin = (0.0, G * times[0])
    else:
        vx, vy = v(i - 1)
        vin = (vx, vy + G * (times[i] - times[i - 1]))
    nx, ny = vout[0] - vin[0], vout[1] - vin[1]
    L = math.hypot(nx, ny) or 1
    return nx / L, ny / L


def colour(m, stage):
    base = ((m * 5) % 12) / 12
    return hue(base * 0.85 + 0.02 + stage * 0.04, s=0.55 + 0.1 * stage)


def render(spec, out):
    c = build(spec)
    nmax = c["nmax"]
    paths = [ball_positions(c, k) for k in range(nmax)]
    # pads: one per (note, lane)
    pads = []
    for i, (m, n) in enumerate(zip([nb[0] for nb in c["notes"]], c["lanes"])):
        for lane in range(n):
            k = lane * nmax // n
            P = paths[k]
            nx, ny = velocity_in_out(c, P, i)
            pads.append({"x": P[i][0], "y": P[i][1], "nx": nx, "ny": ny, "m": m, "t": c["times"][i],
                         "n": n, "stage": int(round(math.log2(n)))})
    stage_at = lambda t: max([p["stage"] for p in pads if p["t"] <= t] or [0])
    n_frames = int(c["end"] * FPS)
    ts = np.arange(n_frames) / FPS
    by = np.array([pos_at(c, paths[0], t)[1] for t in ts])
    k_ = np.exp(-0.5 * (np.arange(-30, 31) / 12) ** 2); k_ /= k_.sum()
    cam = np.convolve(np.pad(by, 30, mode="edge"), k_, mode="same")[30:-30]
    ft = c["t_final"]
    rng = np.random.default_rng(4)
    grain = [rng.normal(0, 2.0, (H, W, 1)).astype(np.float32) for _ in range(4)]
    stars = [(rng.uniform(0, W), rng.uniform(-600, c["final_y"] + 2000), rng.uniform(1, 2.4)) for _ in range(260)]
    yy = np.linspace(0, 1, H)[:, None]
    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium",
                             "-crf", "19", "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
    trails = [[] for _ in range(nmax)]
    top, bot = -400, c["final_y"] + 300
    fit = (H * 0.9) / (bot - top)
    last_hit = [p["t"] for p in pads]
    for fi, t in enumerate(ts):
        stg = stage_at(t)
        # intensity grows with tempo: background warms, light pulses on each beat
        pulse = 0.0
        recent = [p["t"] for p in pads if p["t"] <= t]
        if recent:
            pulse = math.exp(-(t - max(recent)) * 9)
        top_c = np.array([10 + 6 * stg, 12, 24 + 4 * stg], np.float32) * (1 + 0.25 * pulse * stg / 3)
        grad = top_c[None, :] * (1 - yy) + np.array([4, 5, 10], np.float32)[None, :] * yy
        buf = np.repeat(grad[:, None, :], W, axis=1).astype(np.float32)
        z = 0.0 if t < ft + 0.5 else min(1.0, (t - ft - 0.5) / 1.4)
        z = z * z * (3 - 2 * z)
        s = 1 + (fit - 1) * z
        cy = cam[fi] * (1 - z) + ((top + bot) / 2) * z
        shake = (stg >= 3) * pulse * 5 * (1 - z)
        ox = math.sin(t * 91) * shake
        def S(x, y):
            return (W / 2 + (x - W / 2) * s + ox, H * 0.45 + (y - cy) * s)
        for sx, sy, sr in stars:
            X, Y = sx, H * 0.45 + (sy - cy * 0.4) * s
            if -5 < Y < H + 5:
                capsule(buf, X, Y, X, Y, sr * max(s, 0.5), np.array([70, 80, 110], np.float32), alpha=0.45)
        # pads (only those near the view)
        ytop, ybot = cy - (H * 0.45 + 200) / s, cy + (H * 0.55 + 200) / s
        for p in pads:
            if not (ytop < p["y"] < ybot):
                continue
            nfac = 1 / math.sqrt(p["n"])
            px, py = p["x"] - p["nx"] * (R0 * nfac + 12), p["y"] - p["ny"] * (R0 * nfac + 12)
            tx, ty = -p["ny"], p["nx"]
            hl = (96 - (p["m"] - 55) * 2.5) * (1.0 if p["n"] == 1 else 0.9 / math.sqrt(p["n"]))
            col = colour(p["m"], p["stage"])
            if t >= p["t"]:
                e = t - p["t"]
                lit = 0.3 + 0.7 * math.exp(-e * 5)
                sq = math.exp(-e * 18) * math.sin(e * 60) * 6 * nfac
            else:
                lit, sq, e = 0.0, 0.0, None
            if ft < t:      # final sweep: every pad re-lights in order
                d_ = t - ft - 0.6 * (p["t"] / ft)
                if 0 < d_ < 0.8:
                    lit = max(lit, math.exp(-d_ * 4))
            ox_, oy_ = px - p["nx"] * sq, py - p["ny"] * sq
            a1, a2 = S(ox_ + tx * hl, oy_ + ty * hl), S(ox_ - tx * hl, oy_ - ty * hl)
            capsule(buf, *a1, *a2, max(4, 14 * nfac) * s, col * (0.25 + 0.75 * lit), glow=0.5 * lit,
                    glow_r=80 * s * nfac)
            if e is not None and e < 0.45 and z < 0.5:
                cx_, cy_ = S(p["x"], p["y"])
                ring(buf, cx_, cy_, (26 + e * 240) * s * nfac, 3 * s, col, (1 - e / 0.45) * 0.7)
        # the cup
        fx, fy = W / 2, c["final_y"]
        landed = t >= ft
        lit = 0.3 if not landed else 0.4 + 0.6 * math.exp(-(t - ft) * 1.3)
        cupcol = hue(0.13, s=0.5)
        for a, b in (((-150, -50), (-106, 56)), ((-106, 56), (106, 56)), ((106, 56), (150, -50))):
            capsule(buf, *S(fx + a[0], fy + a[1] + 6), *S(fx + b[0], fy + b[1] + 6), 15 * s,
                    cupcol * (0.35 + 0.65 * lit), glow=0.8 * lit, glow_r=140 * s)
        if landed:
            e = t - ft
            for kk, sp in enumerate((500, 850, 1300)):
                if e < 1.8:
                    ring(buf, *S(fx, fy), (50 + e * sp) * s, (8 - kk * 2) * s, cupcol, max(0, 1 - e / 1.8))
            rs = np.random.default_rng(11)
            for q in range(60):
                ang = rs.uniform(-math.pi, 0); v = rs.uniform(600, 1500)
                sx, sy = fx + math.cos(ang) * v * e, fy + math.sin(ang) * v * e + 0.5 * c["G"] * 0.35 * e * e
                if e < 1.5:
                    X, Y = S(sx, sy)
                    capsule(buf, X, Y, X, Y, 6 * s, hue(q / 60), glow=0.4, glow_r=30 * s, alpha=max(0, 1 - e / 1.5))
            if e < 0.3:        # white flash on the landing
                buf += (np.array([255, 250, 240], np.float32) - buf) * (1 - e / 0.3) ** 2 * 0.6
        # balls: draw distinct positions only (split balls overlap until they diverge)
        drawn = []
        for k in range(nmax):
            bx, byy = pos_at(c, paths[k], t)
            trails[k].append((bx, byy))
            trails[k] = trails[k][-12:]
            if any(abs(bx - dx) < 1 and abs(byy - dy) < 1 for dx, dy in drawn):
                continue
            drawn.append((bx, byy))
        nb = len(drawn)
        rad = R0 / math.sqrt(max(1, min(nb, 8))) * (1.25 if nb > 1 else 1)
        for k in range(nmax):
            tr = trails[k]
            for j, (tx_, ty_) in enumerate(tr[:-1]):
                X, Y = S(tx_, ty_)
                capsule(buf, X, Y, X, Y, rad * s * (0.3 + 0.6 * j / len(tr)), np.array([200, 215, 255], np.float32),
                        alpha=0.14 * j / len(tr))
        a = min(1.0, t / 0.35)
        for bx, byy in drawn:
            X, Y = S(bx, byy)
            capsule(buf, X, Y, X, Y, rad * s, np.array([255, 255, 255], np.float32), glow=0.5 * a,
                    glow_r=70 * s, alpha=a)
        f = 1.0 if t < c["end"] - 0.45 else max(0.0, (c["end"] - t) / 0.45)
        frame = np.clip((buf + grain[fi % 4]) * f, 0, 255).astype(np.uint8)
        proc.stdin.write(frame.tobytes())
        if fi % 300 == 0:
            print(f"frame {fi}/{n_frames}", flush=True)
    proc.stdin.close(); proc.wait()
    return c


def audio(c, spec, path):
    n = int((c["end"] + 0.5) * SR)
    mix = np.zeros(n, np.float32)
    def put(sig, t):
        o = int(t * SR)
        if o < n:
            mix[o:o + len(sig)] += sig[:n - o]
    for nb, t, lanes in zip(c["notes"], c["times"], c["lanes"]):
        m = nb[0]
        put(mallet(midi(m), 1.6, 0.5), t)
        if lanes >= 2:
            put(mallet(midi(m - 12), 1.4, 0.32, 0.6), t)       # octave below
        if lanes >= 4:
            put(mallet(midi(m + 12), 1.0, 0.22, 1.2), t)       # octave above
        if lanes >= 8:
            k = np.arange(int(0.25 * SR)) / SR                # low thump on every note
            put(0.5 * np.sin(2 * np.pi * (70 - 30 * k) * k) * np.exp(-k * 18), t)
    ft = c["t_final"]
    fm = spec.get("final_note", 47)
    for iv, dl, v in ((-12, 0, 0.7), (0, 0, 0.6), (3, 0.02, 0.4), (7, 0.04, 0.4), (12, 0.06, 0.45),
                      (15, 0.08, 0.3), (19, 0.1, 0.3), (24, 0.12, 0.35)):
        put(mallet(midi(fm + iv), 3.8, v, 1.1), ft + dl)
    k = np.arange(int(1.2 * SR)) / SR
    put(0.9 * np.sin(2 * np.pi * (55 - 20 * k) * k) * np.exp(-k * 4), ft)   # sub hit
    ir_t = np.arange(int(1.8 * SR)) / SR
    ir = np.random.default_rng(5).normal(0, 1, len(ir_t)) * np.exp(-ir_t * 3.0)
    ir = np.convolve(ir, np.ones(12) / 12, mode="same"); ir /= np.abs(ir).sum() / 6
    wet = np.fft.irfft(np.fft.rfft(mix, 2 * n) * np.fft.rfft(ir, 2 * n))[:n]
    out = mix + 0.2 * wet
    out *= np.clip((c["end"] - np.arange(n) / SR) / 0.45, 0, 1)
    out = out / (np.abs(out).max() + 1e-9) * 0.9
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR)
        wf.writeframes((out * 32767).astype("<i2").tobytes())


def main(spec, out):
    import os
    tmp = os.path.dirname(os.path.abspath(out))
    v, a = os.path.join(tmp, "_fp_video.mp4"), os.path.join(tmp, "_fp_audio.wav")
    c = render(spec, v)
    audio(c, spec, a)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", v, "-i", a, "-af", "loudnorm=I=-14:TP=-1.5",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out], check=True)
    os.remove(v); os.remove(a)
    print("done", out, round(c["end"], 2), "s")
