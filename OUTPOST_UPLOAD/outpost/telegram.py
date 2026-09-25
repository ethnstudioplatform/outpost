"""Sends the finished video to your phone via a Telegram bot for review."""
from pathlib import Path

import requests

from . import config


def send_video(video: Path, caption: str) -> bool:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        print("[telegram] no bot token / chat id set, skipping preview")
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendVideo"
    # Telegram shows big videos as a square tile unless we pass the real size + a thumbnail.
    import subprocess
    from PIL import Image
    try:
        dur = int(float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                        "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout.strip()))
    except Exception:
        dur = 0
    thumb = video.with_name("tg_thumb.jpg")
    src = video.with_name("thumb.png")
    if src.exists():
        Image.open(src).convert("RGB").resize((180, 320)).save(thumb, "JPEG", quality=85)
    video = _fit_for_telegram(video, dur)
    files = {"video": open(video, "rb")}
    if thumb.exists():
        files["thumbnail"] = open(thumb, "rb")
    data = {"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption[:1000], "supports_streaming": True,
            "width": 1080, "height": 1920, "duration": dur}
    if thumb.exists():
        data["thumbnail"] = "attach://thumbnail"
    r = requests.post(url, data=data, files=files, timeout=300)
    for f in files.values():
        f.close()
    print(f"[telegram] status {r.status_code}")
    return r.ok


TG_LIMIT = 48 * 1024 * 1024   # Telegram bots can upload up to 50 MB


def _fit_for_telegram(video: Path, dur: int) -> Path:
    """Big renders (grainy map room, long videos) can pass 50 MB and Telegram answers 413.
    Make a smaller copy just for Telegram; the full-quality file stays in the draft artifact."""
    import subprocess
    if video.stat().st_size <= TG_LIMIT:
        return video
    secs = max(dur, 1)
    kbps = int((44 * 8 * 1024) / secs) - 160          # aim for about 44 MB, leave room for audio
    small = video.with_name("outpost_telegram.mp4")
    for attempt in range(3):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-c:v", "libx264", "-preset", "slow",
                        "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.3)}k", "-bufsize", f"{kbps * 2}k",
                        "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                        "-movflags", "+faststart", str(small)], check=False)
        if small.exists() and small.stat().st_size <= TG_LIMIT:
            print(f"[telegram] {video.stat().st_size / 1e6:.1f} MB is over the limit, sending a "
                  f"{small.stat().st_size / 1e6:.1f} MB copy ({kbps} kbps)")
            return small
        kbps = int(kbps * 0.8)
    print("[telegram] could not shrink the video under 50 MB, sending the original")
    return video


def send_text(text: str) -> bool:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return False
    r = requests.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                      data={"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4000],
                            "disable_web_page_preview": True}, timeout=30)
    return r.ok
