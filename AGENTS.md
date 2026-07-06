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

## Verifying your own frontend changes (no Telegram account needed)

There's no test suite, and you **cannot** exercise the Mini App through real Telegram from here — there's no Telegram account, and the `/api/*` endpoints reject any request without a valid `initData` HMAC, which you can't forge without `BOT_TOKEN`. So to actually *see* a `web/` change run (looping markers, viewport gating, zoom clustering, the detail overlay, colors), drive a real Chrome against a throwaway local server that serves the real frontend with **fake `/api/*` data and auth disabled**. This is the harness used to verify the video-marker work; rebuild it the same way for any future `web/` change.

Two things make the local server non-optional: `web/index.html` loads `web/js/*.js` as **ES modules** (`<script type="module">`), which won't load over `file://` (CORS), and the `<video>` proxy needs **HTTP Range** support to seek/loop.

**1. A sample clip + a mock server** (both live in `/tmp` — never commit them):

```bash
mkdir -p /tmp/cheers-map-test
# short looping test clip with a fast-start moov atom so it streams immediately
ffmpeg -f lavfi -i testsrc=duration=3:size=240x240:rate=15 -pix_fmt yuv420p \
  -movflags +faststart -y /tmp/cheers-map-test/sample.mp4
```

```python
# /tmp/cheers-map-test/server.py  — throwaway; serves the REAL web/ with mocked /api/*
import http.server, json, re

WEB = "/Users/mauvqz/Documents/code/cheers-map/web"   # point at the repo's web/
VIDEO = "/tmp/cheers-map-test/sample.mp4"
# fixtures: a 3-user cluster at one spot (exercises ring colors + count badge + label-hiding) and a lone far pin
PINS = [
    {"id": 1, "latitude": 48.8566, "longitude": 2.3522, "user_id": 1, "user_name": "Alice", "pin_color": "#e74c3c", "pin_emoji": "\U0001f37a", "created_at": "2026-06-10T12:00:00", "video_file_id": "f1"},
    {"id": 2, "latitude": 48.8567, "longitude": 2.3523, "user_id": 2, "user_name": "Bob",   "pin_color": "#3498db", "pin_emoji": "\U0001f377", "created_at": "2026-06-10T13:00:00", "video_file_id": "f2"},
    {"id": 3, "latitude": 48.8566, "longitude": 2.3522, "user_id": 3, "user_name": "Cara",  "pin_color": "#2ecc71", "pin_emoji": "\U0001f942", "created_at": "2026-06-10T14:00:00", "video_file_id": "f3"},
    {"id": 4, "latitude": 40.4168, "longitude": -3.7038, "user_id": 1, "user_name": "Alice", "pin_color": "#e74c3c", "pin_emoji": "\U0001f37a", "created_at": "2026-06-09T10:00:00", "video_file_id": "f1"},
]

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=WEB, **k)
    def do_GET(self):
        if self.path.startswith("/api/pins"):          # the only read the map needs; returns a bare array
            body = json.dumps(PINS).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if self.path.startswith("/api/video"):
            data = open(VIDEO, "rb").read(); n = len(data); rng = self.headers.get("Range")
            if rng:                                     # Range support = seeking/looping works
                m = re.search(r"bytes=(\d+)-(\d*)", rng); s = int(m.group(1)); e = int(m.group(2)) if m.group(2) else n - 1
                chunk = data[s:e + 1]
                self.send_response(206); self.send_header("Content-Range", f"bytes {s}-{e}/{n}")
            else:
                chunk = data; self.send_response(200)
            self.send_header("Accept-Ranges", "bytes"); self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(chunk))); self.end_headers(); self.wfile.write(chunk); return
        return super().do_GET()                         # serves index.html + /js/*.js statically

H.extensions_map[".js"] = "text/javascript"             # modules need the right MIME or Chrome refuses them
http.server.HTTPServer(("127.0.0.1", 8777), H).serve_forever()
```

```bash
python3 /tmp/cheers-map-test/server.py &     # or run_in_background
```

**2. Drive it with the Chrome DevTools MCP** (`chrome-devtools` tools): `navigate_page` to `http://localhost:8777/?chat_id=-1001`. The frontend reads the chat from the `?chat_id=` query param first, so the signed `start_param` is never needed; `window.Telegram.WebApp.initData` is empty and the mock ignores the (empty) `Authorization: tma` header. Then verify with `take_screenshot` and `evaluate_script` — e.g. `document.elementFromPoint(x, y)` should return a `VIDEO.marker-video` (catches the stacking-context bug where a placeholder paints over the clip), a marker video's `currentTime` should keep advancing and wrap past clip end (looping), and changing `map.setZoom(...)` then re-reading marker count verifies pixel clustering.

**3. Fast no-browser syntax gate** before any of the above: `for f in web/js/*.js; do node --check "$f"; done`.

**Gotchas (each cost real time before):**
- Kill a stale server by **port, not name** (the process arg is just `server.py`): `lsof -nP -iTCP:8777 -sTCP:LISTEN -t | xargs kill`.
- "Browser already running" from the MCP means a stale **MCP-owned** Chrome — it runs its own dedicated profile (`chrome-devtools-mcp/chrome-profile`), **not** your personal Chrome. Kill the process that has `--user-data-dir=...chrome-devtools-mcp...` and no `--type=`; never the user's Chrome.
- Clean up when done: `rm -rf /tmp/cheers-map-test`.

### Driving the REAL backend, auth included (no Telegram account needed either)

The mock harness above skips `auth.py` and all of `web_handlers.py`. When a change touches the API or auth path, run the **real** app instead: real routes, real HMAC verification, real handlers, real SQLite. The trick is that `verify_init_data` checks the signature against whatever `BOT_TOKEN` you set — so a locally-set fake token lets you **forge valid `initData`**. Verified working for the trips feature; rebuild it the same way (throwaway launcher script, run from `src/`, never committed):

1. **Launcher shape** — set `BOT_TOKEN` (any string) *before* importing `config`; then import the real modules and patch the seams:
   - `database.DB_PATH = <tempdir>/e2e.db` (module attribute — `config.DB_PATH` was already bound at import), then `init_db()` + `migrate_db()` and seed pins/trips/pending rows directly.
   - `web_handlers.VIDEO_CACHE_DIR = <tempdir>/video_cache`, `web_handlers.reverse_geocode = <async fake>` (keeps Nominatim out).
   - Build a `web.Application`, register the same routes as `main.py`, and set `app["bot"]` to a stub: `get_chat_member` returns `.status = "member"` (return `"left"` for one magic chat id to probe 403s), `get_file` returns `.file_path = "http://127.0.0.1:<port>/clip-src.mp4"` pointing at a static route on the same app — this exercises the real video proxy (Range, streaming, disk cache) end to end.
2. **Forge initData** (must match `auth.py`): `fields = {auth_date, query_id, user: json.dumps({...})}`; data-check-string = sorted `k=v` lines joined by `\n`; `secret = HMAC_SHA256(key=b"WebAppData", msg=BOT_TOKEN)`; `fields["hash"] = HMAC_SHA256(key=secret, msg=dcs).hexdigest()`; result is `urlencode(fields)`. Use it in curl via `Authorization: tma <initData>`.
3. **Get it into the browser** through Telegram's own mechanism — no stubbing `window.Telegram`: navigate to `http://127.0.0.1:<port>/?chat_id=<id>#tgWebAppData=<urlquote(initData, safe='')>&tgWebAppVersion=8.0&tgWebAppPlatform=web`. The CDN `telegram-web-app.js` parses the hash and populates `initData`, so the frontend's real `authHeaders()` path runs against the real verifier.
4. Assert **through the surface and below it**: curl the auth probes (no auth / tampered hash / non-member chat → 401/401/403), drive the UI via MCP Chrome, then check writes landed with `sqlite3 <tempdir>/e2e.db`.

Telegram *handlers* (`/trip` etc.) still can't be reached this way — that layer needs faked `Update`/`CallbackQuery` objects calling the handler functions directly (real handlers + real DB, fake transport), which is weaker evidence; say so when reporting.

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
1. `handle_video` (filter `VIDEO_NOTE`, groups only) persists the video to the `pending_pins` table (`database.set_pending_pin`, keyed by `(chat_id, user_id)`) and replies with a **📍 Share location** inline button (a deep link to the Mini App in location mode, `startapp=loc_c{chat_id}`).
2. The location arrives one of two ways, and **both consume the same `pending_pins` row** (whichever completes first wins — `pop_pending_pin` deletes it atomically inside a `BEGIN IMMEDIATE` txn, so there's no double-pin):
   - **Mini App (one-tap):** the button opens `web/index.html` in share mode; `LocationManager.getLocation()` (Bot API 8.0+) reads the device location and POSTs it to `POST /api/submit-location`, which authorizes the caller, pops the pending row, and calls `add_pin`.
   - **Attachment menu (fallback):** the user sends a location message; `handle_location` pops the pending row (keyed by the *sender's* `(chat_id, user_id)`, which is what enforces "only the original sender completes it"), calls `reverse_geocode`, then `add_pin`.

`request_location` reply-keyboard buttons are private-chat only (they don't fire in groups), which is why the one-tap path routes through the Mini App rather than a native keyboard button. Pending rows are bounded (one per user per chat, overwritten on re-send) and swept on startup via `delete_stale_pending_pins` (24 h TTL) and on chat removal via `delete_chat_pins`. Bot prompt/confirmation messages are auto-deleted after a few seconds (best-effort, errors swallowed).

### Videos are never stored — only Telegram `file_id`
`add_pin` stores the `file_id`. The frontend `<video>` points at `/api/video?file_id=...`, and `handle_video_proxy` resolves the file via `bot.get_file()` then streams it from Telegram's CDN, forwarding the `Range` header (essential for seeking). This is why the web server needs the shared bot instance.

### Reverse geocoding (Nominatim / OpenStreetMap)
`reverse_geocode` in `telegram_handlers.py` hits the public Nominatim API, guarded by an `asyncio.Lock`, an in-memory LRU-ish cache (max 500 entries), and a `sleep(1)` to respect rate limits. The cache is in-memory only (lost on restart).

### Chat lifecycle / data cleanup
When the bot is removed from a chat (`handle_my_chat_member` → LEFT/BANNED), **all that chat's pins are deleted**. On startup, `cleanup_inactive_chats` checks every known chat and purges pins for any the bot can no longer access.

### Mini App + map (`web/index.html`)
Single static HTML file, vanilla JS + Leaflet from unpkg CDN — **no build step**. The `/map` command opens it as a Telegram Mini App via `t.me/{BOT_USERNAME}/map?startapp=c{chat_id}`. The frontend derives `chat_id` from (in order) `?chat_id=`, `tgWebAppStartParam`, or `Telegram.WebApp.initDataUnsafe.start_param`, parsing the `c{chatId}` format. All dynamic values are escaped via `esc()` (XSS guard). Pins within `GROUP_RADIUS` (10 m) are clustered. Filtering is split: **server-side** (`user_ids`, `date_from`, `date_to`, `q` location) with LIMIT/OFFSET 500 pagination, plus **client-side** ("only in map view" bounds filter, which disables pagination).

The same file also serves a **location-share mode**: when the start param begins with `loc_` (e.g. `startapp=loc_c{chat_id}`), `startLocationShare()` replaces the map with a one-tap screen, captures the device location via `Telegram.WebApp.LocationManager`, and POSTs it to `/api/submit-location`. It gracefully degrades — unsupported client (< Bot API 8.0), denied permission, or timeout all fall back to a "use the 📍 attachment menu" message. To halt the normal map init in this mode it `throw`s after dispatching (same pattern as the missing-`chat_id` guard).

## Security

The Mini App API (`/api/*`) is the only externally reachable surface (behind the Cloudflare Tunnel). Two invariants gate every chat-scoped request — both live in `auth.py` and are bundled by `_authorize(request, chat_id)` in `web_handlers.py`:

1. **initData HMAC** — the request must carry a valid Telegram `initData`, verified by `verify_init_data` (HMAC-SHA256 against `BOT_TOKEN`, plus `auth_date` freshness vs `INITDATA_MAX_AGE`). `extract_init_data` reads it from the `Authorization: tma <data>` header, falling back to `?auth=` for `<video>` tags that can't set headers. **Never trust a client-supplied `chat_id`/`user_id` that isn't backed by a valid signature** — the `chat_id` query param is attacker-controlled; only the signed `user.id` is trustworthy.
2. **Membership** — `is_member(bot, chat_id, user_id)` must pass before returning chat data, so a caller can only read chats they belong to (the `chat_id`-scoped equivalent of tenant isolation). Results are cached ~5 min. `handle_video_proxy` does its own variant: it verifies `initData`, then allows the stream only if the user is a member of *some* chat that references the `file_id` (`get_chat_ids_for_file`).

`POST /api/submit-location` (the Mini App one-tap location path) is chat-scoped and goes through `_authorize` like the read endpoints, but it **writes**: it attributes the new pin to the *signed* `user.id` (never a body/query value) and pops the pending row by that same `(chat_id, user_id)`, so a caller can only ever complete their own pending cheers. Coordinates are range-checked before use.

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

Runs on a Raspberry Pi under systemd (`Type=notify` with `WatchdogSec=30`) behind a Cloudflare Tunnel. `main.py` sends `READY=1` and a `WATCHDOG=1` heartbeat every 15s over `$NOTIFY_SOCKET` (see `_systemd_notify` / `_watchdog_loop`). In production `BOT_TOKEN` lives in a systemd `EnvironmentFile`, not `.env`. `scripts/backup.sh` gzips the DB and pushes to Cloudflare R2 via rclone.

### CI/CD pipeline

Deploys are automated via **GitHub Actions + Tailscale**. The workflow (`.github/workflows/deploy.yml`) is triggered on merge to `main` or manually via workflow_dispatch:

1. **Tailscale**: the GitHub runner joins the tailnet using an OAuth client (`tag:ci`)
2. **SSH**: connects to the Pi via its Tailscale IP using a dedicated SSH key
3. **`scripts/deploy.sh`**: executes `git fetch && git reset --hard origin/main`, `uv sync --frozen --no-dev`, `ruff check .`, and `sudo systemctl restart cheers-bot`

No SSH access to the Pi is required for routine deploys. Full setup steps are in `README.md` (in Spanish).

**Key infrastructure**: `TAILSCALE_IP`, `PI_USER`, `SSH_PRIVATE_KEY`, `TS_OAUTH_CLIENT_ID`, and `TS_OAUTH_SECRET` are stored as GitHub Actions secrets.

## Issue tracking

Issues and improvements are tracked in **Linear** (issue keys use the `CHE-` prefix, e.g. `CHE-5`), not in this repo.

## Claude Code Behavior Rules

### Indentation
Python files use **4-space** indentation (never tabs). When editing, match the exact whitespace of the target file.

### Git Workflow
Never commit directly to `main` — branch first. Commit or push only when the user asks.

### Implementation Discipline
After a fix or refactor, verify it actually removes the problematic code/pattern rather than keeping it as a fallback. This matters most for the auth path: an `except` or default branch must not quietly return data on failure — fail closed (401/403/503).

### Debugging Approach
Diagnose the root cause before editing. State it in 2–3 bullets and confirm before changing files. Don't try multiple speculative fixes at once.
