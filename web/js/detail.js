// Detail overlay: tap a marker -> enlarged video (with sound) + the list of cheers at that spot.
import { state } from './state.js';
import { esc, relTime, placeholderGlyph } from './util.js';
import { getColor } from './color.js';
import { videoUrl } from './api.js';
import { pauseMarkerVideos, updateVideoPlayback, teardownVideo } from './video.js';
import { MAX_CONCURRENT_VIDEOS } from './config.js';

let detailIdx = 0;  // which row in the open detail list is selected

export function openDetail(pins, idx) {
  if (!pins || !pins.length) return;
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
  selectDetail(idx);
}

function selectDetail(i) {
  if (!state.detailPins || !state.detailPins[i]) return;
  detailIdx = i;
  var p = state.detailPins[i];
  var big = document.getElementById('detail-big');
  var old = big.querySelector('video');
  if (old) teardownVideo(old);
  big.innerHTML = '<video id="detail-bigvid" controls loop playsinline></video>';
  var v = document.getElementById('detail-bigvid');
  v.src = videoUrl(p.video_file_id);
  v.muted = false;  // the detail view plays with sound; map markers and thumbnails stay muted
  var pr = v.play();
  if (pr && pr.catch) pr.catch(function () { v.muted = true; var r = v.play(); if (r && r.catch) r.catch(function () {}); });
  document.querySelectorAll('.detail-row').forEach(function (r) {
    r.classList.toggle('active', parseInt(r.dataset.idx, 10) === i);
  });
}

function closeDetail() {
  var overlay = document.getElementById('detail');
  overlay.classList.add('hidden');
  overlay.querySelectorAll('video').forEach(teardownVideo);
  document.getElementById('detail-big').innerHTML = '';
  document.getElementById('detail-list').innerHTML = '';
  state.detailPins = null;
  updateVideoPlayback();  // resume the map previews
}

// Wire the overlay's click handlers (DOM is static, so this runs once at startup).
export function initDetail() {
  document.getElementById('detail').addEventListener('click', function (e) {
    if (e.target.closest('#detail-big') || e.target.closest('#detail-sheet')) return;  // keep open when tapping the video/list
    closeDetail();
  });
  document.getElementById('detail-list').addEventListener('click', function (e) {
    var row = e.target.closest('.detail-row');
    if (row) selectDetail(parseInt(row.dataset.idx, 10));
  });
}
