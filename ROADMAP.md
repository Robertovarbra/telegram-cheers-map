# Cheers Map — Product Vision & Roadmap

## Core Thesis

**Unfiltered, 1-take circular video moments with your real friends — pinned to the real world.**
No filters. No camera-roll uploads. No algorithmic feed.

Unlike disposable daily social feeds (which turn performative and lose retention), private friend circles + geography + time compound in value into a shared visual atlas of trips and life moments.

### Two-Stage Strategy
1. **Stage 1 — Telegram Bot + Mini App (Current):** Use Telegram as our zero-friction distribution wedge. Friend groups already exist there, the 1-take circular video camera is built in, Telegram hosts the media for free, and **Telegram Stars** enables native 1-tap monetization with zero infrastructure burn.
2. **Stage 2 — Standalone Cross-Platform App (Validation-Gated):** Expand to a dedicated iOS/Android app (same camera-only, no-filter, real-GPS circular video ruleset) to capture WhatsApp/iMessage friend groups **only after** Stage 1 proves organic group retention and willingness to pay.

---

## Monetization Model

Keep core pinning, mapping, and basic trips **free forever** so groups adopt it effortlessly. Monetize nostalgia, sharing, and archive upgrades via **Telegram Stars (`XTR`)**:

- **Trip Recap Reels (Pay-per-trip, 100 Stars):** Free 480p watermarked preview (`cheers map · unfiltered` acts as a viral sharing hook on Stories/TikTok) → paid 720p unwatermarked HD export unlocked for everyone in the group.
- **Group Pass (250 Stars / 30 days):** One friend unlocks unlimited HD unwatermarked Trip Recap Reels (and future group perks like permanent archive & web trip links) for the entire chat.
- **Future expansion:** One-time **Event Passes** (weddings, festivals, bachelor/bachelorette trips) and **Branded Community Maps** (run clubs, Erasmus networks, travel creators).

---

## Roadmap

### Step 1 — Foundation & Activation *(Done)*
- [x] Enforce group-membership check on `/start watch_...` deep links
- [x] DM-only `/admin_stats [days]` analytics command (`ADMIN_USER_IDS`)
- [x] Upgraded `/start` onboarding pitch + one-tap **Add to a Group** button + `bot_added` group welcome
- [x] Owner-only **Delete Pin** (`POST /api/delete-pin` + detail player button)

### Step 2 — Standalone-App Feel *(Done)*
- [x] Native Light/Dark theme (auto-matches Telegram + manual toggle, high-contrast palettes)
- [x] Frosted floating map controls + touch-friendly Filter bottom sheet + one-tap `Reset all` & active filter map pill
- [x] Chronological Trip route polylines + floating Trip banner
- [x] **Group Passport & Stats** (`GET /api/stats` — cheers, countries unlocked, per-user traveled distance, tappable country/city stamps, friend leaderboard)
- [x] Automatic content-hash JS cache-busting on deploy

### Step 3 — Trip Recap Reels + Telegram Stars *(Done)*
- [x] Vertical 9:16 FFmpeg + Pillow Trip Recap Reel generator (ambient blur, circular cutout, friend-colored ring, location/date stamp, disk LRU cache)
- [x] Free watermarked 480p preview vs. paid 720p unwatermarked HD tier (`402` server-side paywall)
- [x] Telegram Stars (`XTR`) checkout (`POST /api/reel-invoice`, `PreCheckoutQueryHandler`, `SUCCESSFUL_PAYMENT`, idempotent `payments` & `chat_entitlements` tables)
- [x] End-of-trip summary card with `startapp=c<chat>_t<trip>` deep link + native **Send Reel to Group**

---

### Step 4 — Retention & Personal Atlas *(Done)*
- [x] **Personal "My Atlas" view:** Open the bot in DM (`/start`, `/map`, or `/atlas`) to see all your own pins across every group + allow solo pinning in DM (`mode=atlas` API + personal Passport + per-chat filters)
- [x] **Pin reactions & lightweight captions:** Quick emoji reactions (`🍻🔥😂❤️🤯🥂`) inside the detail sheet (`POST /api/pin-reaction`) + owner-only inline caption editor (`POST /api/pin-caption`) & caption search
- [x] **Pin location editing:** Fix indoor GPS drift directly from the detail sheet with an interactive map crosshair picker (owner-only `POST /api/pin-location` + re-geocoding)
- [x] **"On this day" nostalgic throwbacks:** Daily background throwback loop + `/throwback [on|off]` command resurfacing past memories with date-filtered map deep links

---

### Step 5 — Viral Web Sharing & Demand Validation *(Next)*
- [ ] **Read-only Shareable Trip Web Links (Group Pass perk):** Let groups share a trip map with non-Telegram friends/family on the web — include a subtle *"Want this for your friend group?"* waitlist CTA to directly measure demand for a standalone app
- [ ] **Permanent Cloud Archive (Cloudflare R2 — Group Pass perk):** Back up group videos so memories survive if Telegram expires `file_id`s or messages are deleted
- [ ] **Interactive Demo Map on `/start`:** Let first-time visitors tap into a sample trip map before adding the bot to a group
- [ ] **Opt-in Public "Live Nearby Map" (Exploration):** Let users optionally mark a solo/group cheers as public (e.g. 24h ephemeral) so anyone can center on their current location and discover real unfiltered moments posted nearby — keeping private groups strictly private by default

### Step 6 — Scale Hardening & Stage 2 Gate
- [ ] Replace public Nominatim reverse geocoding with an offline GeoNames dataset (instant lookups, no 1 req/s bottleneck or commercial ToS limit)
- [ ] Non-blocking SQLite access (`aiosqlite`) + background `cleanup_inactive_chats` task
- [ ] Automated `pytest` suite covering `auth.py`, `database.py`, `web_handlers.py`, and payment flows
- [ ] **Stage 2 Go/No-Go Check:** Evaluate `/admin_stats` (weekly active groups, `pin_started → pin_created` completion, repeat map opens, Stars conversion, and web-share waitlist signups) before investing in a standalone native iOS/Android app
