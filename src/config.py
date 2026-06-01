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

DB_PATH = BASE_DIR / "videos.db"
WEB_DIR = BASE_DIR / "web"
