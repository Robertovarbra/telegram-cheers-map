// Entry point: parses the start params, branches to location-share mode, then boots the map.
import { state } from './state.js';
import { extractChatIdFromStartParam, extractTripIdFromStartParam, extractDateFromStartParam } from './util.js';
import { authHeaders } from './api.js';
import { startLocationShare } from './location-share.js';
import { populateFilters, loadPins, initFilters, getServerFilterParams } from './filters.js';
import { renderMarkers, renderTripRoute } from './markers.js';
import { updateVideoPlayback } from './video.js';
import { initDetail } from './detail.js';
import { initStats } from './stats.js';
import { initReel } from './reel.js';
import { initRelocate } from './relocate.js';

const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {
  tg.expand();
  tg.ready();
}

const TILE_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';

function detectSystemTheme() {
  if (tg && (tg.colorScheme === 'dark' || tg.colorScheme === 'light')) return tg.colorScheme;
  if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) return 'dark';
  return 'light';
}

function syncThemeToggleIcon(theme) {
  var btn = document.getElementById('theme-toggle');
  if (!btn) return;
  btn.setAttribute('aria-label', theme === 'dark' ? 'Switch to light map' : 'Switch to dark map');
  btn.title = theme === 'dark' ? 'Switch to light map' : 'Switch to dark map';
  btn.innerHTML = theme === 'dark'
    ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>';
}

function applyTheme(theme) {
  state.theme = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.dataset.theme = state.theme;
  syncThemeToggleIcon(state.theme);
  if (tg) {
    try {
      var bg = state.theme === 'dark' ? '#17191d' : '#f4f5f7';
      if (tg.setHeaderColor) tg.setHeaderColor(bg);
      if (tg.setBackgroundColor) tg.setBackgroundColor(bg);
    } catch (_) { /* older Telegram clients */ }
  }
  if (state.map && state.selectedTripId !== null) renderTripRoute();
}

applyTheme(detectSystemTheme());

const params = new URLSearchParams(window.location.search);

function getStartParam() {
  var sp = params.get('tgWebAppStartParam');
  if (!sp && window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initDataUnsafe) {
    sp = window.Telegram.WebApp.initDataUnsafe.start_param;
  }
  return sp || null;
}

var startParam = getStartParam();
var locShare = !!(startParam && startParam.indexOf('loc_') === 0);
var isAtlas = !locShare && (params.get('mode') === 'atlas' || startParam === 'atlas');
var chatId = params.get('chat_id');
if (!chatId && !isAtlas) {
  chatId = extractChatIdFromStartParam(locShare ? startParam.slice(4) : startParam);
}
if (!isAtlas && !chatId) {
  document.body.innerHTML = '<div style="padding:20px;text-align:center;color:#999">Missing chat ID</div>';
  throw new Error('chat_id required');
}

if (locShare) {
  startLocationShare(chatId);
  throw new Error('location share mode');  // halt: skip map init below (same pattern as the guard above)
}

state.isAtlas = isAtlas;
state.chatId = isAtlas ? null : chatId;
var tripIdParam = params.get('trip_id');
state.initialTripId = isAtlas
  ? null
  : ((tripIdParam && /^\d+$/.test(tripIdParam)) ? parseInt(tripIdParam, 10) : extractTripIdFromStartParam(startParam));
var dateParam = params.get('date');
state.initialDate = (dateParam && /^\d{4}-\d{2}-\d{2}$/.test(dateParam)) ? dateParam : extractDateFromStartParam(startParam);

if (isAtlas) {
  document.title = 'My Atlas — Cheers Map';
  var statsTitle = document.querySelector('#stats-header .stats-title');
  if (statsTitle) statsTitle.textContent = 'My Passport';
  var statsToggle = document.getElementById('stats-toggle');
  if (statsToggle) {
    statsToggle.title = 'My Passport';
    statsToggle.setAttribute('aria-label', 'My Passport');
  }
  var atlasBadge = document.getElementById('atlas-badge');
  if (atlasBadge) atlasBadge.classList.remove('hidden');
}

const map = L.map('map').setView([0, 0], 2);
state.map = map;
state.tileLayer = L.tileLayer(TILE_URL, {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  maxZoom: 19,
}).addTo(map);
applyTheme(state.theme);
// Route layer sits underneath markers so trip polylines never cover circular video bubbles.
state.routeGroup = L.layerGroup().addTo(map);
state.markerGroup = L.layerGroup().addTo(map);

if (tg && tg.onEvent) {
  tg.onEvent('themeChanged', function () { applyTheme(detectSystemTheme()); });
}
var themeBtn = document.getElementById('theme-toggle');
if (themeBtn) {
  themeBtn.addEventListener('click', function () {
    applyTheme(state.theme === 'dark' ? 'light' : 'dark');
  });
}

map.on('moveend', updateVideoPlayback);
// Re-cluster as the zoom changes (pixel distances are zoom-dependent). Skip the bounds-fit so we
// don't fight the user's zoom.
map.on('zoomend', function () { state.skipBoundsFit = true; renderMarkers(); });

initFilters();
initDetail();
initStats();
initReel();
initRelocate();

document.body.addEventListener('click', function (e) {
  var btn = e.target.closest('#view-all');
  if (!btn) return;
  var m = state.map, pins = state.allPins;
  if (m && pins.length > 0) {
    m.fitBounds(pins.map(function (p) { return [p.latitude, p.longitude]; }), { padding: [40, 40], maxZoom: 15 });
  }
});

var metaUrl = state.isAtlas ? '/api/pins-meta?mode=atlas' : ('/api/pins-meta?chat_id=' + encodeURIComponent(chatId));
fetch(metaUrl, { headers: authHeaders() })
  .then(function(r) {
    if (!r.ok) return { users: [], chats: [], min_date: null, max_date: null };
    return r.json();
  })
  .then(function(meta) {
    populateFilters(meta);
    loadPins(1, getServerFilterParams());
  })
  .catch(function(e) {
    console.error('Meta error:', e);
    populateFilters({ users: [], chats: [], min_date: null, max_date: null });
    loadPins(1);
  });
