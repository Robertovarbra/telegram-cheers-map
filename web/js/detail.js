// Detail overlay: tap a marker -> enlarged video (with sound) + the list of cheers at that spot.
import { state } from './state.js';
import { esc, relTime, placeholderGlyph, byNewest } from './util.js';
import { getColor } from './color.js';
import { videoUrl } from './api.js';
import { pauseMarkerVideos, updateVideoPlayback, teardownVideo } from './video.js';
import { MAX_CONCURRENT_VIDEOS } from './config.js';

let detailIdx = 0;     // which row in the open detail list is selected
// How the big player behaves: 'sequence' auto-advances newest -> oldest (wrapping); 'loop' replays the
// current clip. Sequence is the default every time the player opens; the top control bar toggles it.
let playbackMode = 'sequence';

// Sync the toggle's highlight and the playing <video>'s loop flag to the current mode.
function applyPlaybackMode() {
  var seqBtn = document.getElementById('mode-sequence');
  var loopBtn = document.getElementById('mode-loop');
  if (seqBtn) seqBtn.classList.toggle('active', playbackMode === 'sequence');
  if (loopBtn) loopBtn.classList.toggle('active', playbackMode === 'loop');
  var v = document.getElementById('detail-bigvid');
  if (v) v.loop = (playbackMode === 'loop');  // loop=true means 'ended' never fires, so it just repeats
}

function setPlaybackMode(mode) {
  playbackMode = mode;
  applyPlaybackMode();
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

export function openDetail(pins, idx) {
  if (!pins || !pins.length) return;
  playbackMode = 'sequence';  // every open starts in sequence; the user can switch to loop from the bar
  pauseMarkerVideos();  // free the proxy for the detail videos
  state.detailPins = pins;
  var list = document.getElementById('detail-list');
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
  // Only stream the first few thumbnails (a spot can accumulate many cheers over time); the rest
  // keep their placeholder and still load the big player when tapped.
  var thumbs = list.querySelectorAll('video[data-file-id]');
  for (var ti = 0; ti < thumbs.length && ti < MAX_CONCURRENT_VIDEOS; ti++) {
    (function (v) {
      v.src = videoUrl(v.dataset.fileId);
      v.addEventListener('loadeddata', function () { v.classList.add('ready'); });
      var r = v.play(); if (r && r.catch) r.catch(function () {});
    })(thumbs[ti]);
  }
  document.getElementById('detail').classList.remove('hidden');
  applyPlaybackMode();  // reset the toggle highlight to the default before the first clip plays
  selectDetail(idx);
}

function selectDetail(i) {
  if (!state.detailPins || !state.detailPins[i]) return;
  detailIdx = i;
  var p = state.detailPins[i];
  var big = document.getElementById('detail-big');
  var old = big.querySelector('video');
  if (old) teardownVideo(old);
  big.innerHTML = '<video id="detail-bigvid" controls playsinline></video>';
  var v = document.getElementById('detail-bigvid');
  v.src = videoUrl(p.video_file_id);
  v.muted = false;  // the detail view plays with sound; map markers and thumbnails stay muted
  v.loop = (playbackMode === 'loop');  // loop replays this clip; sequence advances on 'ended'
  v.addEventListener('ended', function () {
    // Sequence hands off to the next clip (wrapping after the oldest). In loop mode v.loop is set,
    // so 'ended' never fires here — but guard on the mode anyway in case it was just toggled.
    if (playbackMode === 'sequence' && state.detailPins) selectDetail((i + 1) % state.detailPins.length);
  });
  var pr = v.play();
  if (pr && pr.catch) pr.catch(function () { v.muted = true; var r = v.play(); if (r && r.catch) r.catch(function () {}); });
  var rows = document.querySelectorAll('.detail-row');
  rows.forEach(function (r) {
    var active = parseInt(r.dataset.idx, 10) === i;
    r.classList.toggle('active', active);
    if (active && playbackMode === 'sequence') r.scrollIntoView({ block: 'nearest' });  // keep the playing clip visible in the sheet
  });
}

function closeDetail() {
  var overlay = document.getElementById('detail');
  overlay.classList.add('hidden');
  overlay.querySelectorAll('video').forEach(teardownVideo);
  document.getElementById('detail-big').innerHTML = '';
  document.getElementById('detail-list').innerHTML = '';
  state.detailPins = null;
  playbackMode = 'sequence';
  updateVideoPlayback();  // resume the map previews
}

// Wire the overlay's click handlers (DOM is static, so this runs once at startup).
export function initDetail() {
  document.getElementById('play-all').addEventListener('click', playAll);
  document.getElementById('mode-sequence').addEventListener('click', function () { setPlaybackMode('sequence'); });
  document.getElementById('mode-loop').addEventListener('click', function () { setPlaybackMode('loop'); });
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
