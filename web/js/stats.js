// Group Passport & Stats sheet: countries unlocked, top cities, distance, and friend leaderboard.
import { state } from './state.js';
import { esc, countryFlag, formatDistance, placeholderGlyph } from './util.js';
import { getColor } from './color.js';
import { authHeaders } from './api.js';
import { pauseMarkerVideos, updateVideoPlayback } from './video.js';
import { applyLocationFilter, applyUserFilter } from './filters.js';

let cachedStats = null;
let loadingStats = false;

export function invalidateStats() {
  cachedStats = null;
}

function renderStats(data) {
  var body = document.getElementById('stats-body');
  if (!data || !data.total_pins) {
    body.innerHTML =
      '<div class="stats-empty">' +
        '<div class="stats-empty-icon">🌍</div>' +
        '<div class="stats-empty-title">No cheers pinned yet</div>' +
        '<div class="stats-empty-sub">Send a circular video note in your group and share your location to start your passport.</div>' +
      '</div>';
    return;
  }

  var html = '';

  // Hero summary cards
  html += '<div class="stats-hero">' +
    '<div class="stat-card"><div class="stat-val">' + data.total_pins.toLocaleString() + '</div><div class="stat-lbl">Cheers</div></div>' +
    '<div class="stat-card"><div class="stat-val">' + data.total_countries.toLocaleString() + '</div><div class="stat-lbl">' + (data.total_countries === 1 ? 'Country' : 'Countries') + '</div></div>' +
    '<div class="stat-card"><div class="stat-val">' + esc(formatDistance(data.total_distance_km)) + '</div><div class="stat-lbl">Traveled</div></div>' +
  '</div>';

  var metaParts = [];
  if (data.total_cities) metaParts.push(data.total_cities + ' ' + (data.total_cities === 1 ? 'city' : 'cities'));
  if (data.total_trips) metaParts.push(data.total_trips + ' ' + (data.total_trips === 1 ? 'trip' : 'trips'));
  if (data.total_users) metaParts.push(data.total_users + ' ' + (data.total_users === 1 ? 'friend' : 'friends'));
  if (metaParts.length) {
    html += '<div class="stats-subline">' + esc(metaParts.join(' · ')) + '</div>';
  }

  // Passport country stamps
  if (data.countries && data.countries.length) {
    html += '<div class="stats-sec"><h4>Passport Stamps</h4><div class="stamp-grid">';
    data.countries.forEach(function (c) {
      var flag = countryFlag(c.country_code) || '📍';
      html += '<button type="button" class="stamp-pill" data-loc="' + esc(c.country) + '">' +
        '<span class="stamp-flag">' + flag + '</span>' +
        '<span class="stamp-name">' + esc(c.country) + '</span>' +
        '<span class="stamp-cnt">' + c.pin_count + '</span>' +
      '</button>';
    });
    html += '</div></div>';
  }

  // Top cities
  if (data.cities && data.cities.length) {
    html += '<div class="stats-sec"><h4>Top Cities</h4><div class="city-chips">';
    data.cities.slice(0, 12).forEach(function (c) {
      var flag = countryFlag(c.country_code);
      html += '<button type="button" class="city-chip" data-loc="' + esc(c.city) + '">' +
        (flag ? '<span class="city-flag">' + flag + '</span>' : '') +
        '<span>' + esc(c.city) + '</span>' +
        '<span class="city-cnt">' + c.pin_count + '</span>' +
      '</button>';
    });
    html += '</div></div>';
  }

  // Leaderboard
  if (data.leaderboard && data.leaderboard.length) {
    var maxPins = data.leaderboard[0].pin_count || 1;
    html += '<div class="stats-sec"><h4>Friends</h4><div class="lb-list">';
    data.leaderboard.forEach(function (u, idx) {
      var col = getColor(u.user_id, u.pin_color);
      var pct = Math.max(6, Math.round((u.pin_count / maxPins) * 100));
      var placeBits = [];
      if (u.country_count) placeBits.push(u.country_count + (u.country_count === 1 ? ' country' : ' countries'));
      if (u.city_count) placeBits.push(u.city_count + (u.city_count === 1 ? ' city' : ' cities'));
      if (u.distance_km > 0) placeBits.push(formatDistance(u.distance_km));
      var sub = placeBits.join(' · ') || 'No location metadata';
      html += '<button type="button" class="lb-row" data-uid="' + u.user_id + '">' +
        '<span class="lb-rank">' + (idx + 1) + '</span>' +
        '<span class="lb-avatar" style="background:' + esc(col) + '">' + placeholderGlyph(u) + '</span>' +
        '<span class="lb-main">' +
          '<span class="lb-top"><span class="lb-name">' + esc(u.user_name) + '</span><span class="lb-count">' + u.pin_count + '</span></span>' +
          '<span class="lb-bar-track"><span class="lb-bar-fill" style="width:' + pct + '%;background:' + esc(col) + '"></span></span>' +
          '<span class="lb-sub">' + esc(sub) + '</span>' +
        '</span>' +
      '</button>';
    });
    html += '</div></div>';
  }

  body.innerHTML = html;
}

export function openStats() {
  var overlay = document.getElementById('stats-overlay');
  if (!overlay) return;
  var filterOverlay = document.getElementById('filter-overlay');
  if (filterOverlay) filterOverlay.classList.add('hidden');
  var filterToggle = document.getElementById('filter-toggle');
  if (filterToggle) { filterToggle.classList.remove('active'); filterToggle.setAttribute('aria-expanded', 'false'); }
  pauseMarkerVideos();
  overlay.classList.remove('hidden');

  if (cachedStats) {
    renderStats(cachedStats);
    return;
  }
  if (loadingStats) return;
  loadingStats = true;
  document.getElementById('stats-body').innerHTML =
    '<div class="stats-loading"><span class="spinner"></span>Loading passport…</div>';

  fetch('/api/stats?chat_id=' + encodeURIComponent(state.chatId), { headers: authHeaders() })
    .then(function (r) {
      if (!r.ok) throw new Error('stats ' + r.status);
      return r.json();
    })
    .then(function (data) {
      loadingStats = false;
      cachedStats = data;
      renderStats(data);
    })
    .catch(function (e) {
      loadingStats = false;
      console.error('Stats error:', e);
      document.getElementById('stats-body').innerHTML =
        '<div class="stats-empty"><div class="stats-empty-sub">Could not load group stats.</div></div>';
    });
}

export function closeStats() {
  var overlay = document.getElementById('stats-overlay');
  if (!overlay || overlay.classList.contains('hidden')) return;
  overlay.classList.add('hidden');
  updateVideoPlayback();
}

export function initStats() {
  var toggle = document.getElementById('stats-toggle');
  if (toggle) toggle.addEventListener('click', openStats);

  var closeBtn = document.getElementById('stats-close');
  if (closeBtn) closeBtn.addEventListener('click', closeStats);

  var overlay = document.getElementById('stats-overlay');
  if (overlay) {
    overlay.addEventListener('click', function (e) {
      if (e.target.closest('#stats-sheet')) return;
      closeStats();
    });
  }

  var body = document.getElementById('stats-body');
  if (body) {
    body.addEventListener('click', function (e) {
      var locBtn = e.target.closest('[data-loc]');
      if (locBtn) {
        closeStats();
        applyLocationFilter(locBtn.dataset.loc);
        return;
      }
      var uidBtn = e.target.closest('[data-uid]');
      if (uidBtn) {
        closeStats();
        applyUserFilter(uidBtn.dataset.uid);
      }
    });
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeStats();
  });
}
