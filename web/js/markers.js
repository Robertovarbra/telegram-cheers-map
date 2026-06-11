// Map marker rendering: client-side filtering, screen-pixel clustering, and the circular
// video-note markers (Snapchat-map style).
import { state } from './state.js';
import { esc, placeholderGlyph, byNewest } from './util.js';
import { getColor, averageColor } from './color.js';
import { CLUSTER_PX } from './config.js';
import { updateVideoPlayback } from './video.js';
import { updatePagination } from './filters.js';
import { openDetail } from './detail.js';

function getFilteredPins() {
  var selected = {};
  document.querySelectorAll('.filter-user-cb:checked').forEach(function(cb) { selected[cb.value] = true; });
  var dateFrom = document.getElementById('date-from').value;
  var dateTo = document.getElementById('date-to').value;
  var bounds = document.getElementById('filter-bounds').checked ? state.map.getBounds() : null;

  return state.allPins.filter(function(p) {
    if (!selected[p.user_id]) return false;
    if (dateFrom && p.created_at < dateFrom) return false;
    if (dateTo && p.created_at.split('T')[0] > dateTo) return false;
    if (bounds && !bounds.contains([p.latitude, p.longitude])) return false;
    return true;
  });
}

// Greedy screen-pixel clustering at a given zoom: a pin joins the first group whose running
// centroid is within CLUSTER_PX, else it starts a new group. Shared by renderMarkers (to draw)
// and zoomToSplit (to predict the zoom at which a cluster breaks apart).
function clusterAtZoom(pins, z) {
  var groups = [];
  pins.forEach(function(p) {
    var pt = state.map.project([p.latitude, p.longitude], z);
    var g = null;
    for (var i = 0; i < groups.length; i++) {
      var dx = groups[i].x - pt.x, dy = groups[i].y - pt.y;
      if (dx * dx + dy * dy <= CLUSTER_PX * CLUSTER_PX) { g = groups[i]; break; }
    }
    if (g) {
      g.pins.push(p);
      var n = g.pins.length;
      g.x = (g.x * (n - 1) + pt.x) / n;
      g.y = (g.y * (n - 1) + pt.y) / n;
    } else {
      groups.push({ x: pt.x, y: pt.y, pins: [p] });
    }
  });
  return groups;
}

// Tapping a cluster zooms in just enough to break it into ≥2 clusters (one unbundle step), centred
// on the cluster. Returns false when no further zoom would split it (coincident points or maxZoom),
// so the caller can open the detail player instead.
function zoomToSplit(lat, lng, pins) {
  var maxZoom = state.map.getMaxZoom();
  for (var nz = Math.floor(state.map.getZoom()) + 1; nz <= maxZoom; nz++) {
    if (clusterAtZoom(pins, nz).length > 1) {
      state.map.setView([lat, lng], nz);
      return true;
    }
  }
  return false;
}

export function renderMarkers() {
  state.markerGroup.clearLayers();
  state.videoMarkers = [];
  var filtered = getFilteredPins();
  if (filtered.length === 0) {
    document.getElementById('filter-count').textContent = '0 pins match filters';
    document.getElementById('pagination').style.display = 'none';
    return;
  }
  // Cluster by screen-pixel distance at the current zoom, so markers bundle when zoomed out and
  // spread apart as you zoom in (re-clustered on every zoomend). Each cluster shows its latest
  // video plus a count badge.
  var z = state.map.getZoom();
  var groups = clusterAtZoom(filtered, z);
  groups.forEach(function(g) {
    var ll = state.map.unproject(L.point(g.x, g.y), z);
    addVideoMarker(ll.lat, ll.lng, g.pins);
  });
  // Fit to all pins (not cluster centroids) so the initial view doesn't depend on how points
  // happen to cluster at the starting zoom.
  if (!state.skipBoundsFit) state.map.fitBounds(filtered.map(function(p) { return [p.latitude, p.longitude]; }), { padding: [40, 40], maxZoom: 15 });
  state.skipBoundsFit = false;
  document.getElementById('filter-count').textContent = filtered.length + ' of ' + state.totalPins + ' pins shown';
  updatePagination();
  updateVideoPlayback();
}

// Ring colour for a cluster: one segment per distinct user, sized by how many videos they posted
// here, so the border shows every contributor's colour. A single user is just a solid ring.
function ringBackground(pins) {
  var order = [], counts = {}, colors = {};
  pins.forEach(function(p) {
    if (!(p.user_id in counts)) { counts[p.user_id] = 0; colors[p.user_id] = getColor(p.user_id, p.pin_color); order.push(p.user_id); }
    counts[p.user_id]++;
  });
  if (order.length === 1) return colors[order[0]];
  var total = pins.length, pct = 0;
  var stops = order.map(function(uid) {
    var seg = counts[uid] / total * 100;
    var a = Math.round(pct * 10) / 10;
    pct += seg;
    var b = Math.round(pct * 10) / 10;
    return colors[uid] + ' ' + a + '% ' + b + '%';
  }).join(', ');
  return 'conic-gradient(' + stops + ')';
}

// One marker per cluster (a single pin is just a cluster of one). Shows the latest cheer's video;
// a count badge appears when the cluster holds more than one.
function addVideoMarker(lat, lng, pins) {
  var sorted = pins.slice().sort(byNewest);  // latest first
  var top = sorted[0];  // latest cheer in the cluster
  var c = getColor(top.user_id, top.pin_color);
  var first = esc((top.user_name || '').split(' ')[0] || '');
  // Show a name only for a single contributor; a name on a multi-user cluster would imply it's
  // just theirs. (Same user posting several videos here still counts as one contributor.)
  var multiUser = sorted.some(function(p) { return p.user_id !== top.user_id; });
  var label = multiUser ? '' : '<div class="marker-label">' + first + '</div>';
  var count = '';
  if (sorted.length > 1) {
    var avg = averageColor(sorted.map(function(p) { return getColor(p.user_id, p.pin_color); }));
    var badgeStyle = '';
    if (avg) {
      var fg = (0.299 * avg[0] + 0.587 * avg[1] + 0.114 * avg[2]) > 150 ? '#222' : '#fff';  // keep the number legible
      badgeStyle = ' style="background:rgb(' + avg[0] + ',' + avg[1] + ',' + avg[2] + ');color:' + fg + '"';
    }
    count = '<span class="vm-count"' + badgeStyle + '>' + sorted.length + '</span>';
  }
  var icon = L.divIcon({
    className: 'video-marker',
    html: '<div class="vm-ring" style="background:' + esc(ringBackground(sorted)) + '">' +
            '<div class="vm-inner">' +
              '<div class="vm-ph" style="background:' + esc(c) + '">' + placeholderGlyph(top) + '</div>' +
              '<video class="marker-video" muted loop playsinline preload="none"></video>' +
              '<span class="vm-speaker">🔊</span>' +
            '</div>' +
          '</div>' + count + label,
    iconSize: [56, 56],
    iconAnchor: [28, 28],
    popupAnchor: [0, -34],
  });
  var marker = L.marker([lat, lng], { icon: icon });
  // A cluster zooms in one level to unbundle; a single pin (or a cluster that can't split any
  // further — coincident points at max zoom) opens the detail player on the latest, then the rest.
  marker.on('click', function () {
    if (sorted.length > 1 && zoomToSplit(lat, lng, sorted)) return;
    openDetail(sorted, 0);
  });
  state.markerGroup.addLayer(marker);
  var el = marker.getElement();  // icon exists right after addLayer; cache the <video> for playback
  // Keep the cluster's pins so the "Play" button can expand each visible bubble into its cheers.
  state.videoMarkers.push({ video: el ? el.querySelector('video') : null, lat: lat, lng: lng, fileId: top.video_file_id, pins: sorted });
}
