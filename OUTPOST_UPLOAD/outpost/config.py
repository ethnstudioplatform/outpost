"""OUTPOST settings. Everything tunable lives here or in environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "out"
STATE_FILE = ROOT / "state" / "seen.json"
FONT_DIR = ROOT / "assets" / "fonts"
VOICE_DIR = ROOT / "assets" / "voices"

HANDLE = "@outpost.feed"
BRAND = "OUTPOST"
TAGLINE = "LIVE CONFLICT MONITOR"

# ---- video ----
W, H = 1080, 1920
FPS = 30

# phosphor palette
BG = (6, 12, 7)
GREEN = (51, 255, 102)
GREEN_MID = (34, 170, 68)
GREEN_DIM = (16, 80, 34)
GREEN_FAINT = (10, 38, 18)
AMBER = (255, 176, 0)  # only used for the LIVE / ALERT tag

# ---- data ----
# Only headlines from these outlets are used. Keep it to outlets with real
# newsrooms and corrections policies.
TRUSTED_DOMAINS = {
    "reuters.com", "apnews.com", "bbc.co.uk", "bbc.com", "aljazeera.com",
    "theguardian.com", "france24.com", "dw.com", "npr.org", "cnn.com",
    "nytimes.com", "washingtonpost.com", "ft.com", "economist.com",
    "kyivindependent.com", "timesofisrael.com", "haaretz.com",
    "thehindu.com", "abc.net.au", "cbc.ca", "euronews.com", "politico.eu",
    "bloomberg.com", "news.sky.com", "independent.co.uk", "japantimes.co.jp",
    "scmp.com", "reliefweb.int", "un.org", "news.un.org",
}

GDELT_QUERY = (
    '(airstrike OR ceasefire OR "drone strike" OR shelling OR offensive '
    'OR "armed forces" OR militants OR frontline OR missile) sourcelang:english'
)
GDELT_TIMESPAN = os.getenv("OUTPOST_TIMESPAN", "12h")

# ---- script writer ----
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("OUTPOST_MODEL", "claude-sonnet-4-6")
MAX_LINES = 9          # narrated lines per video
MAX_LINE_CHARS = 90   # keeps each line readable on screen

# ---- voice ----
PIPER_VOICE = os.getenv("OUTPOST_VOICE", "en_GB-southern_english_female-low")

# ---- telegram preview ----
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

RELIEFWEB_APPNAME = os.getenv("RELIEFWEB_APPNAME", "")
