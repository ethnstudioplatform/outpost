"""OUTPOST narration.

Primary engine: Kokoro (open-source, Apache 2.0) via kokoro-onnx, with a custom
blend of British female voices: the "OUTPOST" voice. Calm, close, documentary.
Fallbacks: Piper, then espeak-ng, then a test tone, so a run never hard-fails.
"""
import math
import os
import shutil
import struct
import subprocess
import wave
from pathlib import Path

from . import config

SR = 44100

# ---- the OUTPOST voice: a blend of Kokoro's British female voices ----
# Emma carries the warmth and clarity, Isabella adds a lower, steadier edge.
VOICE_BLEND = [("bf_emma", 0.68), ("bf_isabella", 0.32)]
VOICE_SPEED = float(os.getenv("OUTPOST_SPEED", "0.94"))
MUSIC_LEVEL = 0.0   # built-in music bed off: Ethan adds a TikTok sound when he posts. Set 0.2 (20%) or use the script key "music_level" to turn it on  # a touch slower than default: measured, human

# Light, natural chain. No radio effect: sounds like a person in a quiet room.
VOICE_FX = ("highpass=f=65,"
            "equalizer=f=180:t=q:w=1.1:g=2.2,"      # warmth
            "equalizer=f=3200:t=q:w=1.6:g=1.2,"     # presence
            "equalizer=f=7400:t=q:w=2:g=-3,"        # soften sibilance
            "acompressor=threshold=0.12:ratio=2.6:attack=12:release=160:makeup=1.3,"
            "aecho=0.85:0.22:23|37:0.07|0.045")     # small room, barely there

_kokoro = None
_style = None
_piper = None
ENGINE = None


def _load_kokoro():
    global _kokoro, _style
    if _kokoro is not None:
        return _kokoro
    model = config.ROOT / "assets" / "kokoro" / "kokoro-v1.0.onnx"
    voices = config.ROOT / "assets" / "kokoro" / "voices-v1.0.bin"
    if not (model.exists() and voices.exists()):
        print("[voice] kokoro model files missing")
        _kokoro = False
        return _kokoro
    try:
        import numpy as np
        from kokoro_onnx import Kokoro
        k = Kokoro(str(model), str(voices))
        parts = []
        for name, w in VOICE_BLEND:
            try:
                st = k.get_voice_style(name)
            except Exception:
                st = k.voices[name]
            parts.append(np.asarray(st, dtype=np.float32) * w)
        _style = sum(parts)
        _kokoro = k
    except Exception as e:
        print(f"[voice] kokoro unavailable: {e}")
        _kokoro = False
    return _kokoro


def _load_piper():
    global _piper
    if _piper is not None:
        return _piper
    model = config.VOICE_DIR / f"{config.PIPER_VOICE}.onnx"
    if not model.exists():
        _piper = False
        return _piper
    try:
        from piper import PiperVoice
        _piper = PiperVoice.load(str(model))
    except Exception as e:
        print(f"[voice] piper unavailable: {e}")
        _piper = False
    return _piper


_ORD = ["zeroth", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
        "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth",
        "seventeenth", "eighteenth", "nineteenth", "twentieth", "twenty-first", "twenty-second",
        "twenty-third", "twenty-fourth", "twenty-fifth", "twenty-sixth", "twenty-seventh",
        "twenty-eighth", "twenty-ninth", "thirtieth", "thirty-first"]
_MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December|"
           "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec")
_FULL = {m[:3].lower(): m for m in ["January", "February", "March", "April", "May", "June", "July",
                                     "August", "September", "October", "November", "December"]}


def _dates(t: str) -> str:
    """'3 November' -> 'the third of November', 'November 3' -> 'November the third' (British read-out)."""
    import re

    def month(m):
        return _FULL[m[:3].lower()]

    def dm(mt):
        d = int(mt.group(1))
        return f"the {_ORD[d]} of {month(mt.group(2))}" if 1 <= d <= 31 else mt.group(0)

    def md(mt):
        d = int(mt.group(2))
        return f"{month(mt.group(1))} the {_ORD[d]}" if 1 <= d <= 31 else mt.group(0)

    t = re.sub(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTHS})\b\.?", dm, t, flags=re.I)
    t = re.sub(rf"\b({_MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\d)", md, t, flags=re.I)
    return t


def _spoken(text: str) -> str:
    """Small fixes so numbers, dates and symbols read naturally."""
    t = _dates(text)
    t = t.replace("%", " percent").replace("&", " and ")
    t = t.replace(" km", " kilometres").replace("km ", "kilometres ")
    return t


def _raw_tts(text: str, path: Path) -> str:
    k = _load_kokoro()
    if k:
        import numpy as np
        try:
            samples, sr = k.create(_spoken(text), voice=_style, speed=VOICE_SPEED, lang="en-gb")
        except TypeError:
            samples, sr = k.create(_spoken(text), _style, VOICE_SPEED, "en-gb")
        samples = np.asarray(samples, dtype=np.float32)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(int(sr))
            wf.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        return "kokoro"
    v = _load_piper()
    if v:
        with wave.open(str(path), "wb") as wf:
            if hasattr(v, "synthesize_wav"):
                v.synthesize_wav(text, wf)
            else:
                v.synthesize(text, wf)
        return "piper"
    if shutil.which("espeak-ng"):
        subprocess.run(["espeak-ng", "-v", "en-gb", "-s", "150", "-p", "30", "-w", str(path), text], check=True)
        return "espeak-ng"
    dur = max(1.2, len(text) * 0.065)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR)
        frames = bytearray()
        for i in range(int(dur * SR)):
            t = i / SR
            s = 0.12 * (0.5 + 0.5 * math.sin(2 * math.pi * 5 * t)) * math.sin(2 * math.pi * 190 * t)
            frames += struct.pack("<h", int(s * 32767))
        wf.writeframes(bytes(frames))
    return "test-tone"


def duration(path: Path) -> float:
    with wave.open(str(path)) as wf:
        return wf.getnframes() / wf.getframerate()


def speak(text: str, out: Path) -> float:
    global ENGINE
    raw = out.with_suffix(".raw.wav")
    ENGINE = _raw_tts(text, raw)
    fx = "aresample=44100," + (VOICE_FX if ENGINE in ("kokoro", "piper") else "anull")
    # trim leading/trailing silence so our own pauses control the rhythm
    fx = "silenceremove=start_periods=1:start_threshold=-50dB," + fx + \
         ",areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af", fx,
                    "-ar", str(SR), "-ac", "1", "-sample_fmt", "s16", str(out)], check=True)
    raw.unlink(missing_ok=True)
    return duration(out)


def engine_name() -> str:
    if _load_kokoro():
        return "kokoro:" + "+".join(f"{n}{int(w * 100)}" for n, w in VOICE_BLEND)
    if _load_piper():
        return "piper"
    return "espeak-ng" if shutil.which("espeak-ng") else "test-tone"


def music_bed(n_all, total, bpm=92):
    """A tense, understated news-desk loop in A minor (Am, F, C, G): kick, bass, plucked arpeggio,
    soft pad and hats. Generated from scratch every render, so it is fully original."""
    import numpy as np
    t = np.arange(n_all) / SR
    beat = 60.0 / bpm
    out = np.zeros(n_all, np.float32)
    hz = lambda m: 440.0 * 2 ** ((m - 69) / 12)
    chords = [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]   # Am F C G
    bar = 4 * beat

    def note(start, dur, freq, amp, decay, harm=(1.0,)):
        o = int(start * SR); m = int(dur * SR)
        if o >= n_all or m <= 0:
            return
        m = min(m, n_all - o)
        k = np.arange(m) / SR
        w = sum(a * np.sin(2 * np.pi * freq * (i + 1) * k) for i, a in enumerate(harm))
        env = np.exp(-k * decay) * np.clip(k / 0.004, 0, 1)
        out[o:o + m] += (amp * w * env).astype(np.float32)

    n_beats = int(total / beat) + 2
    rng = np.random.default_rng(11)
    for b in range(n_beats):
        st = b * beat
        ch = chords[(b // 8) % 4]                      # 2 bars per chord
        if b % 2 == 0:                                 # kick on 1 and 3
            o = int(st * SR); m = min(int(0.35 * SR), n_all - o)
            if m > 0:
                k = np.arange(m) / SR
                out[o:o + m] += (0.9 * np.sin(2 * np.pi * (48 + 70 * np.exp(-k * 30)) * k) * np.exp(-k * 9)).astype(np.float32)
        for half in (0, 0.5):                          # driving eighth-note bass
            note(st + half * beat, 0.9 * beat / 2, hz(ch[0] - 24), 0.30, 7, (1.0, 0.35, 0.15))
        for q in range(4):                             # plucked sixteenth arpeggio
            tone = ch[(b * 4 + q) % 3] + 12
            note(st + q * beat / 4, beat / 2, hz(tone), 0.07, 11, (1.0, 0.25))
        o = int((st + beat / 2) * SR); m = min(int(0.05 * SR), n_all - o)
        if m > 0:                                      # soft off-beat hat
            out[o:o + m] += (rng.normal(0, 1, m) * 0.05 * np.exp(-np.arange(m) / SR * 80)).astype(np.float32)
    for c in range(int(total / (2 * bar)) + 2):          # sustained pad under each chord
        ch = chords[c % 4]
        for mnote in ch:
            note(c * 2 * bar, 2 * bar, hz(mnote), 0.035, 0.4, (1.0, 0.2))
    return out


def build_track(segments, out: Path, sfx=()):
    """segments: [(start_s, wav)], sfx: [(t, kind)] with kind in tick|pulse|swell.
    Voice on top of a quiet ambient pad. Loudness normalised for phones."""
    import numpy as np
    total = max(s + (duration(p) if p else 0) for s, p in segments) + 0.2
    n_all = int(total * SR)
    voice = np.zeros(n_all, np.float32)
    for start, p in segments:
        if not p:
            continue
        with wave.open(str(p)) as wf:
            v = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float32) / 32768
        o = int(start * SR)
        n = min(len(v), n_all - o)
        if n > 0:
            voice[o:o + n] += v[:n]

    t = np.arange(n_all) / SR
    rng = np.random.default_rng(7)
    # ambient pad: low A and E, slowly breathing, plus a soft air layer
    pad = (0.05 * np.sin(2 * np.pi * 55 * t) + 0.035 * np.sin(2 * np.pi * 82.41 * t + 1.3)
           + 0.018 * np.sin(2 * np.pi * 110.3 * t + 0.4) + 0.010 * np.sin(2 * np.pi * 164.8 * t + 2.1))
    pad *= 0.65 + 0.35 * np.sin(2 * np.pi * t / 9.0)
    air = rng.normal(0, 1, n_all).astype(np.float32)
    k = 200
    air = np.convolve(air, np.ones(k) / k, mode="same") * 0.12
    bed = (pad + air) * np.clip(t / 2.0, 0, 1) * np.clip((total - t) / 1.5, 0, 1)
    if MUSIC_LEVEL > 0:
        # original music bed (generated here, so no copyright issues) sitting at MUSIC_LEVEL of the voice
        mus = music_bed(n_all, total)
        vo = voice[np.abs(voice) > 0.01]
        v_rms = float(np.sqrt(np.mean(vo ** 2))) if len(vo) else 0.1
        m_rms = float(np.sqrt(np.mean(mus ** 2))) or 1.0
        bed = mus * (MUSIC_LEVEL * v_rms / m_rms) * np.clip(t / 0.3, 0, 1) * np.clip((total - t) / 1.5, 0, 1)

    fx = np.zeros(n_all, np.float32)
    for t0, kind in sfx:
        o = int(t0 * SR)
        if kind == "tick":        # soft UI tick when a label lands
            m = int(0.05 * SR); kk = np.arange(m) / SR
            s_ = 0.05 * np.sin(2 * np.pi * 2400 * kk) * np.exp(-kk * 90)
        elif kind == "pulse":     # low, round pulse for a key number
            m = int(0.9 * SR); kk = np.arange(m) / SR
            s_ = 0.22 * np.sin(2 * np.pi * (62 - 12 * kk) * kk) * np.exp(-kk * 4.5)
        elif kind in ("clock", "clock2"):   # map room: a quiet wall clock, tick then tock
            m = int(0.06 * SR); kk = np.arange(m) / SR
            fq = 1900 if kind == "clock" else 1450
            s_ = (0.028 * np.sin(2 * np.pi * fq * kk) + 0.02 * np.sin(2 * np.pi * 380 * kk)) * np.exp(-kk * 140)
        elif kind == "swell":     # air swell on big camera moves
            m = int(1.2 * SR); kk = np.arange(m) / m
            s_ = rng.normal(0, 1, m) * np.sin(np.pi * kk) ** 2 * 0.03
            s_ = np.convolve(s_, np.ones(60) / 60, mode="same")
        else:
            continue
        m = min(len(s_), n_all - o)
        if m > 0 and o >= 0:
            fx[o:o + m] += s_[:m]

    # duck the bed under the voice
    env = np.convolve(np.abs(voice), np.ones(4410) / 4410, mode="same")
    duck = 1 - np.clip(env * 6, 0, 0.25 if MUSIC_LEVEL > 0 else 0.55)
    mix = voice + bed * duck + fx
    dry = out.with_suffix(".dry.wav")
    with wave.open(str(dry), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR)
        wf.writeframes((np.clip(mix, -1, 1) * 32767).astype("<i2").tobytes())
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(dry),
                    "-af", "loudnorm=I=-15:TP=-1.5:LRA=9", "-ar", str(SR), "-ac", "2", str(out)], check=True)
    dry.unlink(missing_ok=True)
    return total
