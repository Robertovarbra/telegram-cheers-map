// Detail overlay: tap a marker -> enlarged video (with sound) + the list of cheers at that spot.
import { state } from './state.js';
import { esc, relTime, placeholderGlyph, byNewest, tripDupDate, countryFlag, DEFAULT_REACTIONS, showToast } from './util.js';
import { getColor } from './color.js';
import { videoUrl, apiPost } from './api.js';
import { applyTripCountsDelta } from './filters.js';
import { renderMarkers } from './markers.js';
import { invalidateStats } from './stats.js';
import { startRelocate } from './relocate.js';
import { pauseMarkerVideos, updateVideoPlayback, teardownVideo } from './video.js';
import { MAX_CONCURRENT_VIDEOS } from './config.js';

let detailIdx = 0;     // which row in the open detail list is selected
let deletingPin = false;
let editingCaption = false;
let savingCaption = false;
let savingComment = false;
let sheetExpanded = false;
let ownerMenuOpen = false;
let activeTab = 'videos';  // 'videos' | 'replies'
let userMuted = false;
const RING_CIRC = 304.73;  // 2 * PI * 48.5 for the circular SVG video progress ring
// How the big player behaves: when loop is OFF (the default) it auto-advances newest -> oldest
// (wrapping); when ON it replays the current clip. Always starts OFF when the player opens; the
// loop button in the top bar toggles it.
let loopMode = false;
let reverseMode = false;
let detailIo = null;  // IntersectionObserver for lazy-loading thumbnails

// Sync the loop button's active state and the playing <video>'s loop flag to the current mode.
function applyLoopMode() {
  var loopBtn = document.getElementById('mode-loop');
  if (loopBtn) {
    loopBtn.classList.toggle('active', loopMode);
    loopBtn.setAttribute('aria-pressed', loopMode ? 'true' : 'false');
  }
  var v = document.getElementById('detail-bigvid');
  if (v) v.loop = loopMode;  // loop=true means 'ended' never fires, so it just repeats
}

function toggleLoop() {
  loopMode = !loopMode;
  applyLoopMode();
}

function toggleReverse() {
  reverseMode = !reverseMode;
  if (!state.detailPins || state.detailPins.length <= 1) {
    applyReverseMode();
    return;
  }
  state.detailPins.reverse();
  renderDetailList();
  selectDetail(0);
  applyReverseMode();
}

function applyReverseMode() {
  var btn = document.getElementById('mode-reverse');
  if (btn) {
    btn.classList.toggle('active', reverseMode);
    btn.setAttribute('aria-pressed', reverseMode ? 'true' : 'false');
    var svg = btn.querySelector('svg');
    if (svg) {
      svg.innerHTML = reverseMode
        ? '<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>'
        : '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>';
    }
  }
}

// Open the bottom-left "Play": the videos in the bubbles currently on screen, newest first,
// auto-advancing. Built from the rendered cluster markers (videoMarkers) filtered to the visible
// bounds — so it plays exactly what the user can see, not every filtered pin in the dataset — then
// expanded so each visible bubble contributes all of its cheers.
export function playAll() {
  var bounds = state.map.getBounds();
  var pins = (state.videoMarkers || [])
    .filter(function (vm) { return bounds.contains([vm.lat, vm.lng]); })
    .reduce(function (acc, vm) { return acc.concat(vm.pins || []); }, [])
    .sort(byNewest);
  if (!pins.length) return;
  openDetail(pins, 0);
}

function pinChatId(pin) {
  return (pin && pin.chat_id != null) ? pin.chat_id : state.chatId;
}

function isOwner(pin) {
  return !!pin && state.currentUserId != null && pin.user_id === state.currentUserId;
}

function rowActivitySummary(pin) {
  var parts = [];
  var rx = pin && pin.reactions;
  if (rx && rx.length) {
    var total = rx.reduce(function (acc, r) { return acc + r.count; }, 0);
    if (total > 0) {
      parts.push(rx.slice(0, 2).map(function (r) { return r.emoji; }).join('') + ' ' + total);
    }
  }
  var cm = pin && pin.comments;
  if (cm && cm.length) {
    parts.push('💬 ' + cm.length);
  }
  return parts.join(' · ');
}

function renderDetailList() {
  var list = document.getElementById('detail-list');
  var pins = state.detailPins;
  var lh = '';
  pins.forEach(function (p, i) {
    var c = getColor(p.user_id, p.pin_color);
    var subTag = state.isAtlas
      ? (p.chat_title ? (p.chat_id === p.user_id ? '👤 ' : '💬 ') + esc(p.chat_title) : '')
      : rowTripLabel(p);
    var capHtml = p.caption ? '<div class="dr-caption">' + esc(p.caption) + '</div>' : '<div class="dr-caption"></div>';
    var actSummary = rowActivitySummary(p);
    var rxHtml = '<span class="dr-rx' + (actSummary ? '' : ' hidden') + '">' + esc(actSummary) + '</span>';
    lh += '<div class="detail-row" data-idx="' + i + '">' +
      '<div class="detail-thumb" style="box-shadow:0 0 0 2px ' + esc(c) + '">' +
        '<div class="vm-ph" style="background:' + esc(c) + '">' + placeholderGlyph(p) + '</div>' +
        '<video muted loop playsinline preload="none" data-file-id="' + esc(p.video_file_id) + '"></video>' +
      '</div>' +
      '<div class="detail-rowtext">' +
        '<div class="dr-line1"><span class="dr-name">' + esc(p.user_name) + '</span> <span class="dr-date">— ' + esc(relTime(p.created_at)) + '</span></div>' +
        '<div class="dr-trip">' + subTag + '</div>' +
        capHtml +
      '</div>' +
      rxHtml +
      '<span class="dr-check">✓</span>' +
    '</div>';
  });
  list.innerHTML = lh;
  renderRowMarks();  // re-apply selection highlights (e.g. after a reverse-order re-render)
  var vidCnt = document.getElementById('tab-videos-cnt');
  if (vidCnt) vidCnt.textContent = String(pins.length);
  var thumbs = list.querySelectorAll('video[data-file-id]');
  if (detailIo) detailIo.disconnect();
  var loadedCount = 0;
  // Load the first MAX_CONCURRENT_VIDEOS thumbnails immediately; lazy-load the rest on scroll.
  for (var ti = 0; ti < thumbs.length && ti < MAX_CONCURRENT_VIDEOS; ti++) {
    (function (v) {
      v.src = videoUrl(v.dataset.fileId);
      loadedCount++;
      v.addEventListener('loadeddata', function () { v.classList.add('ready'); });
      var r = v.play(); if (r && r.catch) r.catch(function () {});
    })(thumbs[ti]);
  }
  // Observe all thumbnails via Intersection Observer: off-screen ones release their slot,
  // allowing on-screen ones beyond the first 8 to load while respecting the concurrent cap.
  detailIo = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      var v = entry.target;
      if (entry.isIntersecting && !v.src) {
        if (loadedCount < MAX_CONCURRENT_VIDEOS) {
          v.src = videoUrl(v.dataset.fileId);
          loadedCount++;
          v.addEventListener('loadeddata', function () { v.classList.add('ready'); });
          var r = v.play(); if (r && r.catch) r.catch(function () {});
        }
      } else if (!entry.isIntersecting && v.src) {
        teardownVideo(v);
        v.classList.remove('ready');
        loadedCount--;
      }
    });
  }, { root: list, threshold: 0.01 });
  thumbs.forEach(function (v) { detailIo.observe(v); });
}

export function openDetail(pins, idx) {
  if (!pins || !pins.length) return;
  loopMode = false;
  reverseMode = false;
  editingCaption = false;
  savingCaption = false;
  savingComment = false;
  ownerMenuOpen = false;
  userMuted = false;
  setActiveTab('videos');
  setSheetExpanded(false);
  tripSel.clear();          // never carry a stale selection into a freshly opened player
  targetTripId = undefined;
  pauseMarkerVideos();
  state.detailPins = pins;
  renderDetailList();
  document.getElementById('detail').classList.remove('hidden');
  try {
    var tg = window.Telegram && window.Telegram.WebApp;
    if (tg && tg.disableVerticalSwipes) tg.disableVerticalSwipes();
  } catch (_) { /* older Telegram clients */ }
  applyLoopMode();
  applyReverseMode();
  selectDetail(idx);
}

// ---- Trip tagging: long-press to select cheers, then assign the selection to a trip ----
// Deliberate by design (no always-on control, so nobody mis-tags with a stray tap). Normal mode
// shows the playing clip's trip as a read-only pill; long-pressing a row enters selection mode,
// where you tap rows to add/remove, pick a destination chip, and Apply. The whole bar is hidden
// when the chat has no trips.
var tripSel = new Set();  // pin ids selected for (re)assignment; non-empty == selection mode
var targetTripId;         // chosen destination: undefined = none picked yet, null = "No trip", number = a trip
var lpTimer = null, lpFired = false, lpStart = null;  // long-press tracking

function inSelectMode() { return tripSel.size > 0; }

function tripName(tripId) {
  if (tripId == null) return null;
  var t = state.trips.find(function (x) { return x.id === tripId; });
  return t ? t.name : null;
}

// The per-row trip tag markup (escaped for innerHTML); empty string when the cheers has no trip.
function rowTripLabel(pin) {
  var name = tripName(pin.trip_id);
  return name ? '🍻 ' + esc(name) : '';
}

// Update the rows' trip tags in place after a (re)assignment — avoids a full list re-render, which
// would reload every thumbnail. Uses textContent (no escaping needed).
function refreshRowTrips() {
  document.querySelectorAll('.detail-row').forEach(function (r) {
    var p = state.detailPins && state.detailPins[parseInt(r.dataset.idx, 10)];
    var el = r.querySelector('.dr-trip');
    if (!p || !el) return;
    var name = tripName(p.trip_id);
    el.textContent = name ? '🍻 ' + name : '';
  });
}

function renderTripBar() {
  var bar = document.getElementById('detail-trip-bar');
  var controls = document.getElementById('detail-controls');
  var edit = document.getElementById('dtb-edit');
  updateOwnerButtons();
  renderPinMeta();
  if (state.isAtlas || !state.trips.length || !inSelectMode()) {
    if (bar) bar.classList.add('hidden');
    if (edit) edit.classList.add('hidden');
    if (controls) controls.classList.remove('hidden');
    return;
  }
  if (controls) controls.classList.add('hidden');  // free the header while tagging trips
  if (bar) bar.classList.remove('hidden');
  if (edit) edit.classList.remove('hidden');
  document.getElementById('dtb-count').textContent = tripSel.size + ' selected';
  document.getElementById('pin-trip-apply').disabled = targetTripId === undefined;
  renderTargetChips();
}

function renderTargetChips() {
  var wrap = document.getElementById('dtb-targets');
  var html = '<span class="tt-chip' + (targetTripId === null ? ' active' : '') + '" data-tt="none">No trip</span>';
  state.trips.forEach(function (t) {
    var dt = tripDupDate(t, state.trips);
    var lbl = esc(t.name) + (dt ? ' · ' + esc(dt) : '');
    html += '<span class="tt-chip' + (t.id === targetTripId ? ' active' : '') + '" data-tt="' + t.id + '">' + lbl + '</span>';
  });
  wrap.innerHTML = html;
}

// Reflect the selection set onto the currently rendered rows (checkmark + highlight).
function renderRowMarks() {
  document.querySelectorAll('.detail-row').forEach(function (r) {
    var p = state.detailPins && state.detailPins[parseInt(r.dataset.idx, 10)];
    r.classList.toggle('selected', !!(p && tripSel.has(p.id)));
  });
}

function toggleRowSelect(idx) {
  var p = state.detailPins && state.detailPins[idx];
  if (!p) return;
  var adding = !tripSel.has(p.id);
  if (adding) tripSel.add(p.id); else tripSel.delete(p.id);
  if (!inSelectMode()) targetTripId = undefined;  // deselected the last one -> leave selection mode clean
  renderRowMarks();
  renderTripBar();
  // Focus the big player on the clip you just picked, so "play what I'm selecting" holds. selectDetail
  // won't scroll the list while selecting (see its guard), so the list stays put under your finger.
  if (adding) selectDetail(idx);
}

// Which clip the big player advances to when the current one ends. Normally the next clip in the
// list (wrapping). While selecting, it stays within the selected set — looping a lone selection —
// so tagging never drags playback, and the auto-scroll, through unselected clips.
function nextPlayIndex(from) {
  var pins = state.detailPins;
  if (inSelectMode()) {
    for (var n = 1; n <= pins.length; n++) {
      var cand = (from + n) % pins.length;
      if (tripSel.has(pins[cand].id)) return cand;
    }
    return from;  // nothing else selected -> keep looping the current clip
  }
  return (from + 1) % pins.length;
}

function exitSelect() {
  tripSel.clear();
  targetTripId = undefined;
  renderRowMarks();
  renderTripBar();
}

function onTargetChipClick(e) {
  var chip = e.target.closest('.tt-chip');
  if (!chip) return;
  targetTripId = chip.dataset.tt === 'none' ? null : parseInt(chip.dataset.tt, 10);
  // Toggle 'active' in place rather than re-rendering the chips: rebuilding innerHTML would detach
  // the clicked node mid-event, and the overlay's outside-click-to-close guard (which reads the now
  // orphaned e.target) would then mistake it for a click outside the sheet and close the player.
  document.querySelectorAll('#dtb-targets .tt-chip').forEach(function (c) { c.classList.toggle('active', c === chip); });
  document.getElementById('pin-trip-apply').disabled = targetTripId === undefined;
}

function onTripApply() {
  if (!inSelectMode() || targetTripId === undefined) return;
  var tripId = targetTripId;
  var pins = state.detailPins.filter(function (p) { return tripSel.has(p.id); });
  var ids = pins.map(function (p) { return p.id; });
  var moves = pins.map(function (p) { return { from: p.trip_id, to: tripId }; });
  applyTripCountsDelta(moves);  // optimistic; reverted if the API rejects it
  pins.forEach(function (p) { p.trip_id = tripId; });
  refreshRowTrips();  // reflect the new tags in the rows right away
  var btn = document.getElementById('pin-trip-apply');
  btn.disabled = true;
  btn.textContent = 'Applying…';
  apiPost('/api/pin-trip', state.chatId, { pin_ids: ids, trip_id: tripId })
    .then(function (r) { if (!r.ok) throw new Error('failed ' + r.status); })
    .then(function () { btn.textContent = 'Apply'; exitSelect(); })
    .catch(function () {
      pins.forEach(function (p, i) { p.trip_id = moves[i].from; });  // revert pins + counts + row tags, keep the selection to retry
      applyTripCountsDelta(moves.map(function (m) { return { from: m.to, to: m.from }; }));
      refreshRowTrips();
      btn.textContent = 'Apply';
      btn.disabled = false;
    });
}

function hapticBump() {
  try {
    if (window.Telegram && Telegram.WebApp && Telegram.WebApp.HapticFeedback) Telegram.WebApp.HapticFeedback.impactOccurred('medium');
  } catch (_) { /* no haptics outside Telegram */ }
}

function hapticLight() {
  try {
    if (window.Telegram && Telegram.WebApp && Telegram.WebApp.HapticFeedback) Telegram.WebApp.HapticFeedback.impactOccurred('light');
  } catch (_) { /* no haptics outside Telegram */ }
}

function hapticSuccess() {
  try {
    if (window.Telegram && Telegram.WebApp && Telegram.WebApp.HapticFeedback) Telegram.WebApp.HapticFeedback.notificationOccurred('success');
  } catch (_) { /* no haptics outside Telegram */ }
}

function setSheetExpanded(expanded) {
  sheetExpanded = !!expanded;
  var overlay = document.getElementById('detail');
  if (overlay) overlay.classList.toggle('expanded', sheetExpanded);
  var handle = document.getElementById('detail-handle');
  if (handle) handle.setAttribute('aria-expanded', sheetExpanded ? 'true' : 'false');
}

function setOwnerMenuOpen(open) {
  ownerMenuOpen = !!open;
  var menu = document.getElementById('detail-owner-menu');
  var moreBtn = document.getElementById('detail-more');
  if (menu) menu.classList.toggle('hidden', !ownerMenuOpen);
  if (moreBtn) {
    moreBtn.classList.toggle('active', ownerMenuOpen);
    moreBtn.setAttribute('aria-expanded', ownerMenuOpen ? 'true' : 'false');
  }
}

function updateOwnerButtons() {
  var moreBtn = document.getElementById('detail-more');
  var delBtn = document.getElementById('detail-delete');
  var moveBtn = document.getElementById('detail-move');
  var cur = state.detailPins && state.detailPins[detailIdx];
  var canEdit = !inSelectMode() && isOwner(cur);
  if (moreBtn) moreBtn.classList.toggle('hidden', !canEdit);
  if (!canEdit && ownerMenuOpen) setOwnerMenuOpen(false);
  if (delBtn) delBtn.disabled = deletingPin;
  if (moveBtn) moveBtn.disabled = deletingPin;
}

function refreshRowMeta(pin) {
  if (!pin || !state.detailPins) return;
  document.querySelectorAll('.detail-row').forEach(function (r) {
    var p = state.detailPins[parseInt(r.dataset.idx, 10)];
    if (!p || p.id !== pin.id) return;
    var capEl = r.querySelector('.dr-caption');
    if (capEl) capEl.textContent = p.caption || '';
    var rxEl = r.querySelector('.dr-rx');
    if (rxEl) {
      var sum = rowActivitySummary(p);
      rxEl.textContent = sum;
      rxEl.classList.toggle('hidden', !sum);
    }
  });
}

function syncPinInAllPins(pinId, patchFn) {
  state.allPins.forEach(function (p) { if (p.id === pinId) patchFn(p); });
}

function setActiveTab(tab) {
  activeTab = (tab === 'replies') ? 'replies' : 'videos';
  var vTab = document.getElementById('tab-videos');
  var rTab = document.getElementById('tab-replies');
  var vPanel = document.getElementById('detail-list');
  var rPanel = document.getElementById('dm-comments');
  var isReplies = activeTab === 'replies';
  if (vTab) vTab.setAttribute('aria-selected', isReplies ? 'false' : 'true');
  if (rTab) rTab.setAttribute('aria-selected', isReplies ? 'true' : 'false');
  if (vPanel) vPanel.hidden = isReplies;
  if (rPanel) rPanel.hidden = !isReplies;
}

function renderComments(pin) {
  var listEl = document.getElementById('dm-comments-list');
  var rTab = document.getElementById('tab-replies');
  var rCnt = document.getElementById('tab-replies-cnt');
  if (!listEl) return;
  var comments = (pin && pin.comments) || [];
  if (rCnt) rCnt.textContent = comments.length ? String(comments.length) : '';
  if (rTab) rTab.classList.toggle('has-replies', comments.length > 0);
  if (!comments.length) {
    listEl.innerHTML = '<div class="cm-empty"><div class="cm-empty-icon">💬</div>No replies yet — reply here or directly on the video in Telegram.</div>';
    return;
  }
  var html = '';
  comments.forEach(function (cm) {
    var canDel = state.currentUserId != null && cm.user_id === state.currentUserId;
    html += '<div class="cm-item" data-cid="' + cm.id + '">' +
      '<div class="cm-body">' +
        '<span class="cm-author">' + esc(cm.user_name) + '</span>' +
        '<span class="cm-time">' + esc(relTime(cm.created_at)) + '</span>' +
        '<div class="cm-text">' + esc(cm.text) + '</div>' +
      '</div>' +
      (canDel ? '<button type="button" class="cm-del" data-del-cid="' + cm.id + '" title="Delete reply" aria-label="Delete reply">✕</button>' : '') +
    '</div>';
  });
  listEl.innerHTML = html;
}

function renderPinMeta() {
  var cur = state.detailPins && state.detailPins[detailIdx];
  var authorEl = document.getElementById('stage-author');
  var timeEl = document.getElementById('stage-time');
  var infoEl = document.getElementById('dm-info');
  var tripEl = document.getElementById('stage-trip');
  var capView = document.getElementById('dm-caption-view');
  var capEdit = document.getElementById('dm-caption-edit');
  var capText = document.getElementById('dm-caption-text');
  var capEditBtn = document.getElementById('dm-caption-btn');
  var rxBar = document.getElementById('dm-reactions');

  if (!cur) {
    if (authorEl) authorEl.textContent = '';
    if (timeEl) timeEl.textContent = '';
    if (infoEl) { infoEl.textContent = ''; infoEl.classList.add('hidden'); }
    if (tripEl) { tripEl.textContent = ''; tripEl.classList.add('hidden'); }
    if (capView) capView.classList.add('hidden');
    if (capEdit) capEdit.classList.add('hidden');
    if (rxBar) { rxBar.innerHTML = ''; rxBar.classList.add('hidden'); }
    renderComments(null);
    return;
  }

  // Stage header: author · relative time + place/chat chip + trip chip
  if (authorEl) authorEl.textContent = cur.user_name || '';
  if (timeEl) timeEl.textContent = cur.created_at ? ('· ' + relTime(cur.created_at)) : '';
  if (infoEl) {
    var bits = [];
    var place = [cur.city, cur.country].filter(Boolean).join(', ');
    var flag = countryFlag(cur.country_code);
    if (place || flag) bits.push('📍 ' + [place, flag].filter(Boolean).join(' '));
    if (state.isAtlas && cur.chat_title) {
      bits.push((cur.chat_id === cur.user_id ? '👤 ' : '💬 ') + cur.chat_title);
    }
    infoEl.textContent = bits.join(' · ');
    infoEl.classList.toggle('hidden', !bits.length);
  }
  if (tripEl) {
    var tName = (!state.isAtlas && state.trips.length) ? tripName(cur.trip_id) : null;
    if (tName) {
      tripEl.textContent = '🍻 ' + tName;
      tripEl.title = 'Tap to change trip';
      tripEl.classList.remove('hidden');
    } else {
      tripEl.textContent = '';
      tripEl.classList.add('hidden');
    }
  }

  // Stage footer: caption view vs inline editor + frosted reaction pills
  var owner = isOwner(cur);
  if (editingCaption && owner) {
    if (capView) capView.classList.add('hidden');
    if (capEdit) capEdit.classList.remove('hidden');
    if (rxBar) rxBar.classList.add('hidden');
  } else {
    editingCaption = false;
    if (capEdit) capEdit.classList.add('hidden');
    if (rxBar) rxBar.classList.remove('hidden');
    if (capView) {
      var hasCap = !!(cur.caption && cur.caption.trim());
      capView.classList.toggle('hidden', !hasCap && !owner);
      if (capText) {
        capText.textContent = hasCap ? cur.caption : '';
        capText.classList.toggle('hidden', !hasCap);
      }
      if (capEditBtn) {
        capEditBtn.classList.toggle('hidden', !owner);
        capEditBtn.textContent = hasCap ? '✎' : '+ Caption';
        capEditBtn.classList.toggle('is-add', !hasCap);
      }
    }
  }

  // Reaction pills: active emojis on this pin (including any Telegram-synced emoji like 😍/🎉)
  // followed by the 6 default quick-pick emojis.
  if (rxBar && !editingCaption) {
    var byEmoji = {};
    var orderedEmojis = [];
    (cur.reactions || []).forEach(function (r) {
      byEmoji[r.emoji] = r;
      if (r.count > 0 && orderedEmojis.indexOf(r.emoji) === -1) orderedEmojis.push(r.emoji);
    });
    DEFAULT_REACTIONS.forEach(function (em) {
      if (orderedEmojis.indexOf(em) === -1) orderedEmojis.push(em);
    });
    var existing = Array.from(rxBar.querySelectorAll('.rx-pill'));
    var sameOrder = existing.length === orderedEmojis.length && existing.every(function (btn, idx) {
      return btn.dataset.emoji === orderedEmojis[idx];
    });
    if (sameOrder) {
      existing.forEach(function (btn) {
        var em = btn.dataset.emoji;
        var item = byEmoji[em];
        var cnt = item ? item.count : 0;
        var me = !!(item && item.me);
        btn.classList.toggle('active', me);
        btn.setAttribute('aria-pressed', me ? 'true' : 'false');
        var cntEl = btn.querySelector('.rx-cnt');
        if (cntEl) {
          cntEl.textContent = cnt > 0 ? String(cnt) : '';
          cntEl.classList.toggle('hidden', cnt <= 0);
        }
      });
    } else {
      var rhtml = '';
      orderedEmojis.forEach(function (em) {
        var item = byEmoji[em];
        var cnt = item ? item.count : 0;
        var me = !!(item && item.me);
        rhtml += '<button type="button" class="rx-pill' + (me ? ' active' : '') + '" data-emoji="' + esc(em) + '" aria-pressed="' + (me ? 'true' : 'false') + '">' +
          '<span class="rx-em">' + esc(em) + '</span>' +
          '<span class="rx-cnt' + (cnt > 0 ? '' : ' hidden') + '">' + (cnt > 0 ? cnt : '') + '</span>' +
        '</button>';
      });
      rxBar.innerHTML = rhtml;
    }
  }

  renderComments(cur);
}

function openCaptionEditor() {
  var cur = state.detailPins && state.detailPins[detailIdx];
  if (!isOwner(cur)) return;
  setOwnerMenuOpen(false);
  editingCaption = true;
  if (sheetExpanded) setSheetExpanded(false);
  renderPinMeta();
  var input = document.getElementById('dm-caption-input');
  if (input) {
    input.value = cur.caption || '';
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
}

function closeCaptionEditor() {
  editingCaption = false;
  savingCaption = false;
  renderPinMeta();
}

function saveCaption() {
  if (savingCaption) return;
  var cur = state.detailPins && state.detailPins[detailIdx];
  if (!isOwner(cur)) return;
  var input = document.getElementById('dm-caption-input');
  var saveBtn = document.getElementById('dm-caption-save');
  if (!input) return;
  var val = input.value.trim();
  if (val.length > 140) {
    showToast('Caption max 140 characters.');
    return;
  }
  savingCaption = true;
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = '…'; }
  apiPost('/api/pin-caption', pinChatId(cur), { pin_id: cur.id, caption: val })
    .then(function (r) { if (!r.ok) throw new Error('caption ' + r.status); return r.json(); })
    .then(function (res) {
      cur.caption = res.caption;
      syncPinInAllPins(cur.id, function (p) { p.caption = res.caption; });
      savingCaption = false;
      if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save'; }
      editingCaption = false;
      renderPinMeta();
      refreshRowMeta(cur);
      hapticSuccess();
    })
    .catch(function () {
      savingCaption = false;
      if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save'; }
      showToast('Could not save caption.');
    });
}

function isTypingInSheet() {
  var ae = document.activeElement;
  return editingCaption || (ae && (ae.id === 'dm-caption-input' || ae.id === 'dm-comment-input'));
}

function onCommentSubmit(e) {
  if (e) e.preventDefault();
  if (savingComment) return;
  var cur = state.detailPins && state.detailPins[detailIdx];
  if (!cur) return;
  var input = document.getElementById('dm-comment-input');
  var sendBtn = document.getElementById('dm-comment-send');
  if (!input) return;
  var text = input.value.trim();
  if (!text) return;
  if (text.length > 500) {
    showToast('Reply max 500 characters.');
    return;
  }
  savingComment = true;
  if (sendBtn) sendBtn.disabled = true;
  var pinId = cur.id;
  apiPost('/api/pin-comment', pinChatId(cur), { pin_id: pinId, text: text })
    .then(function (r) { if (!r.ok) throw new Error('comment ' + r.status); return r.json(); })
    .then(function (res) {
      savingComment = false;
      if (sendBtn) sendBtn.disabled = false;
      input.value = '';
      if (!cur.comments) cur.comments = [];
      cur.comments = cur.comments.concat([res.comment]);
      syncPinInAllPins(pinId, function (p) { p.comments = cur.comments; });
      renderComments(cur);
      refreshRowMeta(cur);
      var cmListEl = document.getElementById('dm-comments-list');
      if (cmListEl) cmListEl.scrollTop = cmListEl.scrollHeight;
      hapticLight();
    })
    .catch(function () {
      savingComment = false;
      if (sendBtn) sendBtn.disabled = false;
      showToast('Could not post reply.');
    });
}

function onCommentListClick(e) {
  var delBtn = e.target.closest('[data-del-cid]');
  if (!delBtn) return;
  var cur = state.detailPins && state.detailPins[detailIdx];
  if (!cur) return;
  var cid = parseInt(delBtn.dataset.delCid, 10);
  if (!cid) return;
  delBtn.disabled = true;
  var pinId = cur.id;
  apiPost('/api/delete-comment', pinChatId(cur), { comment_id: cid })
    .then(function (r) { if (!r.ok) throw new Error('del ' + r.status); return r.json(); })
    .then(function () {
      cur.comments = (cur.comments || []).filter(function (c) { return c.id !== cid; });
      syncPinInAllPins(pinId, function (p) { p.comments = cur.comments; });
      renderComments(cur);
      refreshRowMeta(cur);
      hapticLight();
    })
    .catch(function () {
      delBtn.disabled = false;
      showToast('Could not delete reply.');
    });
}

function onReactionClick(e) {
  var btn = e.target.closest('.rx-pill');
  if (!btn) return;
  var cur = state.detailPins && state.detailPins[detailIdx];
  if (!cur) return;
  var emoji = btn.dataset.emoji;
  if (!emoji) return;
  hapticLight();

  // Optimistic single-reaction toggle (matches Telegram: tapping the same emoji removes it;
  // tapping a different emoji replaces your previous reaction on this pin).
  var prev = (cur.reactions || []).map(function (r) { return Object.assign({}, r); });
  var next = prev.map(function (r) { return Object.assign({}, r); });
  var existing = next.find(function (r) { return r.emoji === emoji; });
  if (existing && existing.me) {
    existing.count = Math.max(0, existing.count - 1);
    existing.me = false;
  } else {
    next.forEach(function (r) {
      if (r.me) {
        r.count = Math.max(0, r.count - 1);
        r.me = false;
      }
    });
    if (existing) {
      existing.count += 1;
      existing.me = true;
    } else {
      next.push({ emoji: emoji, count: 1, me: true });
    }
  }
  next = next.filter(function (r) { return r.count > 0; });
  cur.reactions = next;
  syncPinInAllPins(cur.id, function (p) { p.reactions = next; });
  renderPinMeta();
  refreshRowMeta(cur);

  var pinId = cur.id;
  apiPost('/api/pin-reaction', pinChatId(cur), { pin_id: pinId, emoji: emoji })
    .then(function (r) { if (!r.ok) throw new Error('rx ' + r.status); return r.json(); })
    .then(function (res) {
      cur.reactions = res.reactions || [];
      syncPinInAllPins(pinId, function (p) { p.reactions = cur.reactions; });
      renderPinMeta();
      refreshRowMeta(cur);
    })
    .catch(function () {
      cur.reactions = prev;
      syncPinInAllPins(pinId, function (p) { p.reactions = prev; });
      renderPinMeta();
      refreshRowMeta(cur);
      showToast('Could not save reaction.');
    });
}

function onMovePin() {
  if (deletingPin || !state.detailPins || !state.detailPins[detailIdx]) return;
  var pin = state.detailPins[detailIdx];
  if (!isOwner(pin)) return;
  setOwnerMenuOpen(false);
  closeDetail(true);  // skip resuming marker videos; relocate mode manages playback
  startRelocate(pin);
}

function confirmAction(message, onConfirm) {
  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg && tg.showConfirm && tg.isVersionAtLeast && tg.isVersionAtLeast('6.2')) {
    tg.showConfirm(message, function (ok) { if (ok) onConfirm(); });
    return;
  }
  if (window.confirm(message)) onConfirm();
}

function notifyError(message) {
  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg && tg.showAlert && tg.isVersionAtLeast && tg.isVersionAtLeast('6.2')) {
    tg.showAlert(message);
    return;
  }
  window.alert(message);
}

function onDeletePin() {
  if (deletingPin || !state.detailPins || !state.detailPins[detailIdx]) return;
  var pin = state.detailPins[detailIdx];
  if (!isOwner(pin)) return;
  setOwnerMenuOpen(false);

  confirmAction('Delete this cheers from the map?', function () {
    deletingPin = true;
    updateOwnerButtons();
    apiPost('/api/delete-pin', pinChatId(pin), { pin_id: pin.id })
      .then(function (r) { if (!r.ok) throw new Error('failed ' + r.status); return r.json(); })
      .then(function () {
        deletingPin = false;
        hapticSuccess();
        invalidateStats();
        state.allPins = state.allPins.filter(function (p) { return p.id !== pin.id; });
        state.totalPins = Math.max(0, state.totalPins - 1);
        if (!state.isAtlas && pin.trip_id != null) applyTripCountsDelta([{ from: pin.trip_id, to: null }]);
        state.detailPins = state.detailPins.filter(function (p) { return p.id !== pin.id; });
        state.skipBoundsFit = true;
        renderMarkers();
        if (!state.detailPins.length) {
          closeDetail();
        } else {
          renderDetailList();
          selectDetail(Math.min(detailIdx, state.detailPins.length - 1));
        }
      })
      .catch(function () {
        deletingPin = false;
        updateOwnerButtons();
        notifyError('Could not delete this cheers. Please try again.');
      });
  });
}

function syncMuteButton() {
  var btn = document.getElementById('detail-mute');
  if (!btn) return;
  btn.setAttribute('aria-pressed', userMuted ? 'true' : 'false');
  btn.title = userMuted ? 'Unmute' : 'Mute';
  btn.innerHTML = userMuted
    ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="22" y1="9" x2="16" y2="15"/><line x1="16" y1="9" x2="22" y2="15"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>';
}

function selectDetail(i) {
  if (!state.detailPins || !state.detailPins[i]) return;
  detailIdx = i;
  editingCaption = false;
  setOwnerMenuOpen(false);
  var p = state.detailPins[i];
  renderTripBar();  // refreshes owner buttons, stage header/caption/reactions/replies, and trip bar
  var big = document.getElementById('detail-big');
  var old = big.querySelector('video');
  if (old) teardownVideo(old);
  // Custom Telegram-style circular video player: clean tap-to-pause + SVG progress ring (zero
  // native browser controls covering the face when shrunk to a mini-player).
  big.innerHTML =
    '<video id="detail-bigvid" playsinline></video>' +
    '<svg class="vid-ring" viewBox="0 0 100 100" aria-hidden="true">' +
      '<circle class="vid-ring-track" cx="50" cy="50" r="48.5"/>' +
      '<circle id="detail-ring-prog" class="vid-ring-prog" cx="50" cy="50" r="48.5"/>' +
    '</svg>' +
    '<div id="detail-pause-badge" class="vid-pause-badge hidden" aria-hidden="true"><div class="vid-pause-circle">▶</div></div>';
  var v = document.getElementById('detail-bigvid');
  var ring = document.getElementById('detail-ring-prog');
  var badge = document.getElementById('detail-pause-badge');
  v.src = videoUrl(p.video_file_id);
  v.muted = userMuted;
  v.loop = loopMode;  // loop replays this clip; otherwise it advances on 'ended'
  syncMuteButton();

  v.addEventListener('timeupdate', function () {
    if (!ring || !v.duration || isNaN(v.duration)) return;
    var pct = Math.max(0, Math.min(1, v.currentTime / v.duration));
    ring.style.strokeDashoffset = String((RING_CIRC * (1 - pct)).toFixed(2));
  });
  v.addEventListener('play', function () { if (badge) badge.classList.add('hidden'); });
  v.addEventListener('pause', function () { if (badge && !v.ended) badge.classList.remove('hidden'); });
  v.addEventListener('ended', function () {
    // Stay on the current clip while the user is reading/writing replies or editing a caption.
    if (activeTab === 'replies' || isTypingInSheet()) {
      v.currentTime = 0;
      var rp = v.play();
      if (rp && rp.catch) rp.catch(function () {});
      return;
    }
    if (!loopMode && state.detailPins) selectDetail(nextPlayIndex(i));
  });

  var pr = v.play();
  if (pr && pr.catch) {
    pr.catch(function () {
      userMuted = true;
      v.muted = true;
      syncMuteButton();
      var r = v.play();
      if (r && r.catch) r.catch(function () {});
    });
  }
  var rows = document.querySelectorAll('.detail-row');
  rows.forEach(function (r) {
    var active = parseInt(r.dataset.idx, 10) === i;
    r.classList.toggle('active', active);
    // Reactions & caption live on the stage above the drawer, so auto-scrolling #detail-list never
    // yanks reactions out of view; pause auto-scroll while reading replies or tagging trips.
    if (active && activeTab === 'videos' && !loopMode && !inSelectMode() && !isTypingInSheet()) {
      r.scrollIntoView({ block: 'nearest' });
    }
  });
}

function closeDetail(skipResume) {
  var overlay = document.getElementById('detail');
  overlay.classList.add('hidden');
  overlay.classList.remove('dragging');
  overlay.querySelectorAll('video').forEach(teardownVideo);
  document.getElementById('detail-big').innerHTML = '';
  document.getElementById('detail-list').innerHTML = '';
  var vidCnt = document.getElementById('tab-videos-cnt');
  if (vidCnt) vidCnt.textContent = '';
  if (detailIo) { detailIo.disconnect(); detailIo = null; }
  state.detailPins = null;
  deletingPin = false;
  editingCaption = false;
  savingCaption = false;
  savingComment = false;
  setActiveTab('videos');
  setOwnerMenuOpen(false);
  setSheetExpanded(false);
  loopMode = false;
  reverseMode = false;
  tripSel.clear();
  targetTripId = undefined;
  updateOwnerButtons();
  renderPinMeta();
  try {
    var tg = window.Telegram && window.Telegram.WebApp;
    if (tg && tg.enableVerticalSwipes) tg.enableVerticalSwipes();
  } catch (_) { /* older Telegram clients */ }
  if (!skipResume) updateVideoPlayback();  // resume the map previews
}

// Wire the overlay's click handlers (DOM is static, so this runs once at startup).
export function initDetail() {
  document.getElementById('play-all').addEventListener('click', playAll);
  document.getElementById('detail-delete').addEventListener('click', onDeletePin);
  var moveBtn = document.getElementById('detail-move');
  if (moveBtn) moveBtn.addEventListener('click', onMovePin);
  var moreBtn = document.getElementById('detail-more');
  if (moreBtn) moreBtn.addEventListener('click', function () { setOwnerMenuOpen(!ownerMenuOpen); });

  // Live finger-tracking drag across the drawer header (#detail-handle + #detail-controls):
  // pulling up/down resizes the sheet and scales the circular video smoothly in real time, then
  // snaps to expanded or collapsed on release. Tapping #detail-handle still toggles cleanly.
  var header = document.getElementById('detail-header');
  var sheet = document.getElementById('detail-sheet');
  var overlay = document.getElementById('detail');
  var big = document.getElementById('detail-big');
  if (header && sheet && overlay && big) {
    var drag = null;
    var suppressHandleClick = false;

    function clearInlineDragStyles() {
      overlay.classList.remove('dragging');
      sheet.style.height = '';
      sheet.style.maxHeight = '';
      big.style.width = '';
      big.style.height = '';
    }

    header.addEventListener('pointerdown', function (e) {
      // Let real buttons (Loop, Reverse, tabs, ..., trip chips, Cancel/Apply) receive clicks normally;
      // the grab handle itself IS draggable.
      if (e.target.closest('button, .tt-chip') && !e.target.closest('#detail-handle')) return;
      var vh = window.innerHeight;
      var collapsedH = Math.round(vh * 0.44);
      var expandedH = Math.max(collapsedH + 100, vh - 116);
      var startH = sheet.getBoundingClientRect().height;
      var fullVid = Math.max(118, Math.min(Math.round(window.innerWidth * 0.48), Math.round(vh * 0.25), 206));
      drag = {
        pointerId: e.pointerId,
        startY: e.clientY,
        lastY: e.clientY,
        lastT: Date.now(),
        velY: 0,
        startH: startH,
        collapsedH: collapsedH,
        expandedH: expandedH,
        fullVid: fullVid,
        moved: false,
        progress: sheetExpanded ? 1 : 0,
      };
      try { header.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
    });

    header.addEventListener('pointermove', function (e) {
      if (!drag || e.pointerId !== drag.pointerId) return;
      var dy = e.clientY - drag.startY;
      var now = Date.now();
      var dt = Math.max(1, now - drag.lastT);
      drag.velY = (e.clientY - drag.lastY) / dt;
      drag.lastY = e.clientY;
      drag.lastT = now;

      if (!drag.moved && Math.abs(dy) < 6) return;
      if (!drag.moved) {
        drag.moved = true;
        overlay.classList.add('dragging');
      }

      var minH = Math.round(drag.collapsedH * 0.85);
      var newH = Math.max(minH, Math.min(drag.expandedH, drag.startH - dy));
      var span = Math.max(1, drag.expandedH - drag.collapsedH);
      var progress = Math.max(0, Math.min(1, (newH - drag.collapsedH) / span));
      drag.progress = progress;

      sheet.style.height = newH + 'px';
      sheet.style.maxHeight = newH + 'px';
      var vidSize = Math.round(drag.fullVid - (drag.fullVid - 76) * progress);
      big.style.width = vidSize + 'px';
      big.style.height = vidSize + 'px';
    });

    function finishDrag(e) {
      if (!drag || e.pointerId !== drag.pointerId) return;
      var d = drag;
      drag = null;
      try { header.releasePointerCapture(e.pointerId); } catch (_) { /* ignore */ }
      clearInlineDragStyles();
      if (!d.moved) return;
      suppressHandleClick = true;
      setTimeout(function () { suppressHandleClick = false; }, 120);
      var shouldExpand;
      if (d.velY < -0.3) shouldExpand = true;
      else if (d.velY > 0.3) shouldExpand = false;
      else shouldExpand = d.progress >= 0.42;
      setSheetExpanded(shouldExpand);
    }

    header.addEventListener('pointerup', finishDrag);
    header.addEventListener('pointercancel', finishDrag);

    var handle = document.getElementById('detail-handle');
    if (handle) {
      handle.addEventListener('click', function () {
        if (suppressHandleClick) return;
        setSheetExpanded(!sheetExpanded);
      });
    }
  }

  document.getElementById('mode-loop').addEventListener('click', toggleLoop);
  document.getElementById('mode-reverse').addEventListener('click', toggleReverse);
  document.getElementById('dtb-targets').addEventListener('click', onTargetChipClick);
  document.getElementById('pin-trip-apply').addEventListener('click', onTripApply);
  document.getElementById('pin-trip-cancel').addEventListener('click', exitSelect);

  var rxBar = document.getElementById('dm-reactions');
  if (rxBar) rxBar.addEventListener('click', onReactionClick);

  var capBtn = document.getElementById('dm-caption-btn');
  if (capBtn) capBtn.addEventListener('click', openCaptionEditor);
  var capText = document.getElementById('dm-caption-text');
  if (capText) capText.addEventListener('click', function () {
    var cur = state.detailPins && state.detailPins[detailIdx];
    if (isOwner(cur)) openCaptionEditor();
  });
  var capSave = document.getElementById('dm-caption-save');
  if (capSave) capSave.addEventListener('click', saveCaption);
  var capCancel = document.getElementById('dm-caption-cancel');
  if (capCancel) capCancel.addEventListener('click', closeCaptionEditor);
  var capInput = document.getElementById('dm-caption-input');
  if (capInput) {
    capInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); saveCaption(); }
      else if (e.key === 'Escape') { e.preventDefault(); closeCaptionEditor(); }
    });
  }

  var cmForm = document.getElementById('dm-comment-form');
  if (cmForm) cmForm.addEventListener('submit', onCommentSubmit);
  var cmInput = document.getElementById('dm-comment-input');
  if (cmInput) cmInput.addEventListener('focus', function () { setSheetExpanded(true); });
  var cmList = document.getElementById('dm-comments-list');
  if (cmList) cmList.addEventListener('click', onCommentListClick);

  // Segmented drawer tabs: Videos | Replies (with arrow-key navigation).
  var tabVideos = document.getElementById('tab-videos');
  var tabReplies = document.getElementById('tab-replies');
  if (tabVideos) tabVideos.addEventListener('click', function () { setActiveTab('videos'); });
  if (tabReplies) tabReplies.addEventListener('click', function () { setActiveTab('replies'); });
  var tabsWrap = document.getElementById('detail-tabs');
  if (tabsWrap) {
    tabsWrap.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      var next = activeTab === 'videos' ? 'replies' : 'videos';
      setActiveTab(next);
      var nextEl = next === 'videos' ? tabVideos : tabReplies;
      if (nextEl) nextEl.focus();
    });
  }

  // Tapping the frosted trip chip in the stage enters trip-tagging mode for the active video.
  var stageTrip = document.getElementById('stage-trip');
  if (stageTrip) {
    stageTrip.addEventListener('click', function () {
      if (state.isAtlas || !state.trips.length || !state.detailPins || !state.detailPins[detailIdx]) return;
      setActiveTab('videos');
      toggleRowSelect(detailIdx);
    });
  }

  // Tap circular video to pause/play; tap mute pill to toggle sound.
  var bigEl = document.getElementById('detail-big');
  if (bigEl) {
    bigEl.addEventListener('click', function () {
      var v = document.getElementById('detail-bigvid');
      if (!v) return;
      if (v.paused) {
        var r = v.play();
        if (r && r.catch) r.catch(function () {});
      } else {
        v.pause();
      }
    });
  }
  var muteBtn = document.getElementById('detail-mute');
  if (muteBtn) {
    muteBtn.addEventListener('click', function () {
      userMuted = !userMuted;
      var v = document.getElementById('detail-bigvid');
      if (v) v.muted = userMuted;
      syncMuteButton();
    });
  }

  var closeBtn = document.getElementById('detail-close');
  if (closeBtn) closeBtn.addEventListener('click', function () { closeDetail(); });

  document.getElementById('detail').addEventListener('click', function (e) {
    // Keep open when tapping interactive stage controls (video, author/trip, caption, reactions), close, or the drawer.
    if (
      e.target.closest('#detail-big-wrap') ||
      e.target.closest('#stage-head') ||
      e.target.closest('#stage-foot') ||
      e.target.closest('#detail-sheet') ||
      e.target.closest('#detail-close')
    ) return;
    // When expanded, tapping the backdrop beside the mini-video collapses back to full-size video first!
    if (sheetExpanded) {
      setSheetExpanded(false);
      return;
    }
    closeDetail();
  });

  // Row interaction: a tap plays the clip (or toggles its selection while in selection mode); a
  // long-press (held ~half a second, no drag) selects the clip for trip tagging.
  var list = document.getElementById('detail-list');
  list.addEventListener('pointerdown', function (e) {
    var row = e.target.closest('.detail-row');
    if (!row || state.isAtlas || !state.trips.length) return;  // no trips (or Atlas) -> nothing to tag
    var idx = parseInt(row.dataset.idx, 10);
    lpFired = false;
    lpStart = { x: e.clientX, y: e.clientY };
    clearTimeout(lpTimer);
    lpTimer = setTimeout(function () { lpFired = true; hapticBump(); toggleRowSelect(idx); }, 500);
  });
  list.addEventListener('pointermove', function (e) {
    if (lpStart && Math.abs(e.clientX - lpStart.x) + Math.abs(e.clientY - lpStart.y) > 12) clearTimeout(lpTimer);
  });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (ev) {
    list.addEventListener(ev, function () { clearTimeout(lpTimer); });
  });
  list.addEventListener('click', function (e) {
    var row = e.target.closest('.detail-row');
    if (!row) return;
    var idx = parseInt(row.dataset.idx, 10);
    if (lpFired) { lpFired = false; return; }  // swallow the click a long-press may synthesize
    if (inSelectMode()) toggleRowSelect(idx);
    else selectDetail(idx);
  });
}
