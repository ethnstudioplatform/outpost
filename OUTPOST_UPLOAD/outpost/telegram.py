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


def send_text(text: str) -> bool:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return False
    r = requests.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                      data={"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4000],
                            "disable_web_page_preview": True}, timeout=30)
    return r.ok
