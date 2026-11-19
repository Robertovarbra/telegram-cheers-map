// Owner-only pin location editor: Uber-style center crosshair so users can fix indoor GPS drift
// by panning the map underneath the pin instead of fighting a tiny draggable marker on touchscreens.
import { state } from './state.js';
import { apiPost } from './api.js';
import { showToast } from './util.js';
import { renderMarkers } from './markers.js';
import { invalidateStats } from './stats.js';
import { pauseMarkerVideos, updateVideoPlayback } from './video.js';

let saving = false;

function updateCoordReadout() {
  var el = document.getElementById('relocate-coords');
  if (!el || !state.map) return;
  var c = state.map.getCenter();
  el.textContent = c.lat.toFixed(5) + ', ' + c.lng.toFixed(5);
}

export function startRelocate(pin) {
  if (!pin || !state.map) return;
  saving = false;
  state.relocatingPin = pin;
  document.body.classList.add('relocating');
  pauseMarkerVideos();

  var saveBtn = document.getElementById('relocate-save');
  var cancelBtn = document.getElementById('relocate-cancel');
  if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save location'; }
  if (cancelBtn) cancelBtn.disabled = false;

  var targetZoom = Math.max(state.map.getZoom(), 16);
  state.skipBoundsFit = true;
  state.map.setView([pin.latitude, pin.longitude], targetZoom, { animate: true });
  updateCoordReadout();
}

export function exitRelocate(resumePlayback) {
  saving = false;
  state.relocatingPin = null;
  document.body.classList.remove('relocating');
  if (resumePlayback) updateVideoPlayback();
}

function onSave() {
  if (saving || !state.relocatingPin || !state.map) return;
  var pin = state.relocatingPin;
  var targetChatId = pin.chat_id != null ? pin.chat_id : state.chatId;
  var center = state.map.getCenter();
  var saveBtn = document.getElementById('relocate-save');
  var cancelBtn = document.getElementById('relocate-cancel');
  saving = true;
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving…'; }
  if (cancelBtn) cancelBtn.disabled = true;

  apiPost('/api/pin-location', targetChatId, { pin_id: pin.id, lat: center.lat, lng: center.lng })
    .then(function (r) {
      if (!r.ok) throw new Error('relocate ' + r.status);
      return r.json();
    })
    .then(function (res) {
      var patch = function (p) {
        if (p.id !== pin.id) return;
        p.latitude = res.latitude;
        p.longitude = res.longitude;
        p.city = res.city;
        p.country = res.country;
        p.country_code = res.country_code;
      };
      patch(pin);
      state.allPins.forEach(patch);
      invalidateStats();
      exitRelocate(false);
      state.skipBoundsFit = true;
      renderMarkers();
      try {
        if (window.Telegram && Telegram.WebApp && Telegram.WebApp.HapticFeedback) {
          Telegram.WebApp.HapticFeedback.notificationOccurred('success');
        }
      } catch (_) { /* ignore outside Telegram */ }
      var place = [res.city, res.country].filter(Boolean).join(', ');
      showToast(place ? 'Moved to ' + place : 'Location updated!');
    })
    .catch(function () {
      saving = false;
      if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save location'; }
      if (cancelBtn) cancelBtn.disabled = false;
      showToast('Could not update location. Try again.');
    });
}

export function initRelocate() {
  var cancelBtn = document.getElementById('relocate-cancel');
  if (cancelBtn) cancelBtn.addEventListener('click', function () { exitRelocate(true); });

  var saveBtn = document.getElementById('relocate-save');
  if (saveBtn) saveBtn.addEventListener('click', onSave);

  if (state.map) {
    state.map.on('move', function () {
      if (state.relocatingPin) updateCoordReadout();
    });
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && state.relocatingPin && !saving) exitRelocate(true);
  });
}
