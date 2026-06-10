import { PALETTE } from './config.js';

// Stable per-user colour assignment (cache keyed by user id; honours an explicit pin_color).
var userColors = {};

export function getColor(uid, customColor) {
  if (customColor) return customColor;
  if (!userColors[uid]) userColors[uid] = PALETTE[Math.abs(uid) % PALETTE.length];
  return userColors[uid];
}

function parseHexColor(col) {
  if (typeof col !== 'string') return null;
  var s = col.trim().replace(/^#/, '');
  if (/^[0-9a-fA-F]{3}$/.test(s)) return [parseInt(s[0] + s[0], 16), parseInt(s[1] + s[1], 16), parseInt(s[2] + s[2], 16)];
  if (/^[0-9a-fA-F]{6}$/.test(s)) return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
  return null;
}

// sRGB <-> OKLab (Björn Ottosson). Averaging in OKLab (a perceptual space) keeps blends of
// different hues vivid instead of the muddy/dark result you get from averaging raw sRGB.
function srgbToLinear(c) { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
function linearToSrgb(c) { var v = c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055; return Math.round(Math.max(0, Math.min(1, v)) * 255); }

function rgbToOklab(rgb) {
  var r = srgbToLinear(rgb[0]), g = srgbToLinear(rgb[1]), b = srgbToLinear(rgb[2]);
  var l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  var m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  var s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
  ];
}

function oklabToRgb(lab) {
  var l_ = lab[0] + 0.3963377774 * lab[1] + 0.2158037573 * lab[2];
  var m_ = lab[0] - 0.1055613458 * lab[1] - 0.0638541728 * lab[2];
  var s_ = lab[0] - 0.0894841775 * lab[1] - 1.2914855480 * lab[2];
  var l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
  return [
    linearToSrgb(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    linearToSrgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    linearToSrgb(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s),
  ];
}

// Perceptual mean of every pin's user colour (count-weighted) for the count badge.
// Returns an [r,g,b] array, or null if no colour parsed (caller falls back to the CSS default).
export function averageColor(colors) {
  var L = 0, A = 0, B = 0, n = 0;
  colors.forEach(function(col) {
    var rgb = parseHexColor(col);
    if (!rgb) return;
    var lab = rgbToOklab(rgb);
    L += lab[0]; A += lab[1]; B += lab[2]; n++;
  });
  return n ? oklabToRgb([L / n, A / n, B / n]) : null;
}
