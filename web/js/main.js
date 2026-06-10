// Entry point: parses the start params, branches to location-share mode, then boots the map.
import { state } from './state.js';
import { extractChatIdFromStartParam } from './util.js';
import { authHeaders } from './api.js';
import { startLocationShare } from './location-share.js';
import { populateFilters, loadPins, initFilters } from './filters.js';
import { renderMarkers } from './markers.js';
import { updateVideoPlayback } from './video.js';
import { initDetail } from './detail.js';

if (window.Telegram && window.Telegram.WebApp) {
  window.Telegram.WebApp.expand();
  window.Telegram.WebApp.ready();
}

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
var chatId = params.get('chat_id');
if (!chatId) {
  chatId = extractChatIdFromStartParam(locShare ? startParam.slice(4) : startParam);
}
if (!chatId) {
  document.body.innerHTML = '<div style="padding:20px;text-align:center;color:#999">Missing chat ID</div>';
  throw new Error('chat_id required');
}

if (locShare) {
  startLocationShare(chatId);
  throw new Error('location share mode');  // halt: skip map init below (same pattern as the guard above)
}

state.chatId = chatId;

const map = L.map('map').setView([0, 0], 2);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; <a href="https://osm.org/copyright">OpenStreetMap</a>',
  maxZoom: 19,
}).addTo(map);
state.map = map;
state.markerGroup = L.layerGroup().addTo(map);

map.on('moveend', updateVideoPlayback);
// Re-cluster as the zoom changes (pixel distances are zoom-dependent). Skip the bounds-fit so we
// don't fight the user's zoom.
map.on('zoomend', function () { state.skipBoundsFit = true; renderMarkers(); });

initFilters();
initDetail();

fetch('/api/pins-meta?chat_id=' + encodeURIComponent(chatId), { headers: authHeaders() })
  .then(function(r) {
    if (!r.ok) return { users: [], min_date: null, max_date: null };
    return r.json();
  })
  .then(function(meta) {
    populateFilters(meta);
    loadPins(1);
  })
  .catch(function(e) {
    console.error('Meta error:', e);
    populateFilters({ users: [], min_date: null, max_date: null });
    loadPins(1);
  });
