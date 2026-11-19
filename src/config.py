import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

_token_from_env = os.environ.get("BOT_TOKEN")
load_dotenv(BASE_DIR / ".env")
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise SystemExit("ERROR: BOT_TOKEN not found. Set BOT_TOKEN in .env or as an environment variable.")

if not _token_from_env:
    logger.warning("BOT_TOKEN loaded from .env file. For production, set it via systemd EnvironmentFile or a secure env manager.")
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", 8080))
WEB_URL = os.getenv("WEB_URL", f"https://localhost:{WEB_PORT}")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
if not BOT_USERNAME:
    logger.warning("BOT_USERNAME not set. Mini App deep links will be broken. Set it in .env or environment.")

# Max age (seconds) of a Telegram Mini App initData auth_date before the API rejects it.
INITDATA_MAX_AGE = int(os.getenv("INITDATA_MAX_AGE_HOURS", "24")) * 3600

DB_PATH = BASE_DIR / "videos.db"
WEB_DIR = BASE_DIR / "web"

# On-disk byte cache for the /api/video proxy: a clip is pulled from Telegram once, then every
# repeat (any user, any reload, any Range) is served from here instead of re-streaming the CDN.
# Bounded by an LRU eviction; size is env-tunable.
VIDEO_CACHE_DIR = BASE_DIR / "video_cache"
VIDEO_CACHE_MAX_BYTES = int(os.getenv("VIDEO_CACHE_MAX_MB", "512")) * 1024 * 1024


def _parse_admin_ids() -> frozenset[int]:
    raw = os.getenv("ADMIN_USER_IDS") or os.getenv("ADMIN_USER_ID") or ""
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return frozenset(ids)


# Telegram user IDs allowed to invoke /admin_stats in DM. Empty by default (fail closed).
ADMIN_USER_IDS: frozenset[int] = _parse_admin_ids()

# Trip Recap Reels + Telegram Stars pricing (amounts are whole Telegram Stars, currency="XTR").
REEL_PRICE_STARS = max(1, int(os.getenv("REEL_PRICE_STARS", "100")))
GROUP_PASS_STARS = max(1, int(os.getenv("GROUP_PASS_STARS", "250")))
GROUP_PASS_DAYS = max(1, int(os.getenv("GROUP_PASS_DAYS", "30")))
REEL_CACHE_DIR = BASE_DIR / "reel_cache"
REEL_CACHE_MAX_BYTES = int(os.getenv("REEL_CACHE_MAX_MB", "512")) * 1024 * 1024
REEL_MAX_CLIPS = max(1, int(os.getenv("REEL_MAX_CLIPS", "20")))
REEL_CLIP_MAX_SEC = max(2, int(os.getenv("REEL_CLIP_MAX_SEC", "6")))
