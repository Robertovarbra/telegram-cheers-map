// Location-share mode: the bot's "Share location" button opens this app with startapp=loc_c<chatId>.
// Instead of the map, show a one-tap screen that reads the device location via the Telegram
// LocationManager (Bot API 8.0+) and posts it to /api/submit-location to finish pinning the cheers.
import { esc } from './util.js';
import { authHeaders } from './api.js';

export function startLocationShare(cid) {
  var tg = window.Telegram && window.Telegram.WebApp;
  var lm = tg && tg.LocationManager;
  var btnColor = (tg && tg.themeParams && tg.themeParams.button_color) || '#3498db';
  var btnText = (tg && tg.themeParams && tg.themeParams.button_text_color) || '#ffffff';
  var ATTACH_HINT = 'use the 📍 attachment menu in the chat to send your location instead.';

  document.body.innerHTML =
    '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;padding:24px;'
    + 'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;text-align:center;color:#222;">'
    + '<div style="font-size:52px;margin-bottom:14px;">📍</div>'
    + '<div id="loc-title" style="font-size:18px;font-weight:600;margin-bottom:8px;">Share your location</div>'
    + '<div id="loc-msg" style="font-size:14px;color:#666;margin-bottom:22px;max-width:300px;line-height:1.45;"><span class="spinner"></span>Getting your location…</div>'
    + '<button id="loc-btn" style="display:none;border:none;border-radius:10px;padding:12px 24px;font-size:15px;font-weight:600;cursor:pointer;'
    + 'background:' + esc(btnColor) + ';color:' + esc(btnText) + ';">Share my location</button>'
    + '</div>';

  var titleEl = document.getElementById('loc-title');
  var msgEl = document.getElementById('loc-msg');
  var btnEl = document.getElementById('loc-btn');
  var watchdog = null;

  function clearWatchdog() { if (watchdog) { clearTimeout(watchdog); watchdog = null; } }
  function setMsg(t) { msgEl.textContent = t; }
  function setBusy(t) { msgEl.innerHTML = '<span class="spinner"></span>' + t; }  // t is always a literal -> safe
  function hideBtn() { btnEl.style.display = 'none'; }
  function showBtn(label, handler) {
    btnEl.style.display = '';
    btnEl.disabled = false;
    btnEl.textContent = label;
    btnEl.onclick = handler;
  }

  function showError(text, canOpenSettings) {
    clearWatchdog();
    titleEl.textContent = 'Couldn’t pin';
    setMsg(text);
    if (canOpenSettings && lm && lm.openSettings) {
      showBtn('Open settings', function() { lm.openSettings(); });
    } else {
      showBtn('Try again', request);
    }
  }

  function submit(lat, lng) {
    clearWatchdog();
    titleEl.textContent = 'Share your location';
    setBusy('Pinning your cheers…');
    btnEl.disabled = true;
    fetch('/api/submit-location?chat_id=' + encodeURIComponent(cid), {
      method: 'POST',
      headers: Object.assign(authHeaders(), { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ lat: lat, lng: lng })
    }).then(function(r) {
      if (r.ok) {
        titleEl.textContent = 'Pinned! 🎉';
        setMsg('Your cheers is on the map.');
        hideBtn();
        if (tg && tg.HapticFeedback) { try { tg.HapticFeedback.notificationOccurred('success'); } catch (e) {} }
        setTimeout(function() { if (tg && tg.close) tg.close(); }, 1400);
        return;
      }
      if (r.status === 409) { showError('We couldn’t find a cheers waiting for a location. Send a new video, then ' + ATTACH_HINT); return; }
      // 401 = the Mini App reached us without a valid Telegram session (initData); 403 = the session
      // is valid but you're not seen as a member of this chat. Kept distinct so the message matches.
      if (r.status === 401) { showError('Couldn’t verify your Telegram session. Reopen this from the 📍 Share location button in the chat, or ' + ATTACH_HINT); return; }
      if (r.status === 403) { showError('You don’t appear to be a member of this group, so we can’t pin here. ' + ATTACH_HINT); return; }
      showError('Something went wrong saving your pin. Please try again.');
    }).catch(function() {
      showError('Network error. Please try again.');
    });
  }

  function request() {
    titleEl.textContent = 'Share your location';
    setBusy('Getting your location…');
    hideBtn();
    if (!lm) { showError('Your Telegram version doesn’t support one-tap location — ' + ATTACH_HINT); return; }
    function getLoc() {
      if (!lm.isLocationAvailable) { showError('Location isn’t available on this device — ' + ATTACH_HINT); return; }
      // Generous timeout (covers the native permission prompt) so a client that never fires the
      // callback can't leave the screen stuck on "Getting your location…".
      clearWatchdog();
      watchdog = setTimeout(function() { watchdog = null; showError('Location request timed out — ' + ATTACH_HINT); }, 45000);
      try {
        lm.getLocation(function(loc) {
          clearWatchdog();
          if (!loc) {
            if (lm.isAccessRequested && !lm.isAccessGranted) {
              showError('Location access is turned off. Enable it in settings, then try again.', true);
            } else {
              showError('Couldn’t read your location. Please try again.');
            }
            return;
          }
          submit(loc.latitude, loc.longitude);
        });
      } catch (e) {
        showError('Couldn’t read your location — ' + ATTACH_HINT);
      }
    }
    if (lm.isInited) {
      getLoc();
    } else {
      // init() returns without calling back on unsupported clients; guard with a watchdog.
      clearWatchdog();
      watchdog = setTimeout(function() { watchdog = null; showError('Location isn’t responding here — ' + ATTACH_HINT); }, 10000);
      lm.init(function() { clearWatchdog(); getLoc(); });
    }
  }

  if (!tg || !tg.isVersionAtLeast || !tg.isVersionAtLeast('8.0') || !lm) {
    setMsg('Your Telegram app doesn’t support one-tap location yet — please ' + ATTACH_HINT);
    hideBtn();
    return;
  }
  // Auto-start the moment the Mini App opens — no in-app tap needed. For a user who already granted
  // access this is silent (open → capture → close); first time, Telegram shows its own native
  // permission prompt. The button only reappears via showError() as a retry / open-settings action.
  request();
}
