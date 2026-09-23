"""Robotic narration. Piper (free, offline neural TTS) -> radio filter.
Falls back to espeak-ng, then to a test tone so the pipeline never hard-fails."""
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

from . import config

SR = 44100
# Field-radio chain tuned for a female command voice: slightly lower and slower,
# warm low-mids, clear presence, tight compression, a touch of room. No tinny band-pass.
RADIO_FX = ("asetrate=44100*0.96,aresample=44100,atempo=0.97,"
            "highpass=f=110,lowpass=f=7000,"
            "equalizer=f=220:t=q:w=1:g=3,equalizer=f=2800:t=q:w=1.4:g=2,"
            "equalizer=f=6000:t=q:w=1:g=-3,"
            "acompressor=threshold=0.08:ratio=5:attack=5:release=90,"
            "aecho=0.8:0.35:14:0.10,volume=1.4")

_piper = None


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


def _raw_tts(text: str, path: Path) -> str:
    v = _load_piper()
    if v:
        with wave.open(str(path), "wb") as wf:
            if hasattr(v, "synthesize_wav"):
                v.synthesize_wav(text, wf)
            else:
                v.synthesize(text, wf)
        return "piper"
    if shutil.which("espeak-ng"):
        subprocess.run(["espeak-ng", "-v", "en-gb", "-s", "150", "-p", "30",
                        "-w", str(path), text], check=True)
        return "espeak-ng"
    # last resort: a data-burst tone the length of the sentence (layout tests only)
    dur = max(1.2, len(text) * 0.06)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR)
        frames = bytearray()
        for i in range(int(dur * SR)):
            t = i / SR
            env = 0.5 + 0.5 * math.sin(2 * math.pi * 7 * t)
            s = 0.18 * env * math.sin(2 * math.pi * (180 + 40 * math.sin(2 * math.pi * 3 * t)) * t)
            frames += struct.pack("<h", int(s * 32767))
        wf.writeframes(bytes(frames))
    return "test-tone"


def duration(path: Path) -> float:
    with wave.open(str(path)) as wf:
        return wf.getnframes() / wf.getframerate()


def speak(text: str, out: Path) -> float:
    raw = out.with_suffix(".raw.wav")
    engine = _raw_tts(text, raw)
    fx = RADIO_FX if engine != "test-tone" else "anull"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af", ("aresample=44100," + fx) if fx != "anull" else fx,
                    "-ar", str(SR), "-ac", "1", "-sample_fmt", "s16", str(out)], check=True)
    raw.unlink(missing_ok=True)
    return duration(out)


def engine_name() -> str:
    if _load_piper():
        return "piper"
    return "espeak-ng" if shutil.which("espeak-ng") else "test-tone"


def build_track(segments: list[tuple[float, Path | None]], out: Path, beeps: list[float]):
    """segments: (start_time, wav) placed on a timeline. Adds hum bed + line beeps."""
    import numpy as np
    total = max(s + (duration(p) if p else 0) for s, p in segments) + 0.1
    buf = np.zeros(int(total * SR), dtype=np.float32)
    for start, p in segments:
        if not p:
            continue
        with wave.open(str(p)) as wf:
            v = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float32) / 32768
        o = int(start * SR)
        n = min(len(v), len(buf) - o)
        buf[o:o + n] += v[:n]
    k = np.arange(int(0.07 * SR))
    chirp = 0.12 * (1 - k / len(k)) * np.sin(2 * np.pi * 1320 * k / SR)
    for b in beeps:  # short terminal chirp at each new line
        o = int(b * SR)
        n = min(len(chirp), len(buf) - o)
        if n > 0:
            buf[o:o + n] += chirp[:n]
    dry = out.with_suffix(".dry.wav")
    with wave.open(str(dry), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR)
        wf.writeframes((np.clip(buf, -1, 1) * 32767).astype("<i2").tobytes())

    # low war-room hum + static bed under the voice
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(dry),
        "-f", "lavfi", "-i", f"sine=f=55:d={total:.2f}:sample_rate={SR}",
        "-f", "lavfi", "-i", f"anoisesrc=d={total:.2f}:c=brown:a=0.5:r={SR}",
        "-filter_complex",
        "[1]volume=0.05[h];[2]lowpass=f=900,volume=0.05[n];"
        "[0][h][n]amix=inputs=3:normalize=0,alimiter=limit=0.95[a]",
        "-map", "[a]", "-ar", str(SR), "-ac", "2", str(out)], check=True)
    dry.unlink(missing_ok=True)
    return total
