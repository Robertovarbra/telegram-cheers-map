// Filter panel, pagination, and the server-side pin fetch.
import { state } from './state.js';
import { esc } from './util.js';
import { getColor } from './color.js';
import { authHeaders } from './api.js';
import { PAGE_SIZE } from './config.js';
import { renderMarkers } from './markers.js';

let renderTimer = null;  // debounce handle for scheduleRender()

function showAuthError() {
  document.getElementById('filter-count').textContent = '';
  if (document.getElementById('auth-error')) return;
  var d = document.createElement('div');
  d.id = 'auth-error';
  d.style.cssText = 'position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);z-index:2000;background:#fff;padding:16px 20px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,0.25);font-family:-apple-system,sans-serif;font-size:14px;color:#444;max-width:80%;text-align:center;';
  d.textContent = 'Open this map from inside your Telegram group to view it.';
  document.body.appendChild(d);
}

export function updatePagination() {
  var el = document.getElementById('pagination');
  if (hasClientFilters()) {
    el.style.display = 'none';
    return;
  }
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

function getServerFilterParams() {
  var totalCbs = document.querySelectorAll('.filter-user-cb').length;
  var checkedCbs = document.querySelectorAll('.filter-user-cb:checked');
  var params = {};
  if (totalCbs > 0 && checkedCbs.length !== totalCbs) {
    if (checkedCbs.length === 0) {
      params.user_ids = '-1';
    } else {
      params.user_ids = Array.from(checkedCbs).map(function(cb) { return cb.value; }).join(',');
    }
  }
  var dateFrom = document.getElementById('date-from').value;
  var dateTo = document.getElementById('date-to').value;
  if (dateFrom) params.date_from = dateFrom;
  if (dateTo) params.date_to = dateTo;
  var locText = document.getElementById('filter-location').value.trim();
  if (locText) params.q = locText;
  return Object.keys(params).length ? params : null;
}

export function loadPins(page, filterParams) {
  var offset = (page - 1) * PAGE_SIZE;
  var url = '/api/pins?chat_id=' + encodeURIComponent(state.chatId) + '&limit=' + PAGE_SIZE + '&offset=' + offset;
  if (filterParams) {
    if (filterParams.user_ids) url += '&user_ids=' + encodeURIComponent(filterParams.user_ids);
    if (filterParams.date_from) url += '&date_from=' + encodeURIComponent(filterParams.date_from);
    if (filterParams.date_to) url += '&date_to=' + encodeURIComponent(filterParams.date_to);
    if (filterParams.q) url += '&q=' + encodeURIComponent(filterParams.q);
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

function hasClientFilters() {
  return document.getElementById('filter-bounds').checked;
}

function scheduleRender() {
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

function onMapMove() {
  state.skipBoundsFit = true;
  scheduleRender();
}

export function populateFilters(meta) {
  var sorted = meta.users.sort(function(a, b) { return a.user_name.localeCompare(b.user_name); });
  var html = '';
  sorted.forEach(function(u) {
    var dot = u.pin_emoji ? '<span class="emoji-dot">' + esc(u.pin_emoji) + '</span>' : '<span class="color-dot" style="background:' + esc(getColor(u.user_id, u.pin_color)) + '"></span>';
    html += '<div class="filter-user"><input type="checkbox" class="filter-user-cb" value="' + u.user_id + '" checked>' +
      dot +
      '<label>' + esc(u.user_name) + '</label></div>';
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

  document.getElementById('date-from').value = '';
  document.getElementById('date-to').value = '';
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

  document.getElementById('filter-bounds').addEventListener('change', function() {
    if (this.checked) {
      state.map.on('moveend', onMapMove);
    } else {
      state.map.off('moveend', onMapMove);
    }
    scheduleRender();
  });

  document.getElementById('filter-location').addEventListener('input', scheduleRender);
  document.getElementById('location-clear').addEventListener('click', function() {
    document.getElementById('filter-location').value = '';
    scheduleRender();
  });

  document.getElementById('filter-toggle').addEventListener('click', function() {
    var p = document.getElementById('filter-panel');
    p.classList.toggle('hidden');
    this.textContent = p.classList.contains('hidden') ? '🔽' : '🔼';
  });
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
