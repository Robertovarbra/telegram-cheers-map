# AGENTS.md

This file provides guidance to coding agents working in this repository. `CLAUDE.md` is a symlink to it, so Claude Code reads the same content.

## What this is

A Telegram bot ("Cheers Map") that lets group members post video notes + a location, which are then pinned on an interactive Leaflet map served as a Telegram Mini App. Single-process Python asyncio app: the Telegram bot and the aiohttp web server share one event loop.

## Commands

```bash
uv sync                      # install deps (use --frozen on deploy to honor the lockfile)
uv run python src/main.py    # run bot + web server (requires BOT_TOKEN; see Config)
uv run ruff check .          # lint
uv run ruff format .         # format
```

There are **no tests** in this repo.

Ruff is configured in `pyproject.toml`. Two choices to be aware of: `web/` is excluded from linting, and the line length is a wide **160** (so don't hand-wrap long lines to 88). Run `ruff check`/`ruff format` and match the existing style.

## Config / running

`src/config.py` loads env vars from `.env` (via `python-dotenv`) or real env vars, which take priority and are preferred in production. Two carry non-obvious consequences: a missing `BOT_TOKEN` aborts startup with `SystemExit`, and a missing `BOT_USERNAME` *silently* breaks Mini App deep links — `/map` emits broken `t.me/{bot}/map?startapp=...` links rather than erroring. The remaining vars and their defaults live in `.env.example`.

**Import style matters:** modules import each other flat (`from config import ...`, not `from src.config import ...`). This only resolves when `src/` is on `sys.path`, which happens automatically when you run `python src/main.py` (Python adds the script's dir). Any script or test you add must replicate that, e.g. run from inside `src/`.

## Architecture

`src/main.py` is the single entry point. In one `asyncio.run(main())` it:
1. Initializes SQLite (`init_db` + `migrate_db`).
2. Builds the aiohttp `web.Application` and registers `/api/*` routes + `/` (serves `web/index.html`).
3. Builds the `python-telegram-bot` `Application`, registers handlers, starts polling.
4. **Shares the bot with the web layer via `web_app["bot"] = application.bot`** — this is the key coupling. Web handlers reach Telegram (resolving file paths, fetching chat info) through this shared bot instance.

### The five source modules

- **`config.py`** — env loading, logging, paths (`DB_PATH`, `WEB_DIR`).
- **`database.py`** — all SQLite access. Every function opens its own connection in a `try/finally`. WAL mode is enabled. `migrate_db()` does idempotent `ALTER TABLE`/`CREATE TABLE` wrapped in `try/except sqlite3.OperationalError` — **add schema changes here**, there is no migration framework. Rows are read by numeric index (not `sqlite3.Row`), so column order in SELECTs is load-bearing.
- **`telegram_handlers.py`** — bot command/message handlers (the Telegram side).
- **`web_handlers.py`** — aiohttp request handlers (the HTTP/Mini App side).
- **`auth.py`** — Mini App authorization: `verify_init_data` (Telegram `initData` HMAC + freshness), `extract_init_data` (header/query), `is_member` (cached chat-membership check). See the Security section.

### Pin creation is a two-step, stateful flow
A pin requires both a video note and a location, sent as separate messages:
1. `handle_video` (filter `VIDEO_NOTE`, groups only) stashes `pending_video` in `context.user_data`.
2. `handle_location` pops it, calls `reverse_geocode`, then `add_pin`.

So pinning depends on per-user `context.user_data` carrying state between two updates. Bot prompt/confirmation messages are auto-deleted after a few seconds (best-effort, errors swallowed).

### Videos are never stored — only Telegram `file_id`
`add_pin` stores the `file_id`. The frontend `<video>` points at `/api/video?file_id=...`, and `handle_video_proxy` resolves the file via `bot.get_file()` then streams it from Telegram's CDN, forwarding the `Range` header (essential for seeking). This is why the web server needs the shared bot instance.

### Reverse geocoding (Nominatim / OpenStreetMap)
`reverse_geocode` in `telegram_handlers.py` hits the public Nominatim API, guarded by an `asyncio.Lock`, an in-memory LRU-ish cache (max 500 entries), and a `sleep(1)` to respect rate limits. The cache is in-memory only (lost on restart).

### Chat lifecycle / data cleanup
When the bot is removed from a chat (`handle_my_chat_member` → LEFT/BANNED), **all that chat's pins are deleted**. On startup, `cleanup_inactive_chats` checks every known chat and purges pins for any the bot can no longer access.

### Mini App + map (`web/index.html`)
Single static HTML file, vanilla JS + Leaflet from unpkg CDN — **no build step**. The `/map` command opens it as a Telegram Mini App via `t.me/{BOT_USERNAME}/map?startapp=c{chat_id}`. The frontend derives `chat_id` from (in order) `?chat_id=`, `tgWebAppStartParam`, or `Telegram.WebApp.initDataUnsafe.start_param`, parsing the `c{chatId}` format. All dynamic values are escaped via `esc()` (XSS guard). Pins within `GROUP_RADIUS` (10 m) are clustered. Filtering is split: **server-side** (`user_ids`, `date_from`, `date_to`, `q` location) with LIMIT/OFFSET 500 pagination, plus **client-side** ("only in map view" bounds filter, which disables pagination).

## Security

The Mini App API (`/api/*`) is the only externally reachable surface (behind the Cloudflare Tunnel). Two invariants gate every chat-scoped request — both live in `auth.py` and are bundled by `_authorize(request, chat_id)` in `web_handlers.py`:

1. **initData HMAC** — the request must carry a valid Telegram `initData`, verified by `verify_init_data` (HMAC-SHA256 against `BOT_TOKEN`, plus `auth_date` freshness vs `INITDATA_MAX_AGE`). `extract_init_data` reads it from the `Authorization: tma <data>` header, falling back to `?auth=` for `<video>` tags that can't set headers. **Never trust a client-supplied `chat_id`/`user_id` that isn't backed by a valid signature** — the `chat_id` query param is attacker-controlled; only the signed `user.id` is trustworthy.
2. **Membership** — `is_member(bot, chat_id, user_id)` must pass before returning chat data, so a caller can only read chats they belong to (the `chat_id`-scoped equivalent of tenant isolation). Results are cached ~5 min. `handle_video_proxy` does its own variant: it verifies `initData`, then allows the stream only if the user is a member of *some* chat that references the `file_id` (`get_chat_ids_for_file`).

Intentionally public (no auth): `handle_health`, `handle_bot_info`.

Secrets: `BOT_TOKEN` is never committed — `.env` in dev (config warns when loaded this way), systemd `EnvironmentFile` in prod. `.env.example` holds placeholders only.

### Code Review Checklist

| Check | Grep / verify |
|-------|---------------|
| New chat-scoped endpoint authorizes the caller | `async def handle_*` returning pin/chat data without `await _authorize(` (or, for streams, an `is_member` check) |
| `chat_id` is validated before `int()` | `query.get("chat_id")` without a following `.lstrip("-").isdigit()` guard |
| Auth has no insecure fallback | an `except`/`if not ...` branch that returns data instead of 401/403/503 |
| Schema changes go through `migrate_db()` | `ALTER TABLE`/`CREATE TABLE` outside `database.migrate_db` |
| Frontend output stays escaped | dynamic value injected into the DOM in `web/index.html` without `esc()` |

## Deployment

Runs on a Raspberry Pi under systemd (`Type=notify` with `WatchdogSec=30`) behind a Cloudflare Tunnel. `main.py` sends `READY=1` and a `WATCHDOG=1` heartbeat every 15s over `$NOTIFY_SOCKET` (see `_systemd_notify` / `_watchdog_loop`). In production `BOT_TOKEN` lives in a systemd `EnvironmentFile`, not `.env`. `scripts/backup.sh` gzips the DB and pushes to Cloudflare R2 via rclone. Full setup steps are in `README.md` (in Spanish).

## Issue tracking

Issues and improvements are tracked in **Linear** (issue keys use the `CHE-` prefix, e.g. `CHE-5`), not in this repo. The numbered list (1–51) near the top of `README.md` is **legacy** — kept only for the design intent it records behind existing code; it's no longer maintained, so don't treat it as the live backlog.

## Claude Code Behavior Rules

### Indentation
Python files use **4-space** indentation (never tabs). When editing, match the exact whitespace of the target file.

### Git Workflow
Never commit directly to `main` — branch first. Commit or push only when the user asks.

### Implementation Discipline
After a fix or refactor, verify it actually removes the problematic code/pattern rather than keeping it as a fallback. This matters most for the auth path: an `except` or default branch must not quietly return data on failure — fail closed (401/403/503).

### Debugging Approach
Diagnose the root cause before editing. State it in 2–3 bullets and confirm before changing files. Don't try multiple speculative fixes at once.
