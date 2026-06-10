// Escape arbitrary text for safe insertion into HTML (XSS guard for every dynamic value).
export function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

// A pin's marker glyph: its emoji, else the uppercased first letter of the user's name.
export function placeholderGlyph(p) {
  return p.pin_emoji ? esc(p.pin_emoji) : esc(((p.user_name || '?').trim()[0] || '?').toUpperCase());
}

export function relTime(iso) {
  var then = new Date(iso).getTime();
  if (isNaN(then)) return '';
  var s = Math.floor((Date.now() - then) / 1000);
  if (s < 60) return 'just now';
  var m = Math.floor(s / 60); if (m < 60) return m + 'm ago';
  var h = Math.floor(m / 60); if (h < 24) return h + 'h ago';
  var d = Math.floor(h / 24); if (d < 7) return d + 'd ago';
  var w = Math.floor(d / 7); if (w < 5) return w + 'w ago';
  return new Date(iso).toLocaleDateString();
}

export function extractChatIdFromStartParam(sp) {
  if (!sp || !sp.startsWith('c')) return null;
  var rest = sp.slice(1);
  var sep = rest.indexOf('_');
  return sep > 0 ? rest.slice(0, sep) : rest;
}
