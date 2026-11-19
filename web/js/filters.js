// Filter panel, pagination, and the server-side pin fetch.
import { state } from './state.js';
import { esc, tripDupDate } from './util.js';
import { getColor } from './color.js';
import { authHeaders } from './api.js';
import { PAGE_SIZE } from './config.js';
import { renderMarkers } from './markers.js';
import { pauseMarkerVideos, updateVideoPlayback } from './video.js';

let renderTimer = null;  // debounce handle for scheduleRender()
let usersById = {};      // user_id -> display name for active-filter banner labels
let chatsById = {};      // chat_id -> title for active-filter banner labels in My Atlas

function showAuthError() {
  document.getElementById('filter-count').textContent = '';
  if (document.getElementById('auth-error')) return;
  var d = document.createElement('div');
  d.id = 'auth-error';
  d.style.cssText = 'position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);z-index:2000;background:var(--surface,#fff);padding:16px 20px;border-radius:14px;box-shadow:0 4px 20px rgba(0,0,0,0.28);font-family:-apple-system,sans-serif;font-size:14px;color:var(--text,#444);max-width:80%;text-align:center;';
  d.textContent = state.isAtlas
    ? 'Open My Atlas from your Telegram bot chat to view it.'
    : 'Open this map from inside your Telegram group to view it.';
  document.body.appendChild(d);
}

export function updatePagination() {
  var el = document.getElementById('pagination');
  var totalPages = Math.ceil(state.totalPins / PAGE_SIZE);
  if (totalPages <= 1) {
    el.style.display = 'none';
    return;
  }
  el.style.display = 'block';
  document.getElementById('page-info').textContent = 'Page ' + state.currentPage + ' of ' + totalPages;
  document.getElementById('page-prev').disabled = state.currentPage <= 1;
  document.getElementById('page-next').disabled = state.currentPage >= totalPages;
}

export function getServerFilterParams() {
  var params = {};
  if (state.isAtlas) {
    var totalChatCbs = document.querySelectorAll('.filter-chat-cb').length;
    var checkedChatCbs = document.querySelectorAll('.filter-chat-cb:checked');
    if (totalChatCbs > 0 && checkedChatCbs.length !== totalChatCbs) {
      if (checkedChatCbs.length === 0) {
        params.chat_ids = '0';
      } else {
        params.chat_ids = Array.from(checkedChatCbs).map(function(cb) { return cb.value; }).join(',');
      }
    }
  } else {
    var totalCbs = document.querySelectorAll('.filter-user-cb').length;
    var checkedCbs = document.querySelectorAll('.filter-user-cb:checked');
    if (totalCbs > 0 && checkedCbs.length !== totalCbs) {
      if (checkedCbs.length === 0) {
        params.user_ids = '-1';
      } else {
        params.user_ids = Array.from(checkedCbs).map(function(cb) { return cb.value; }).join(',');
      }
    }
  }
  var dateFrom = document.getElementById('date-from').value;
  var dateTo = document.getElementById('date-to').value;
  if (dateFrom) params.date_from = dateFrom;
  if (dateTo) params.date_to = dateTo;
  var locText = document.getElementById('filter-location').value.trim();
  if (locText) params.q = locText;
  if (!state.isAtlas && state.selectedTripId !== null) params.trip_id = state.selectedTripId;
  return Object.keys(params).length ? params : null;
}

export function loadPins(page, filterParams) {
  var offset = (page - 1) * PAGE_SIZE;
  var url = state.isAtlas
    ? '/api/pins?mode=atlas&limit=' + PAGE_SIZE + '&offset=' + offset
    : '/api/pins?chat_id=' + encodeURIComponent(state.chatId) + '&limit=' + PAGE_SIZE + '&offset=' + offset;
  if (filterParams) {
    if (filterParams.chat_ids) url += '&chat_ids=' + encodeURIComponent(filterParams.chat_ids);
    if (filterParams.user_ids) url += '&user_ids=' + encodeURIComponent(filterParams.user_ids);
    if (filterParams.date_from) url += '&date_from=' + encodeURIComponent(filterParams.date_from);
    if (filterParams.date_to) url += '&date_to=' + encodeURIComponent(filterParams.date_to);
    if (filterParams.q) url += '&q=' + encodeURIComponent(filterParams.q);
    if (filterParams.trip_id) url += '&trip_id=' + encodeURIComponent(filterParams.trip_id);
  }
  document.getElementById('filter-count').innerHTML = '<span class="spinner"></span> Loading...';
  fetch(url, { headers: authHeaders() })
    .then(function(r) {
      if (r.status === 401 || r.status === 403) { showAuthError(); throw new Error('unauthorized'); }
      if (!r.ok) throw new Error('Failed ' + r.status);
      return r.json();
    })
    .then(function(data) {
      state.allPins = Array.isArray(data) ? data : data.pins;
      state.totalPins = Array.isArray(data) ? data.length : data.total;
      state.currentFilterParams = filterParams || null;
      renderMarkers();
    })
    .catch(function(e) { console.error('Map error:', e); });
}

function activeFilterList() {
  var list = [];
  if (!state.isAtlas && state.selectedTripId !== null) list.push('trip');
  if (state.isAtlas) {
    var totalChatCbs = document.querySelectorAll('.filter-chat-cb').length;
    var checkedChatCbs = document.querySelectorAll('.filter-chat-cb:checked');
    if (totalChatCbs > 0 && checkedChatCbs.length !== totalChatCbs) list.push('chats');
  } else {
    var totalCbs = document.querySelectorAll('.filter-user-cb').length;
    var checkedCbs = document.querySelectorAll('.filter-user-cb:checked');
    if (totalCbs > 0 && checkedCbs.length !== totalCbs) list.push('users');
  }
  if (document.getElementById('filter-location').value.trim()) list.push('location');
  if (document.getElementById('date-from').value || document.getElementById('date-to').value) list.push('date');
  return list;
}

export function openFilters() {
  var overlay = document.getElementById('filter-overlay');
  var btn = document.getElementById('filter-toggle');
  if (!overlay) return;
  pauseMarkerVideos();
  overlay.classList.remove('hidden');
  if (btn) {
    btn.classList.add('active');
    btn.setAttribute('aria-expanded', 'true');
  }
}

export function closeFilters() {
  var overlay = document.getElementById('filter-overlay');
  var btn = document.getElementById('filter-toggle');
  if (!overlay || overlay.classList.contains('hidden')) return;
  overlay.classList.add('hidden');
  if (btn) {
    btn.classList.remove('active');
    btn.setAttribute('aria-expanded', 'false');
  }
  updateVideoPlayback();
}

export function resetAllFilters() {
  if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; }
  state.selectedTripId = null;
  document.querySelectorAll('.filter-user-cb').forEach(function (cb) { cb.checked = true; });
  document.querySelectorAll('.filter-chat-cb').forEach(function (cb) { cb.checked = true; });
  document.getElementById('filter-location').value = '';
  document.getElementById('date-from').value = '';
  document.getElementById('date-to').value = '';
  renderTripChips();
  updateFilterIndicators();
  state.currentPage = 1;
  state.skipBoundsFit = false;
  loadPins(1, null);
}

function updateFilterIndicators() {
  var active = activeFilterList();
  var hasAny = active.length > 0;

  var dot = document.getElementById('filter-active-dot');
  if (dot) dot.classList.toggle('hidden', !hasAny);

  var resetBtn = document.getElementById('filter-reset-all');
  if (resetBtn) {
    resetBtn.disabled = !hasAny;
    resetBtn.classList.toggle('hidden', !hasAny);
  }

  var locClear = document.getElementById('location-clear');
  if (locClear) locClear.classList.toggle('hidden', !document.getElementById('filter-location').value.trim());

  var dateClear = document.getElementById('date-clear');
  if (dateClear) dateClear.classList.toggle('hidden', !(document.getElementById('date-from').value || document.getElementById('date-to').value));

  var banner = document.getElementById('trip-banner');
  if (!banner) return;
  var iconEl = document.getElementById('trip-banner-icon');
  var nameEl = document.getElementById('trip-banner-name');
  var countEl = document.getElementById('trip-banner-count');
  var reelBtn = document.getElementById('trip-banner-reel');
  var clearBtn = document.getElementById('trip-banner-clear');

  if (state.selectedTripId !== null) {
    var t = state.trips.find(function (x) { return x.id === state.selectedTripId; });
    if (t) {
      if (iconEl) iconEl.textContent = '🍻';
      nameEl.textContent = t.name;
      countEl.textContent = String(t.pin_count);
      countEl.classList.remove('hidden');
      if (reelBtn) reelBtn.classList.toggle('hidden', t.pin_count <= 0);
      if (clearBtn) clearBtn.setAttribute('aria-label', 'Clear trip filter');
      banner.classList.remove('hidden');
      return;
    }
  }

  if (reelBtn) reelBtn.classList.add('hidden');
  countEl.classList.add('hidden');

  var atlasBadge = document.getElementById('atlas-badge');
  if (!hasAny) {
    banner.classList.add('hidden');
    if (atlasBadge) atlasBadge.classList.toggle('hidden', !state.isAtlas);
    return;
  }
  if (atlasBadge) atlasBadge.classList.add('hidden');

  // Show a compact 1-tap dismiss pill on the map for any non-trip filter (location, friend, dates).
  if (active.length === 1) {
    var only = active[0];
    if (only === 'location') {
      if (iconEl) iconEl.textContent = '📍';
      nameEl.textContent = document.getElementById('filter-location').value.trim();
    } else if (only === 'chats') {
      var checkedChats = Array.from(document.querySelectorAll('.filter-chat-cb:checked'));
      if (iconEl) iconEl.textContent = '💬';
      if (checkedChats.length === 1) {
        nameEl.textContent = chatsById[checkedChats[0].value] || '1 chat';
      } else {
        nameEl.textContent = checkedChats.length + ' chats';
      }
    } else if (only === 'users') {
      var checked = Array.from(document.querySelectorAll('.filter-user-cb:checked'));
      if (iconEl) iconEl.textContent = '👤';
      if (checked.length === 1) {
        nameEl.textContent = usersById[checked[0].value] || '1 friend';
      } else {
        nameEl.textContent = checked.length + ' friends';
      }
    } else {
      if (iconEl) iconEl.textContent = '📅';
      var df = document.getElementById('date-from').value;
      var dt = document.getElementById('date-to').value;
      nameEl.textContent = (df && df === dt) ? df : 'Date filter';
    }
  } else {
    if (iconEl) iconEl.textContent = '🔎';
    nameEl.textContent = active.length + ' filters';
  }
  if (clearBtn) clearBtn.setAttribute('aria-label', 'Reset all filters');
  banner.classList.remove('hidden');
}

export function applyLocationFilter(query) {
  var input = document.getElementById('filter-location');
  if (!input) return;
  state.selectedTripId = null;
  renderTripChips();
  document.querySelectorAll('.filter-user-cb').forEach(function (cb) { cb.checked = true; });
  document.querySelectorAll('.filter-chat-cb').forEach(function (cb) { cb.checked = true; });
  document.getElementById('date-from').value = '';
  document.getElementById('date-to').value = '';
  input.value = query || '';
  updateFilterIndicators();
  state.currentPage = 1;
  state.skipBoundsFit = false;
  loadPins(1, getServerFilterParams());
}

export function applyChatFilter(chatId) {
  var cidStr = String(chatId);
  var found = false;
  document.querySelectorAll('.filter-chat-cb').forEach(function (cb) {
    var match = cb.value === cidStr;
    cb.checked = match;
    if (match) found = true;
  });
  if (!found) return;
  document.getElementById('filter-location').value = '';
  document.getElementById('date-from').value = '';
  document.getElementById('date-to').value = '';
  updateFilterIndicators();
  state.currentPage = 1;
  state.skipBoundsFit = false;
  loadPins(1, getServerFilterParams());
}

export function applyUserFilter(userId) {
  var uidStr = String(userId);
  var found = false;
  document.querySelectorAll('.filter-user-cb').forEach(function (cb) {
    var match = cb.value === uidStr;
    cb.checked = match;
    if (match) found = true;
  });
  if (!found) return;
  state.selectedTripId = null;
  renderTripChips();
  document.getElementById('filter-location').value = '';
  document.getElementById('date-from').value = '';
  document.getElementById('date-to').value = '';
  updateFilterIndicators();
  state.currentPage = 1;
  state.skipBoundsFit = false;
  loadPins(1, getServerFilterParams());
}

function scheduleRender() {
  updateFilterIndicators();
  if (renderTimer) clearTimeout(renderTimer);
  renderTimer = setTimeout(function() {
    var serverFilters = getServerFilterParams();
    var serverChanged = JSON.stringify(serverFilters) !== JSON.stringify(state.currentFilterParams);
    if (serverChanged) {
      state.currentPage = 1;
      loadPins(1, serverFilters);
    } else {
      renderMarkers();
    }
  }, 500);
}

// Trip chips: single-select — tapping a chip filters the map to that trip (server-side), tapping
// it again clears the filter. The open trip (no closed_at) gets a live dot.
export function renderTripChips() {
  var wrap = document.getElementById('trip-filters');
  var section = document.getElementById('trip-section');
  if (!state.trips.length) {
    section.style.display = 'none';
    return;
  }
  section.style.display = '';
  var html = '';
  state.trips.forEach(function(t) {
    var cls = 'trip-chip' + (t.id === state.selectedTripId ? ' active' : '');
    var live = t.closed_at ? '' : '<span class="trip-live"></span>';
    var dt = tripDupDate(t, state.trips);  // short date when another trip shares this name
    var dateTag = dt ? ' <span class="trip-date">' + esc(dt) + '</span>' : '';
    html += '<span class="' + cls + '" data-trip-id="' + t.id + '">' + live + esc(t.name) + dateTag +
      ' <span class="trip-count">' + t.pin_count + '</span></span>';
  });
  wrap.innerHTML = html;
}

// Keep the filter chip counts honest after (bulk) retro-tagging, without refetching /api/pins-meta.
// `moves` is a list of {from, to} trip-id transitions (null = "No trip", which has no chip).
export function applyTripCountsDelta(moves) {
  var counts = {};
  moves.forEach(function(m) {
    if (m.from === m.to) return;
    if (m.from != null) counts[m.from] = (counts[m.from] || 0) - 1;
    if (m.to != null) counts[m.to] = (counts[m.to] || 0) + 1;
  });
  state.trips.forEach(function(t) {
    if (counts[t.id]) t.pin_count = Math.max(0, t.pin_count + counts[t.id]);
  });
  renderTripChips();
  updateFilterIndicators();
}

export function populateFilters(meta) {
  if (meta.current_user_id != null) {
    state.currentUserId = meta.current_user_id;
  } else if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initDataUnsafe && window.Telegram.WebApp.initDataUnsafe.user) {
    state.currentUserId = window.Telegram.WebApp.initDataUnsafe.user.id;
  }
  if (meta.entitlements) state.entitlements = meta.entitlements;
  if (meta.pricing) state.pricing = meta.pricing;
  state.trips = meta.trips || [];
  if (state.initialTripId != null && state.trips.some(function (t) { return t.id === state.initialTripId; })) {
    state.selectedTripId = state.initialTripId;
  }
  renderTripChips();
  document.getElementById('trip-filters').addEventListener('click', function(e) {
    var chip = e.target.closest('.trip-chip');
    if (!chip) return;
    var id = parseInt(chip.dataset.tripId, 10);
    state.selectedTripId = state.selectedTripId === id ? null : id;
    renderTripChips();
    scheduleRender();
  });

  var userSec = document.getElementById('user-section');
  var chatSec = document.getElementById('chat-section');
  if (state.isAtlas) {
    if (userSec) userSec.style.display = 'none';
    state.atlasChats = meta.chats || [];
    chatsById = {};
    if (chatSec) chatSec.style.display = state.atlasChats.length ? '' : 'none';
    var chHtml = '';
    state.atlasChats.forEach(function(ch) {
      chatsById[String(ch.chat_id)] = ch.title;
      var icon = ch.is_solo ? '👤' : '👥';
      chHtml += '<label class="filter-user"><input type="checkbox" class="filter-chat-cb" value="' + ch.chat_id + '" checked>' +
        '<span class="emoji-dot">' + icon + '</span>' +
        '<span class="filter-user-name">' + esc(ch.title) + '</span>' +
        '<span class="stamp-cnt">' + ch.pin_count + '</span></label>';
    });
    var chatWrap = document.getElementById('chat-filters');
    if (chatWrap) {
      chatWrap.innerHTML = chHtml;
      chatWrap.addEventListener('change', scheduleRender);
    }
    var chatAll = document.querySelector('.filter-chat-all');
    if (chatAll) chatAll.addEventListener('click', function() {
      document.querySelectorAll('.filter-chat-cb').forEach(function(cb) { cb.checked = true; });
      scheduleRender();
    });
    var chatNone = document.querySelector('.filter-chat-none');
    if (chatNone) chatNone.addEventListener('click', function() {
      document.querySelectorAll('.filter-chat-cb').forEach(function(cb) { cb.checked = false; });
      scheduleRender();
    });
  } else {
    if (chatSec) chatSec.style.display = 'none';
    if (userSec) userSec.style.display = '';
    var sorted = (meta.users || []).sort(function(a, b) { return a.user_name.localeCompare(b.user_name); });
    usersById = {};
    var html = '';
    sorted.forEach(function(u) {
      usersById[String(u.user_id)] = u.user_name;
      var dot = u.pin_emoji ? '<span class="emoji-dot">' + esc(u.pin_emoji) + '</span>' : '<span class="color-dot" style="background:' + esc(getColor(u.user_id, u.pin_color)) + '"></span>';
      html += '<label class="filter-user"><input type="checkbox" class="filter-user-cb" value="' + u.user_id + '" checked>' +
        dot +
        '<span class="filter-user-name">' + esc(u.user_name) + '</span></label>';
    });
    document.getElementById('user-filters').innerHTML = html;
    document.getElementById('user-filters').addEventListener('change', scheduleRender);

    document.querySelector('.filter-select-all').addEventListener('click', function() {
      document.querySelectorAll('.filter-user-cb').forEach(function(cb) { cb.checked = true; });
      scheduleRender();
    });
    document.querySelector('.filter-select-none').addEventListener('click', function() {
      document.querySelectorAll('.filter-user-cb').forEach(function(cb) { cb.checked = false; });
      scheduleRender();
    });
  }

  document.getElementById('date-from').value = state.initialDate || '';
  document.getElementById('date-to').value = state.initialDate || '';
  var minDate = meta.min_date ? meta.min_date.split('T')[0] : '';
  var maxDate = meta.max_date ? meta.max_date.split('T')[0] : '';
  document.getElementById('date-from').min = minDate;
  document.getElementById('date-from').max = maxDate;
  document.getElementById('date-to').min = minDate;
  document.getElementById('date-to').max = maxDate;
  document.getElementById('date-from').addEventListener('change', scheduleRender);
  document.getElementById('date-to').addEventListener('change', scheduleRender);
  document.getElementById('date-clear').addEventListener('click', function() {
    document.getElementById('date-from').value = '';
    document.getElementById('date-to').value = '';
    scheduleRender();
  });

  document.getElementById('filter-location').addEventListener('input', scheduleRender);
  document.getElementById('location-clear').addEventListener('click', function() {
    document.getElementById('filter-location').value = '';
    scheduleRender();
  });

  document.getElementById('filter-toggle').addEventListener('click', function() {
    var overlay = document.getElementById('filter-overlay');
    if (overlay && overlay.classList.contains('hidden')) openFilters();
    else closeFilters();
  });

  var closeBtn = document.getElementById('filter-close');
  if (closeBtn) closeBtn.addEventListener('click', closeFilters);

  var doneBtn = document.getElementById('filter-done');
  if (doneBtn) doneBtn.addEventListener('click', closeFilters);

  var resetAllBtn = document.getElementById('filter-reset-all');
  if (resetAllBtn) resetAllBtn.addEventListener('click', resetAllFilters);

  var filterOverlay = document.getElementById('filter-overlay');
  if (filterOverlay) {
    filterOverlay.addEventListener('click', function (e) {
      if (e.target.closest('#filter-panel')) return;
      closeFilters();
    });
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeFilters();
  });

  var tripBannerClear = document.getElementById('trip-banner-clear');
  if (tripBannerClear) {
    tripBannerClear.addEventListener('click', function() {
      if (state.selectedTripId !== null) {
        state.selectedTripId = null;
        renderTripChips();
        updateFilterIndicators();
        state.currentPage = 1;
        state.skipBoundsFit = false;
        loadPins(1, getServerFilterParams());
      } else {
        resetAllFilters();
      }
    });
  }
  updateFilterIndicators();
}

// Wire the pagination buttons (DOM is static, so this runs once at startup).
export function initFilters() {
  document.getElementById('page-prev').addEventListener('click', function() {
    if (state.currentPage > 1) {
      state.currentPage--;
      loadPins(state.currentPage, state.currentFilterParams);
    }
  });

  document.getElementById('page-next').addEventListener('click', function() {
    var totalPages = Math.ceil(state.totalPins / PAGE_SIZE);
    if (state.currentPage < totalPages) {
      state.currentPage++;
      loadPins(state.currentPage, state.currentFilterParams);
    }
  });
}
