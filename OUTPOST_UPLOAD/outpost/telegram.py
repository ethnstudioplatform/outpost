"""Sends the finished video to your phone via a Telegram bot for review."""
from pathlib import Path

import requests

from . import config


def send_video(video: Path, caption: str) -> bool:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        print("[telegram] no bot token / chat id set, skipping preview")
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendVideo"
    with open(video, "rb") as fh:
        r = requests.post(url, data={"chat_id": config.TELEGRAM_CHAT_ID,
                                     "caption": caption[:1000], "supports_streaming": True},
                          files={"video": fh}, timeout=180)
    print(f"[telegram] status {r.status_code}")
    return r.ok


def send_text(text: str) -> bool:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return False
    r = requests.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                      data={"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4000],
                            "disable_web_page_preview": True}, timeout=30)
    return r.ok
