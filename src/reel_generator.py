from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from config import REEL_CACHE_DIR, REEL_CACHE_MAX_BYTES, REEL_CLIP_MAX_SEC, logger

if TYPE_CHECKING:
    from PIL import ImageFont

# Palette matching web/js/config.js so friends without a custom pin_color get the same ring color
# in their Trip Recap Reel as on the map.
_PALETTE = ("#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c", "#e67e22", "#34495e", "#e91e63", "#00bcd4")

# Serialize FFmpeg renders so a Raspberry Pi never runs multiple heavy encodes concurrently.
_render_semaphore = asyncio.Semaphore(1)
_reel_locks: dict[str, asyncio.Lock] = {}
_reel_lru: OrderedDict[str, int] = OrderedDict()
_reel_cache_total = 0
_reel_cache_ready = False

_FONT_CANDIDATES_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)
_FONT_CANDIDATES_REG = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    from PIL import ImageFont

    candidates = _FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES_REG
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _parse_hex_rgb(col: str | None, user_id: int) -> tuple[int, int, int]:
    raw = (col or "").strip() or _PALETTE[abs(user_id) % len(_PALETTE)]
    s = raw.lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) == 6:
        try:
            return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        except ValueError:
            pass
    return (47, 128, 237)


def _short_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%b %-d")
    except ValueError:
        return ""


def _truncate(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def _render_circle_mask(diameter: int, out_path: Path) -> None:
    """Create a 2x-supersampled anti-aliased white circle mask on a black background."""
    from PIL import Image, ImageDraw

    ss = diameter * 2
    img = Image.new("L", (ss, ss), 0)
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, ss - 3, ss - 3), fill=255)
    img.resize((diameter, diameter), Image.Resampling.LANCZOS).save(out_path, "PNG")


def _render_overlay_png(
    width: int,
    height: int,
    diameter: int,
    cy: int,
    trip_name: str,
    idx: int,
    total: int,
    user_name: str,
    location_str: str,
    date_str: str,
    ring_rgb: tuple[int, int, int],
    watermark: bool,
    out_path: Path,
) -> None:
    """Render a transparent 9:16 RGBA card: subtle scrims, colored circular ring, trip header,
    author/location caption, and optional free-tier watermark."""
    from PIL import Image, ImageDraw

    scale = width / 720.0
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Top and bottom dark vignette bands for effortless text legibility over any clip.
    top_band = int(210 * scale)
    for y in range(top_band):
        alpha = int(155 * (1.0 - y / top_band) ** 1.4)
        draw.line((0, y, width, y), fill=(10, 12, 16, alpha))

    bot_band = int(380 * scale)
    for dy in range(bot_band):
        y = height - bot_band + dy
        alpha = int(185 * (dy / bot_band) ** 1.3)
        draw.line((0, y, width, y), fill=(10, 12, 16, alpha))

    # Colored ring + crisp white inner rim framing the circular video note.
    cx = width // 2
    r = diameter // 2
    outer_w = max(5, int(10 * scale))
    inner_w = max(2, int(4 * scale))
    draw.ellipse(
        (cx - r - outer_w, cy - r - outer_w, cx + r + outer_w, cy + r + outer_w),
        outline=(*ring_rgb, 245),
        width=outer_w,
    )
    draw.ellipse(
        (cx - r - inner_w // 2, cy - r - inner_w // 2, cx + r + inner_w // 2, cy + r + inner_w // 2),
        outline=(255, 255, 255, 245),
        width=inner_w,
    )

    # Top trip header pill
    f_trip = _load_font(max(18, int(30 * scale)), bold=True)
    f_cnt = _load_font(max(14, int(22 * scale)), bold=True)
    trip_label = _truncate(trip_name, 26)
    cnt_label = f"{idx}/{total}"

    tb = draw.textbbox((0, 0), trip_label, font=f_trip)
    cb = draw.textbbox((0, 0), cnt_label, font=f_cnt)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    cw, ch = cb[2] - cb[0], cb[3] - cb[1]
    pad_x, pad_y, gap = int(22 * scale), int(12 * scale), int(14 * scale)
    pill_w = min(width - int(48 * scale), tw + cw + pad_x * 2 + gap)
    pill_h = max(th, ch) + pad_y * 2
    px0 = (width - pill_w) // 2
    py0 = int(68 * scale)
    draw.rounded_rectangle(
        (px0, py0, px0 + pill_w, py0 + pill_h),
        radius=pill_h // 2,
        fill=(18, 22, 28, 175),
        outline=(255, 255, 255, 45),
        width=max(1, int(2 * scale)),
    )
    tx = px0 + pad_x
    ty = py0 + (pill_h - th) // 2 - tb[1]
    draw.text((tx, ty), trip_label, font=f_trip, fill=(255, 255, 255, 245))
    cx_txt = px0 + pill_w - pad_x - cw
    cy_txt = py0 + (pill_h - ch) // 2 - cb[1]
    draw.text((cx_txt, cy_txt), cnt_label, font=f_cnt, fill=(165, 200, 255, 235))

    # Bottom author + location/date caption
    f_user = _load_font(max(20, int(38 * scale)), bold=True)
    f_sub = _load_font(max(14, int(25 * scale)), bold=False)

    user_label = _truncate(user_name, 24)
    sub_parts = [p for p in (location_str, date_str) if p]
    sub_label = _truncate("  ·  ".join(sub_parts), 38)

    base_y = cy + r + int(68 * scale)
    ub = draw.textbbox((0, 0), user_label, font=f_user)
    uw, uh = ub[2] - ub[0], ub[3] - ub[1]
    draw.text(((width - uw) // 2, base_y - ub[1]), user_label, font=f_user, fill=(255, 255, 255, 250))

    if sub_label:
        sb = draw.textbbox((0, 0), sub_label, font=f_sub)
        sw = sb[2] - sb[0]
        draw.text(((width - sw) // 2, base_y + uh + int(14 * scale) - sb[1]), sub_label, font=f_sub, fill=(215, 222, 234, 225))

    # Free-preview watermark badge at the very bottom
    if watermark:
        f_wm = _load_font(max(13, int(20 * scale)), bold=True)
        wm_text = "cheers map  ·  unfiltered"
        wb = draw.textbbox((0, 0), wm_text, font=f_wm)
        ww, wh = wb[2] - wb[0], wb[3] - wb[1]
        wp_x, wp_y = int(18 * scale), int(9 * scale)
        wx0 = (width - ww) // 2 - wp_x
        wy0 = height - int(96 * scale)
        draw.rounded_rectangle(
            (wx0, wy0, wx0 + ww + wp_x * 2, wy0 + wh + wp_y * 2),
            radius=(wh + wp_y * 2) // 2,
            fill=(16, 18, 24, 170),
            outline=(255, 255, 255, 55),
            width=max(1, int(1.5 * scale)),
        )
        draw.text((wx0 + wp_x, wy0 + wp_y - wb[1]), wm_text, font=f_wm, fill=(255, 255, 255, 215))

    img.save(out_path, "PNG")


def _init_reel_cache() -> None:
    global _reel_cache_total, _reel_cache_ready
    if _reel_cache_ready:
        return
    REEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    found = []
    for p in REEL_CACHE_DIR.iterdir():
        if p.is_file() and p.name.endswith(".mp4"):
            try:
                st = p.stat()
                found.append((st.st_mtime, p.name, st.st_size))
            except OSError:
                pass
    for _, name, size in sorted(found):
        _reel_lru[name] = size
        _reel_cache_total += size
    _evict_reels()
    _reel_cache_ready = True


def _evict_reels() -> None:
    global _reel_cache_total
    while _reel_cache_total > REEL_CACHE_MAX_BYTES and len(_reel_lru) > 1:
        name, size = _reel_lru.popitem(last=False)
        _reel_cache_total -= size
        try:
            (REEL_CACHE_DIR / name).unlink()
        except OSError:
            pass


def _record_reel(name: str, size: int) -> None:
    global _reel_cache_total
    old = _reel_lru.pop(name, None)
    if old is not None:
        _reel_cache_total -= old
    _reel_lru[name] = size
    _reel_cache_total += size
    _evict_reels()


def _reel_cache_key(chat_id: int, trip_id: int, trip_name: str, hd: bool, pins: list[dict]) -> str:
    payload = {
        "v": 1,
        "chat_id": chat_id,
        "trip_id": trip_id,
        "trip_name": trip_name,
        "hd": hd,
        "pins": [(p["id"], p["video_file_id"], p["user_name"], p.get("city"), p.get("country"), p.get("created_at"), p.get("pin_color")) for p in pins],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
    tier = "hd" if hd else "preview"
    return f"reel_{trip_id}_{tier}_{digest}.mp4"


async def _has_audio_stream(video_path: Path) -> bool:
    if not shutil.which("ffprobe"):
        return False
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(video_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return bool(out.strip())


async def _run_ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err_tail = stderr.decode(errors="replace")[-600:]
        raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}): {err_tail}")


async def _render_reel_file(trip_name: str, clip_items: list[tuple[dict, Path]], hd: bool, out_path: Path) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg binary not found on PATH")

    width, height = (720, 1280) if hd else (480, 854)
    diameter = 480 if hd else 320
    cy = 590 if hd else 395
    ox = (width - diameter) // 2
    oy = cy - diameter // 2
    total = len(clip_items)

    with tempfile.TemporaryDirectory(prefix="cheers-reel-") as tmp_str:
        tmp = Path(tmp_str)
        mask_path = tmp / "mask.png"
        await asyncio.to_thread(_render_circle_mask, diameter, mask_path)

        seg_paths: list[Path] = []
        for idx, (pin, src_video) in enumerate(clip_items, start=1):
            overlay_path = tmp / f"ov_{idx}.png"
            loc_parts = [x for x in (pin.get("city"), pin.get("country")) if x]
            loc_str = ", ".join(loc_parts)
            date_str = _short_date(pin.get("created_at"))
            ring_rgb = _parse_hex_rgb(pin.get("pin_color"), pin["user_id"])

            await asyncio.to_thread(
                _render_overlay_png,
                width,
                height,
                diameter,
                cy,
                trip_name,
                idx,
                total,
                pin["user_name"],
                loc_str,
                date_str,
                ring_rgb,
                not hd,
                overlay_path,
            )

            has_audio = await _has_audio_stream(src_video)
            seg_path = tmp / f"seg_{idx:03d}.mp4"

            v_filter = (
                f"[0:v]trim=duration={REEL_CLIP_MAX_SEC},setpts=PTS-STARTPTS,fps=30,split=2[vbg][vfg];"
                f"[vbg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                f"boxblur=18:4,eq=brightness=-0.22:saturation=1.15[bg];"
                f"[vfg]scale={diameter}:{diameter}:force_original_aspect_ratio=increase,crop={diameter}:{diameter},format=rgba[fgr];"
                f"[1:v]format=gray[msk];"
                f"[fgr][msk]alphamerge[circ];"
                f"[bg][circ]overlay={ox}:{oy}:shortest=1[comp];"
                f"[comp][2:v]overlay=0:0:shortest=1,format=yuv420p[vout]"
            )

            cmd = [
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(src_video),
                "-loop",
                "1",
                "-i",
                str(mask_path),
                "-loop",
                "1",
                "-i",
                str(overlay_path),
            ]
            if has_audio:
                audio_filter = f";[0:a]atrim=duration={REEL_CLIP_MAX_SEC},asetpts=PTS-STARTPTS,aformat=sample_rates=44100:channel_layouts=stereo[aout]"
                filter_complex = v_filter + audio_filter
            else:
                cmd.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"])
                filter_complex = v_filter + ";[3:a]aformat=sample_rates=44100:channel_layouts=stereo[aout]"

            cmd.extend(
                [
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[vout]",
                    "-map",
                    "[aout]",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "23" if hd else "26",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-ar",
                    "44100",
                    "-ac",
                    "2",
                    "-shortest",
                    str(seg_path),
                ]
            )
            await _run_ffmpeg(cmd)
            seg_paths.append(seg_path)

        concat_list = tmp / "concat.txt"
        concat_list.write_text("\n".join(f"file '{p.as_posix()}'" for p in seg_paths) + "\n", encoding="utf-8")

        tmp_out = out_path.with_name(out_path.name + ".tmp")
        try:
            await _run_ffmpeg(
                [
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_list),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    "-f",
                    "mp4",
                    str(tmp_out),
                ]
            )
            tmp_out.replace(out_path)
        finally:
            tmp_out.unlink(missing_ok=True)


async def get_or_render_trip_reel(chat_id: int, trip_id: int, trip_name: str, pins: list[dict], hd: bool, fetch_video_fn) -> Path:
    """Return the path to a cached or freshly rendered 9:16 Trip Recap Reel MP4.

    `fetch_video_fn(file_id) -> Path` ensures each clip is on local disk (reusing video_cache)."""
    _init_reel_cache()
    cache_name = _reel_cache_key(chat_id, trip_id, trip_name, hd, pins)
    out_path = REEL_CACHE_DIR / cache_name

    if cache_name in _reel_lru and out_path.exists():
        _reel_lru.move_to_end(cache_name)
        return out_path

    lock = _reel_locks.setdefault(cache_name, asyncio.Lock())
    async with lock:
        if cache_name in _reel_lru and out_path.exists():
            _reel_lru.move_to_end(cache_name)
            return out_path

        clip_items: list[tuple[dict, Path]] = []
        for pin in pins:
            try:
                vpath = await fetch_video_fn(pin["video_file_id"])
                if vpath and vpath.exists():
                    clip_items.append((pin, vpath))
            except Exception as e:
                logger.warning("Skipping pin %s in reel for trip %s: %s", pin["id"], trip_id, e)

        if not clip_items:
            raise RuntimeError("No playable clips found for this trip")

        async with _render_semaphore:
            await _render_reel_file(trip_name, clip_items, hd, out_path)

        _record_reel(cache_name, out_path.stat().st_size)
        return out_path
