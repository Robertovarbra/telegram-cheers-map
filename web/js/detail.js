// Detail overlay: tap a marker -> enlarged video (with sound) + the list of cheers at that spot.
import { state } from './state.js';
import { esc, relTime, placeholderGlyph, byNewest } from './util.js';
import { getColor } from './color.js';
import { videoUrl, apiPost } from './api.js';
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
      '<div class="detail-rowtext"><span class="dr-name">' + esc(p.user_name) + '</span> <span class="dr-date">— ' + esc(relTime(p.created_at)) + '</span></div>' +
    '</div>';
  });
  list.innerHTML = lh;
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
  pauseMarkerVideos();
  state.detailPins = pins;
  renderDetailList();
  document.getElementById('detail').classList.remove('hidden');
  applyLoopMode();
  applyReverseMode();
  selectDetail(idx);
}

// Retro-tag control: reflects the selected pin's trip and reassigns it on change. The fallback
// for cheers the auto-tagging missed (someone who hadn't joined the trip yet). Hidden when the
// chat has no trips.
function syncTripSelect(pin) {
  var sel = document.getElementById('pin-trip-select');
  if (!state.trips.length) {
    sel.classList.add('hidden');
    return;
  }
  sel.classList.remove('hidden');
  var html = '<option value="">No trip</option>';
  state.trips.forEach(function (t) {
    html += '<option value="' + t.id + '"' + (t.id === pin.trip_id ? ' selected' : '') + '>' + esc(t.name) + '</option>';
  });
  sel.innerHTML = html;
}

function onTripSelectChange() {
  if (!state.detailPins || !state.detailPins[detailIdx]) return;
  var pin = state.detailPins[detailIdx];
  var tripId = this.value ? parseInt(this.value, 10) : null;
  var prev = pin.trip_id;
  pin.trip_id = tripId;  // optimistic; reverted if the API rejects it
  apiPost('/api/pin-trip', state.chatId, { pin_id: pin.id, trip_id: tripId })
    .then(function (r) { if (!r.ok) throw new Error('failed ' + r.status); })
    .catch(function () { pin.trip_id = prev; syncTripSelect(pin); });
}

function selectDetail(i) {
  if (!state.detailPins || !state.detailPins[i]) return;
  detailIdx = i;
  var p = state.detailPins[i];
  syncTripSelect(p);
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
    if (!loopMode && state.detailPins) selectDetail((i + 1) % state.detailPins.length);
  });
  var pr = v.play();
  if (pr && pr.catch) pr.catch(function () { v.muted = true; var r = v.play(); if (r && r.catch) r.catch(function () {}); });
  var rows = document.querySelectorAll('.detail-row');
  rows.forEach(function (r) {
    var active = parseInt(r.dataset.idx, 10) === i;
    r.classList.toggle('active', active);
    if (active && !loopMode) r.scrollIntoView({ block: 'center' });  // keep the playing clip visible in the sheet
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
  updateVideoPlayback();  // resume the map previews
}

// Wire the overlay's click handlers (DOM is static, so this runs once at startup).
export function initDetail() {
  document.getElementById('play-all').addEventListener('click', playAll);
  document.getElementById('mode-loop').addEventListener('click', toggleLoop);
  document.getElementById('pin-trip-select').addEventListener('change', onTripSelectChange);
  document.getElementById('mode-reverse').addEventListener('click', toggleReverse);
  document.getElementById('detail').addEventListener('click', function (e) {
    // keep open when tapping the video, or the sheet (which holds the list and the mode toggle)
    if (e.target.closest('#detail-big') || e.target.closest('#detail-sheet')) return;
    closeDetail();
  });
  document.getElementById('detail-list').addEventListener('click', function (e) {
    var row = e.target.closest('.detail-row');
    if (row) selectDetail(parseInt(row.dataset.idx, 10));
  });
}
