// Map-preview playback (muted, capped loops, on-screen only).
import { state } from './state.js';
import { videoUrl } from './api.js';
import { MAX_CONCURRENT_VIDEOS } from './config.js';

function startMarkerVideo(v, fileId) {
  if (v.dataset.state === 'playing') return;  // already looping
  v.muted = true;  // native loop keeps it running seamlessly while on-screen
  if (!v.getAttribute('src')) {
    if (!v.dataset.bound) {
      v.dataset.bound = '1';
      v.addEventListener('loadeddata', function () { v.classList.add('ready'); });
    }
    v.src = videoUrl(fileId);
  }
  v.dataset.state = 'playing';
  var p = v.play();
  if (p && p.catch) p.catch(function () {});
}

// Stop a <video> and release its stream. Each step is a no-op if already idle.
export function teardownVideo(v) {
  try { v.pause(); } catch (e) {}
  v.removeAttribute('src');
  try { v.load(); } catch (e) {}
}

function stopMarkerVideo(v) {
  if (!v.getAttribute('src') && v.dataset.state !== 'playing') return;  // nothing loaded
  v.classList.remove('ready');
  v.dataset.state = '';
  teardownVideo(v);
}

// Stream only the markers currently on-screen (nearest the centre first), capped so we never open
// too many connections to the video proxy at once. Off-screen markers are unloaded.
export function updateVideoPlayback() {
  if (state.detailPins) return;  // the detail overlay owns playback while it's open
  if (!state.videoMarkers.length) return;
  var bounds = state.map.getBounds().pad(0.5);  // margin so markers near the edge don't flicker on/off
  var c = state.map.getCenter();
  var visible = [];
  state.videoMarkers.forEach(function (vm) {
    if (bounds.contains([vm.lat, vm.lng])) {
      var dlat = vm.lat - c.lat, dlng = vm.lng - c.lng;
      visible.push({ vm: vm, d: dlat * dlat + dlng * dlng });
    }
  });
  visible.sort(function (a, b) { return a.d - b.d; });
  var playSet = new Set(visible.slice(0, MAX_CONCURRENT_VIDEOS).map(function (x) { return x.vm; }));
  state.videoMarkers.forEach(function (vm) {
    if (!vm.video) return;
    if (playSet.has(vm)) startMarkerVideo(vm.video, vm.fileId);
    else stopMarkerVideo(vm.video);
  });
}

export function pauseMarkerVideos() {
  state.videoMarkers.forEach(function (vm) {
    if (vm.video) stopMarkerVideo(vm.video);
  });
}
