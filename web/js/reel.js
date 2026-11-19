// Trip Recap Reel modal + Telegram Stars checkout (Free watermarked preview vs HD unwatermarked).
import { state } from './state.js';
import { apiPost, authHeaders, reelUrl } from './api.js';
import { pauseMarkerVideos, updateVideoPlayback, teardownVideo } from './video.js';

let activeTripId = null;
let busy = false;

export function isTripUnlocked(tripId) {
  var ent = state.entitlements || {};
  if (ent.has_group_pass) return true;
  return Array.isArray(ent.unlocked_trip_ids) && ent.unlocked_trip_ids.indexOf(tripId) !== -1;
}

function getTrip(tripId) {
  return (state.trips || []).find(function (t) { return t.id === tripId; }) || null;
}

function hapticSuccess() {
  try {
    if (window.Telegram && Telegram.WebApp && Telegram.WebApp.HapticFeedback) {
      Telegram.WebApp.HapticFeedback.notificationOccurred('success');
    }
  } catch (_) { /* outside Telegram */ }
}

function setStatus(text, isError) {
  var el = document.getElementById('reel-status');
  if (!el) return;
  el.textContent = text || '';
  el.classList.toggle('hidden', !text);
  el.classList.toggle('error', !!isError);
}

function loadReelVideo(tripId, quality) {
  var stage = document.getElementById('reel-stage');
  var old = stage.querySelector('video');
  if (old) teardownVideo(old);
  stage.innerHTML =
    '<div id="reel-loading"><span class="spinner"></span><span>Stitching ' +
    (quality === 'hd' ? 'HD' : 'preview') + ' reel…</span></div>' +
    '<video id="reel-video" controls playsinline loop></video>';

  var v = document.getElementById('reel-video');
  var loader = document.getElementById('reel-loading');
  v.src = reelUrl(state.chatId, tripId, quality);
  v.addEventListener('loadeddata', function () {
    if (loader) loader.classList.add('hidden');
    v.classList.add('ready');
  });
  v.addEventListener('error', function () {
    if (loader) loader.innerHTML = '<span>Could not render reel. Try again.</span>';
  });
  var pr = v.play();
  if (pr && pr.catch) pr.catch(function () { v.muted = true; var r = v.play(); if (r && r.catch) r.catch(function () {}); });
}

function renderReelControls(tripId) {
  var trip = getTrip(tripId);
  var unlocked = isTripUnlocked(tripId);
  var pricing = state.pricing || { reel_stars: 100, group_pass_stars: 250, group_pass_days: 30 };

  document.getElementById('reel-title').textContent = trip ? trip.name : 'Trip Recap Reel';
  var badge = document.getElementById('reel-badge');
  badge.textContent = unlocked ? '✨ HD · No watermark' : 'Preview · Watermarked';
  badge.classList.toggle('hd', unlocked);

  var lockedBox = document.getElementById('reel-locked-actions');
  var unlockedBox = document.getElementById('reel-unlocked-actions');
  lockedBox.classList.toggle('hidden', unlocked);
  unlockedBox.classList.toggle('hidden', !unlocked);

  document.getElementById('reel-unlock-btn').textContent = '⭐ Unlock HD · ' + pricing.reel_stars + ' Stars';
  document.getElementById('reel-pass-btn').textContent = '✨ Group Pass · ' + pricing.group_pass_stars + ' Stars (' + pricing.group_pass_days + 'd)';
  document.getElementById('reel-send-hd-btn').disabled = busy;
  document.getElementById('reel-send-preview-btn').disabled = busy;
  document.getElementById('reel-unlock-btn').disabled = busy;
  document.getElementById('reel-pass-btn').disabled = busy;
}

function refreshEntitlements(expectTripId, attemptsLeft) {
  return fetch('/api/entitlements?chat_id=' + encodeURIComponent(state.chatId), { headers: authHeaders() })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) {
      if (data && data.entitlements) state.entitlements = data.entitlements;
      if (data && data.pricing) state.pricing = data.pricing;
      if (isTripUnlocked(expectTripId) || attemptsLeft <= 0) return isTripUnlocked(expectTripId);
      return new Promise(function (resolve) {
        setTimeout(function () { resolve(refreshEntitlements(expectTripId, attemptsLeft - 1)); }, 900);
      });
    })
    .catch(function () { return false; });
}

function startStarsCheckout(product) {
  if (busy || activeTripId === null) return;
  busy = true;
  setStatus('Opening Telegram Stars checkout…', false);
  renderReelControls(activeTripId);

  var body = product === 'trip_reel' ? { product: 'trip_reel', trip_id: activeTripId } : { product: 'group_pass' };
  apiPost('/api/reel-invoice', state.chatId, body)
    .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
    .then(function (res) {
      if (!res.ok) throw new Error((res.data && res.data.error) || 'invoice failed');
      if (res.data.already_unlocked) {
        if (res.data.entitlements) state.entitlements = res.data.entitlements;
        busy = false;
        hapticSuccess();
        setStatus('HD already unlocked for your group!', false);
        renderReelControls(activeTripId);
        loadReelVideo(activeTripId, 'hd');
        return;
      }
      var tg = window.Telegram && window.Telegram.WebApp;
      if (!tg || !tg.openInvoice) {
        busy = false;
        renderReelControls(activeTripId);
        setStatus('Open this map inside Telegram to pay with Stars.', true);
        return;
      }
      tg.openInvoice(res.data.invoice_url, function (status) {
        if (status === 'paid') {
          setStatus('Payment confirmed — unlocking HD reel…', false);
          refreshEntitlements(activeTripId, 6).then(function (unlocked) {
            busy = false;
            renderReelControls(activeTripId);
            if (unlocked) {
              hapticSuccess();
              setStatus('HD unlocked for everyone in the group!', false);
              loadReelVideo(activeTripId, 'hd');
            } else {
              setStatus('Payment received — reopen in a moment if HD is still syncing.', false);
            }
          });
        } else {
          busy = false;
          renderReelControls(activeTripId);
          setStatus(status === 'cancelled' ? '' : 'Checkout did not complete.', status !== 'cancelled');
        }
      });
    })
    .catch(function () {
      busy = false;
      renderReelControls(activeTripId);
      setStatus('Could not start Telegram Stars checkout. Please try again.', true);
    });
}

function sendReelToChat(quality) {
  if (busy || activeTripId === null) return;
  busy = true;
  setStatus('Sending reel to your Telegram group…', false);
  renderReelControls(activeTripId);

  apiPost('/api/send-trip-reel', state.chatId, { trip_id: activeTripId, quality: quality })
    .then(function (r) {
      if (!r.ok) throw new Error('send failed ' + r.status);
      return r.json();
    })
    .then(function () {
      busy = false;
      hapticSuccess();
      renderReelControls(activeTripId);
      setStatus('Sent to your group chat — save or forward it to Stories!', false);
    })
    .catch(function () {
      busy = false;
      renderReelControls(activeTripId);
      setStatus('Could not send reel to chat. Please try again.', true);
    });
}

export function openReel(tripId) {
  if (tripId == null) return;
  var overlay = document.getElementById('reel-overlay');
  if (!overlay) return;
  activeTripId = tripId;
  busy = false;
  var filterOverlay = document.getElementById('filter-overlay');
  if (filterOverlay) filterOverlay.classList.add('hidden');
  var filterToggle = document.getElementById('filter-toggle');
  if (filterToggle) { filterToggle.classList.remove('active'); filterToggle.setAttribute('aria-expanded', 'false'); }
  pauseMarkerVideos();
  setStatus('', false);
  renderReelControls(tripId);
  overlay.classList.remove('hidden');
  loadReelVideo(tripId, isTripUnlocked(tripId) ? 'hd' : 'preview');
}

export function closeReel() {
  var overlay = document.getElementById('reel-overlay');
  if (!overlay || overlay.classList.contains('hidden')) return;
  overlay.classList.add('hidden');
  var v = document.getElementById('reel-video');
  if (v) teardownVideo(v);
  document.getElementById('reel-stage').innerHTML = '';
  activeTripId = null;
  busy = false;
  setStatus('', false);
  updateVideoPlayback();
}

export function initReel() {
  var bannerBtn = document.getElementById('trip-banner-reel');
  if (bannerBtn) {
    bannerBtn.addEventListener('click', function () {
      if (state.selectedTripId !== null) openReel(state.selectedTripId);
    });
  }
  var closeBtn = document.getElementById('reel-close');
  if (closeBtn) closeBtn.addEventListener('click', closeReel);

  var overlay = document.getElementById('reel-overlay');
  if (overlay) {
    overlay.addEventListener('click', function (e) {
      if (e.target.closest('#reel-card')) return;
      closeReel();
    });
  }

  document.getElementById('reel-unlock-btn').addEventListener('click', function () { startStarsCheckout('trip_reel'); });
  document.getElementById('reel-pass-btn').addEventListener('click', function () { startStarsCheckout('group_pass'); });
  document.getElementById('reel-send-hd-btn').addEventListener('click', function () { sendReelToChat('hd'); });
  document.getElementById('reel-send-preview-btn').addEventListener('click', function () { sendReelToChat('preview'); });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeReel();
  });
}
