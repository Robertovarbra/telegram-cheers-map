// Detail overlay: tap a marker -> enlarged video (with sound) + the list of cheers at that spot.
import { state } from './state.js';
import { esc, relTime, placeholderGlyph, byNewest, tripDupDate } from './util.js';
import { getColor } from './color.js';
import { videoUrl, apiPost } from './api.js';
import { applyTripCountsDelta } from './filters.js';
import { pauseMarkerVideos, updateVideoPlayback, teardownVideo } from './video.js';
import { MAX_CONCURRENT_VIDEOS } from './config.js';

let detailIdx = 0;     // which row in the open detail list is selected
// How the big player behaves: when loop is OFF (the default) it auto-advances newest -> oldest
// (wrapping); when ON it replays the current clip. Always starts OFF when the player opens; the
// loop button in the top bar toggles it.
let loopMode = false;
let reverseMode = false;
let detailIo = null;  // IntersectionObserver for lazy-loading thumbnails

// Sync the loop button's active state and the playing <video>'s loop flag to the current mode.
function applyLoopMode() {
  var loopBtn = document.getElementById('mode-loop');
  if (loopBtn) {
    loopBtn.classList.toggle('active', loopMode);
    loopBtn.setAttribute('aria-pressed', loopMode ? 'true' : 'false');
  }
  var v = document.getElementById('detail-bigvid');
  if (v) v.loop = loopMode;  // loop=true means 'ended' never fires, so it just repeats
}

function toggleLoop() {
  loopMode = !loopMode;
  applyLoopMode();
}

function toggleReverse() {
  reverseMode = !reverseMode;
  if (!state.detailPins || state.detailPins.length <= 1) {
    applyReverseMode();
    return;
  }
  state.detailPins.reverse();
  renderDetailList();
  selectDetail(0);
  applyReverseMode();
}

function applyReverseMode() {
  var btn = document.getElementById('mode-reverse');
  if (btn) {
    btn.classList.toggle('active', reverseMode);
    btn.setAttribute('aria-pressed', reverseMode ? 'true' : 'false');
    var svg = btn.querySelector('svg');
    if (svg) {
      svg.innerHTML = reverseMode
        ? '<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>'
        : '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>';
    }
  }
}

// Open the bottom-left "Play": the videos in the bubbles currently on screen, newest first,
// auto-advancing. Built from the rendered cluster markers (videoMarkers) filtered to the visible
// bounds — so it plays exactly what the user can see, not every filtered pin in the dataset — then
// expanded so each visible bubble contributes all of its cheers.
export function playAll() {
  var bounds = state.map.getBounds();
  var pins = (state.videoMarkers || [])
    .filter(function (vm) { return bounds.contains([vm.lat, vm.lng]); })
    .reduce(function (acc, vm) { return acc.concat(vm.pins || []); }, [])
    .sort(byNewest);
  if (!pins.length) return;
  openDetail(pins, 0);
}

function renderDetailList() {
  var list = document.getElementById('detail-list');
  var pins = state.detailPins;
  var lh = '';
  pins.forEach(function (p, i) {
    var c = getColor(p.user_id, p.pin_color);
    lh += '<div class="detail-row" data-idx="' + i + '">' +
      '<div class="detail-thumb" style="box-shadow:0 0 0 2px ' + esc(c) + '">' +
        '<div class="vm-ph" style="background:' + esc(c) + '">' + placeholderGlyph(p) + '</div>' +
        '<video muted loop playsinline preload="none" data-file-id="' + esc(p.video_file_id) + '"></video>' +
      '</div>' +
      '<div class="detail-rowtext">' +
        '<div class="dr-line1"><span class="dr-name">' + esc(p.user_name) + '</span> <span class="dr-date">— ' + esc(relTime(p.created_at)) + '</span></div>' +
        '<div class="dr-trip">' + rowTripLabel(p) + '</div>' +
      '</div>' +
      '<span class="dr-check">✓</span>' +
    '</div>';
  });
  list.innerHTML = lh;
  renderRowMarks();  // re-apply selection highlights (e.g. after a reverse-order re-render)
  document.getElementById('detail-count').textContent = pins.length + ' ' + (pins.length === 1 ? 'video' : 'videos');
  var thumbs = list.querySelectorAll('video[data-file-id]');
  if (detailIo) detailIo.disconnect();
  var loadedCount = 0;
  // Load the first MAX_CONCURRENT_VIDEOS thumbnails immediately; lazy-load the rest on scroll.
  for (var ti = 0; ti < thumbs.length && ti < MAX_CONCURRENT_VIDEOS; ti++) {
    (function (v) {
      v.src = videoUrl(v.dataset.fileId);
      loadedCount++;
      v.addEventListener('loadeddata', function () { v.classList.add('ready'); });
      var r = v.play(); if (r && r.catch) r.catch(function () {});
    })(thumbs[ti]);
  }
  // Observe all thumbnails via Intersection Observer: off-screen ones release their slot,
  // allowing on-screen ones beyond the first 8 to load while respecting the concurrent cap.
  detailIo = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      var v = entry.target;
      if (entry.isIntersecting && !v.src) {
        if (loadedCount < MAX_CONCURRENT_VIDEOS) {
          v.src = videoUrl(v.dataset.fileId);
          loadedCount++;
          v.addEventListener('loadeddata', function () { v.classList.add('ready'); });
          var r = v.play(); if (r && r.catch) r.catch(function () {});
        }
      } else if (!entry.isIntersecting && v.src) {
        teardownVideo(v);
        v.classList.remove('ready');
        loadedCount--;
      }
    });
  }, { root: list.parentElement, threshold: 0.01 });
  thumbs.forEach(function (v) { detailIo.observe(v); });
}

export function openDetail(pins, idx) {
  if (!pins || !pins.length) return;
  loopMode = false;
  reverseMode = false;
  tripSel.clear();          // never carry a stale selection into a freshly opened player
  targetTripId = undefined;
  pauseMarkerVideos();
  state.detailPins = pins;
  renderDetailList();
  document.getElementById('detail').classList.remove('hidden');
  applyLoopMode();
  applyReverseMode();
  selectDetail(idx);
}

// ---- Trip tagging: long-press to select cheers, then assign the selection to a trip ----
// Deliberate by design (no always-on control, so nobody mis-tags with a stray tap). Normal mode
// shows the playing clip's trip as a read-only pill; long-pressing a row enters selection mode,
// where you tap rows to add/remove, pick a destination chip, and Apply. The whole bar is hidden
// when the chat has no trips.
var tripSel = new Set();  // pin ids selected for (re)assignment; non-empty == selection mode
var targetTripId;         // chosen destination: undefined = none picked yet, null = "No trip", number = a trip
var lpTimer = null, lpFired = false, lpStart = null;  // long-press tracking

function inSelectMode() { return tripSel.size > 0; }

function tripName(tripId) {
  if (tripId == null) return null;
  var t = state.trips.find(function (x) { return x.id === tripId; });
  return t ? t.name : null;
}

// The per-row trip tag markup (escaped for innerHTML); empty string when the cheers has no trip.
function rowTripLabel(pin) {
  var name = tripName(pin.trip_id);
  return name ? '🍻 ' + esc(name) : '';
}

// Update the rows' trip tags in place after a (re)assignment — avoids a full list re-render, which
// would reload every thumbnail. Uses textContent (no escaping needed).
function refreshRowTrips() {
  document.querySelectorAll('.detail-row').forEach(function (r) {
    var p = state.detailPins && state.detailPins[parseInt(r.dataset.idx, 10)];
    var el = r.querySelector('.dr-trip');
    if (!p || !el) return;
    var name = tripName(p.trip_id);
    el.textContent = name ? '🍻 ' + name : '';
  });
}

function renderTripBar() {
  var bar = document.getElementById('detail-trip-bar');
  if (!state.trips.length) { bar.classList.add('hidden'); return; }
  bar.classList.remove('hidden');
  var view = document.getElementById('dtb-view');
  var edit = document.getElementById('dtb-edit');
  if (inSelectMode()) {
    view.classList.add('hidden');
    edit.classList.remove('hidden');
    document.getElementById('dtb-count').textContent = tripSel.size + ' selected';
    document.getElementById('pin-trip-apply').disabled = targetTripId === undefined;
    renderTargetChips();
  } else {
    edit.classList.add('hidden');
    view.classList.remove('hidden');
    var cur = state.detailPins && state.detailPins[detailIdx];
    var name = cur ? tripName(cur.trip_id) : null;
    var pill = document.getElementById('dtb-current');
    pill.textContent = name || 'No trip';
    pill.classList.toggle('empty', !name);
  }
}

function renderTargetChips() {
  var wrap = document.getElementById('dtb-targets');
  var html = '<span class="tt-chip' + (targetTripId === null ? ' active' : '') + '" data-tt="none">No trip</span>';
  state.trips.forEach(function (t) {
    var dt = tripDupDate(t, state.trips);
    var lbl = esc(t.name) + (dt ? ' · ' + esc(dt) : '');
    html += '<span class="tt-chip' + (t.id === targetTripId ? ' active' : '') + '" data-tt="' + t.id + '">' + lbl + '</span>';
  });
  wrap.innerHTML = html;
}

// Reflect the selection set onto the currently rendered rows (checkmark + highlight).
function renderRowMarks() {
  document.querySelectorAll('.detail-row').forEach(function (r) {
    var p = state.detailPins && state.detailPins[parseInt(r.dataset.idx, 10)];
    r.classList.toggle('selected', !!(p && tripSel.has(p.id)));
  });
}

function toggleRowSelect(idx) {
  var p = state.detailPins && state.detailPins[idx];
  if (!p) return;
  var adding = !tripSel.has(p.id);
  if (adding) tripSel.add(p.id); else tripSel.delete(p.id);
  if (!inSelectMode()) targetTripId = undefined;  // deselected the last one -> leave selection mode clean
  renderRowMarks();
  renderTripBar();
  // Focus the big player on the clip you just picked, so "play what I'm selecting" holds. selectDetail
  // won't scroll the list while selecting (see its guard), so the list stays put under your finger.
  if (adding) selectDetail(idx);
}

// Which clip the big player advances to when the current one ends. Normally the next clip in the
// list (wrapping). While selecting, it stays within the selected set — looping a lone selection —
// so tagging never drags playback, and the auto-scroll, through unselected clips.
function nextPlayIndex(from) {
  var pins = state.detailPins;
  if (inSelectMode()) {
    for (var n = 1; n <= pins.length; n++) {
      var cand = (from + n) % pins.length;
      if (tripSel.has(pins[cand].id)) return cand;
    }
    return from;  // nothing else selected -> keep looping the current clip
  }
  return (from + 1) % pins.length;
}

function exitSelect() {
  tripSel.clear();
  targetTripId = undefined;
  renderRowMarks();
  renderTripBar();
}

function onTargetChipClick(e) {
  var chip = e.target.closest('.tt-chip');
  if (!chip) return;
  targetTripId = chip.dataset.tt === 'none' ? null : parseInt(chip.dataset.tt, 10);
  // Toggle 'active' in place rather than re-rendering the chips: rebuilding innerHTML would detach
  // the clicked node mid-event, and the overlay's outside-click-to-close guard (which reads the now
  // orphaned e.target) would then mistake it for a click outside the sheet and close the player.
  document.querySelectorAll('#dtb-targets .tt-chip').forEach(function (c) { c.classList.toggle('active', c === chip); });
  document.getElementById('pin-trip-apply').disabled = targetTripId === undefined;
}

function onTripApply() {
  if (!inSelectMode() || targetTripId === undefined) return;
  var tripId = targetTripId;
  var pins = state.detailPins.filter(function (p) { return tripSel.has(p.id); });
  var ids = pins.map(function (p) { return p.id; });
  var moves = pins.map(function (p) { return { from: p.trip_id, to: tripId }; });
  applyTripCountsDelta(moves);  // optimistic; reverted if the API rejects it
  pins.forEach(function (p) { p.trip_id = tripId; });
  refreshRowTrips();  // reflect the new tags in the rows right away
  var btn = document.getElementById('pin-trip-apply');
  btn.disabled = true;
  btn.textContent = 'Applying…';
  apiPost('/api/pin-trip', state.chatId, { pin_ids: ids, trip_id: tripId })
    .then(function (r) { if (!r.ok) throw new Error('failed ' + r.status); })
    .then(function () { btn.textContent = 'Apply'; exitSelect(); })
    .catch(function () {
      pins.forEach(function (p, i) { p.trip_id = moves[i].from; });  // revert pins + counts + row tags, keep the selection to retry
      applyTripCountsDelta(moves.map(function (m) { return { from: m.to, to: m.from }; }));
      refreshRowTrips();
      btn.textContent = 'Apply';
      btn.disabled = false;
    });
}

function hapticBump() {
  try {
    if (window.Telegram && Telegram.WebApp && Telegram.WebApp.HapticFeedback) Telegram.WebApp.HapticFeedback.impactOccurred('medium');
  } catch (_) { /* no haptics outside Telegram */ }
}

function selectDetail(i) {
  if (!state.detailPins || !state.detailPins[i]) return;
  detailIdx = i;
  var p = state.detailPins[i];
  renderTripBar();  // in normal mode this reflects the newly-playing clip's trip
  var big = document.getElementById('detail-big');
  var old = big.querySelector('video');
  if (old) teardownVideo(old);
  big.innerHTML = '<video id="detail-bigvid" controls playsinline></video>';
  var v = document.getElementById('detail-bigvid');
  v.src = videoUrl(p.video_file_id);
  v.muted = false;  // the detail view plays with sound; map markers and thumbnails stay muted
  v.loop = loopMode;  // loop replays this clip; otherwise it advances on 'ended'
  v.addEventListener('ended', function () {
    // Auto-advance hands off to the next clip (wrapping after the oldest). In loop mode v.loop is
    // set, so 'ended' never fires here — but guard on the mode anyway in case it was just toggled.
    // nextPlayIndex keeps advancement inside the selected set while tagging.
    if (!loopMode && state.detailPins) selectDetail(nextPlayIndex(i));
  });
  var pr = v.play();
  if (pr && pr.catch) pr.catch(function () { v.muted = true; var r = v.play(); if (r && r.catch) r.catch(function () {}); });
  var rows = document.querySelectorAll('.detail-row');
  rows.forEach(function (r) {
    var active = parseInt(r.dataset.idx, 10) === i;
    r.classList.toggle('active', active);
    // Keep the playing clip visible in the sheet — but NOT while selecting, where auto-scrolling the
    // list would yank rows out from under the user as they tap to (de)select.
    if (active && !loopMode && !inSelectMode()) r.scrollIntoView({ block: 'center' });
  });
}

function closeDetail() {
  var overlay = document.getElementById('detail');
  overlay.classList.add('hidden');
  overlay.querySelectorAll('video').forEach(teardownVideo);
  document.getElementById('detail-big').innerHTML = '';
  document.getElementById('detail-list').innerHTML = '';
  document.getElementById('detail-count').textContent = '';
  if (detailIo) { detailIo.disconnect(); detailIo = null; }
  state.detailPins = null;
  loopMode = false;
  reverseMode = false;
  tripSel.clear();
  targetTripId = undefined;
  updateVideoPlayback();  // resume the map previews
}

// Wire the overlay's click handlers (DOM is static, so this runs once at startup).
export function initDetail() {
  document.getElementById('play-all').addEventListener('click', playAll);
  document.getElementById('mode-loop').addEventListener('click', toggleLoop);
  document.getElementById('mode-reverse').addEventListener('click', toggleReverse);
  document.getElementById('dtb-targets').addEventListener('click', onTargetChipClick);
  document.getElementById('pin-trip-apply').addEventListener('click', onTripApply);
  document.getElementById('pin-trip-cancel').addEventListener('click', exitSelect);
  document.getElementById('detail').addEventListener('click', function (e) {
    // keep open when tapping the video, or the sheet (which holds the list and the mode toggle)
    if (e.target.closest('#detail-big') || e.target.closest('#detail-sheet')) return;
    closeDetail();
  });

  // Row interaction: a tap plays the clip (or toggles its selection while in selection mode); a
  // long-press (held ~half a second, no drag) selects the clip for trip tagging.
  var list = document.getElementById('detail-list');
  list.addEventListener('pointerdown', function (e) {
    var row = e.target.closest('.detail-row');
    if (!row || !state.trips.length) return;  // no trips -> nothing to tag
    var idx = parseInt(row.dataset.idx, 10);
    lpFired = false;
    lpStart = { x: e.clientX, y: e.clientY };
    clearTimeout(lpTimer);
    lpTimer = setTimeout(function () { lpFired = true; hapticBump(); toggleRowSelect(idx); }, 500);
  });
  list.addEventListener('pointermove', function (e) {
    if (lpStart && Math.abs(e.clientX - lpStart.x) + Math.abs(e.clientY - lpStart.y) > 12) clearTimeout(lpTimer);
  });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (ev) {
    list.addEventListener(ev, function () { clearTimeout(lpTimer); });
  });
  list.addEventListener('click', function (e) {
    var row = e.target.closest('.detail-row');
    if (!row) return;
    var idx = parseInt(row.dataset.idx, 10);
    if (lpFired) { lpFired = false; return; }  // swallow the click a long-press may synthesize
    if (inSelectMode()) toggleRowSelect(idx);
    else selectDetail(idx);
  });
}
