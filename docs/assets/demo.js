/*
 * Hindsight interactive demo.
 *
 * A small 2D sculpt toy that records its history the way the Blender add-on does:
 *  - every stroke is one step that stores ONLY the points it changed,
 *  - each stored value is the XOR of the old and new float bits, so one record
 *    turns the old shape into the new one and back again,
 *  - scrubbing folds every step between here and the target into one buffer and
 *    writes the shape once,
 *  - a new stroke after stepping back drops the steps ahead (a new branch).
 * The Morph History brush paints a marked earlier step back onto the brushed area,
 * and Movie Mode replays the session without changing it.
 *
 * Plain JavaScript, no libraries. Builds itself inside <div id="hs-demo">.
 */
(function () {
  'use strict';

  var N = 384;              // points on the outline
  var CAP = 10000;          // step cap shown on the counter (the add-on's default)
  var VW = 470, VH = 430;   // model-space box that must stay in view
  var VIEW_CY = 22;         // model y at the centre of the view
  var FLOOR_Y = 214;        // model y of the grid floor
  var BX = 330, BY0 = -270, BY1 = 262; // points are kept inside this box
  var FRAME_MS = 1000 / 60;
  var KEY_LEVELS = 21, RIM_LEVELS = 13;

  var TOOL_INFO = {
    grab: { label: 'Push/Pull', hint: 'Drag across the outline: drag outwards to pull clay out, inwards to push it in.' },
    inflate: { label: 'Inflate', hint: 'Drag along the outline to swell the surface outwards.' },
    deflate: { label: 'Deflate', hint: 'Drag along the outline to shrink the surface inwards.' },
    smooth: { label: 'Smooth', hint: 'Drag over bumps to relax them.' },
    morph: { label: 'Morph History', hint: 'Paints the morph target step back onto only the area you brush. Each morph stroke is a new step.' }
  };
  var TOOL_ORDER = ['grab', 'inflate', 'deflate', 'smooth', 'morph'];

  var ICONS = {
    grab: '<path d="M3.5 18.5c4 0 6.5-1.6 8.6-5.4"/><path d="M12.1 13.1 19 6.2"/><path d="M13.6 5.6h5.9v5.9"/>',
    inflate: '<circle cx="12" cy="12" r="3.6"/><path d="M12 2.8v3.4M12 17.8v3.4M2.8 12h3.4M17.8 12h3.4M5.5 5.5l2.3 2.3M16.2 16.2l2.3 2.3M18.5 5.5l-2.3 2.3M7.8 16.2l-2.3 2.3"/>',
    deflate: '<circle cx="12" cy="12" r="8.4" stroke-dasharray="2.6 2.6"/><path d="M12 6.6v4M9.8 8.6 12 10.8l2.2-2.2M12 17.4v-4M9.8 15.4 12 13.2l2.2 2.2"/>',
    smooth: '<path d="M3 9.2c3-3.2 6 3.2 9 0s6-3.2 9 0"/><path d="M3 15.4c3-1.2 6 1.2 9 0s6-1.2 9 0"/>',
    morph: '<path d="M4 12.5a8 8 0 1 0 2.4-6.1"/><path d="M3.6 3.2v4.6h4.6"/><path d="M12 8v4.3l3 1.9"/>',
    play: '<path d="M7.5 4.8v14.4L19 12z" fill="currentColor" stroke="none"/>',
    pause: '<rect x="6.5" y="5" width="3.8" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="13.7" y="5" width="3.8" height="14" rx="1" fill="currentColor" stroke="none"/>',
    undo: '<path d="M9 14.5 4 9.5l5-5"/><path d="M4 9.5h10.5a5.25 5.25 0 0 1 0 10.5H11"/>',
    redo: '<path d="M15 14.5l5-5-5-5"/><path d="M20 9.5H9.5a5.25 5.25 0 0 0 0 10.5H13"/>',
    reset: '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5"/><path d="M3.5 3.5v5h5"/>',
    hand: '<path d="M8.5 11.5V5.2a1.6 1.6 0 0 1 3.2 0v5.3M11.7 10.2V3.9a1.6 1.6 0 0 1 3.2 0v6.4M14.9 10.4V5.6a1.6 1.6 0 0 1 3.2 0v7.6c0 4.2-2.6 7.6-6.8 7.6-2.6 0-4.2-1.1-5.6-3.2l-2.6-4.1a1.6 1.6 0 0 1 2.6-1.8l2.1 2.2"/>'
  };

  function svg(name, cls) {
    return '<svg class="' + (cls || 'hsd-ico') + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + ICONS[name] + '</svg>';
  }

  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }

  function fmtInt(n) { return Math.round(n).toLocaleString('en-US'); }

  function fmtBytes(b) {
    if (b < 1024) return fmtInt(b) + ' bytes';
    if (b < 1024 * 1024) return (b / 1024).toFixed(b < 10240 ? 1 : 0) + ' KB';
    return (b / 1048576).toFixed(1) + ' MB';
  }

  // Seeded PRNG (mulberry32) so the preloaded session is the same on every visit.
  function rng(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function boot() {
    var host = document.getElementById('hs-demo');
    if (!host || host.getAttribute('data-hsd-ready')) return;
    host.setAttribute('data-hsd-ready', '1');
    createDemo(host);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  function createDemo(host) {
    var mqReduce = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
    function reducedMotion() { return !!(mqReduce && mqReduce.matches); }

    /* ------------------------------------------------------------------ DOM */

    var toolButtons = '';
    for (var ti = 0; ti < TOOL_ORDER.length; ti++) {
      var tk = TOOL_ORDER[ti];
      toolButtons += '<button type="button" class="hsd-tool" role="radio" data-tool="' + tk + '" aria-checked="false"' +
        (tk === 'morph' ? ' aria-describedby="hsd-morph-note"' : '') + '>' + svg(tk) +
        '<span class="hsd-tool-label">' + (tk === 'morph' ? 'Morph<span class="hsd-hide-sm"> History</span>' : TOOL_INFO[tk].label) + '</span></button>';
    }

    host.innerHTML =
      '<div class="hsd-root" tabindex="-1">' +
        '<div class="hsd-bar">' +
          '<div class="hsd-tools" role="radiogroup" aria-label="Brush">' + toolButtons + '</div>' +
          '<div class="hsd-sliders">' +
            '<label class="hsd-slider"><span class="hsd-slider-name">Radius</span>' +
              '<input class="hsd-range" type="range" min="12" max="110" step="1" value="44" data-k="radius" aria-label="Brush radius">' +
              '<output class="hsd-slider-val">44</output></label>' +
            '<label class="hsd-slider"><span class="hsd-slider-name">Strength</span>' +
              '<input class="hsd-range" type="range" min="5" max="100" step="1" value="60" data-k="strength" aria-label="Brush strength">' +
              '<output class="hsd-slider-val">0.60</output></label>' +
          '</div>' +
        '</div>' +
        '<div class="hsd-body">' +
          '<div class="hsd-col">' +
            '<div class="hsd-stage">' +
              '<canvas class="hsd-canvas" tabindex="0" role="img" aria-label="Sculpt viewport. Drag on the clay shape to sculpt it with the selected brush."></canvas>' +
              '<div class="hsd-hud" aria-hidden="true"><span class="hsd-chip">Sculpt Mode</span><span class="hsd-steplabel"></span></div>' +
              '<div class="hsd-flag" hidden></div>' +
              '<div class="hsd-toast" role="status" aria-live="polite" aria-atomic="true"></div>' +
              '<div class="hsd-hint" aria-hidden="true">' + svg('hand', 'hsd-hint-ico') + '<span>Try it: drag on the shape</span></div>' +
              '<div class="hsd-scrub">' +
                '<button type="button" class="hsd-dot" aria-haspopup="menu" aria-expanded="false" aria-controls="hsd-menu" aria-label="History Menu"><span class="hsd-dot-core"></span></button>' +
                '<div class="hsd-track" role="slider" tabindex="0" aria-label="Undo history scrubber" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0">' +
                  '<div class="hsd-rail"><div class="hsd-fill"></div><div class="hsd-ticks"></div></div>' +
                  '<div class="hsd-mtick" hidden><span>Morph target</span></div>' +
                  '<div class="hsd-handle"></div>' +
                '</div>' +
                '<span class="hsd-count" aria-hidden="true">0 / ' + CAP + '</span>' +
              '</div>' +
              '<div class="hsd-menu" id="hsd-menu" role="menu" aria-label="History Menu" hidden>' +
                '<div class="hsd-menu-title" aria-hidden="true">History</div>' +
                '<button type="button" role="menuitem" tabindex="-1" class="hsd-mi" data-act="target"><span class="hsd-mi-ico hsd-mi-violet"></span><span class="hsd-mi-text">Set Morph Target Here</span><span class="hsd-mi-aux" data-aux="target"></span></button>' +
                '<button type="button" role="menuitem" tabindex="-1" class="hsd-mi" data-act="morph"><span class="hsd-mi-ico">' + svg('morph') + '</span><span class="hsd-mi-text">Morph History Brush</span></button>' +
                '<div class="hsd-mi-sep" role="separator"></div>' +
                '<button type="button" role="menuitem" tabindex="-1" class="hsd-mi" data-act="past"><span class="hsd-mi-ico"></span><span class="hsd-mi-text">Delete Past Undo Steps</span></button>' +
                '<button type="button" role="menuitem" tabindex="-1" class="hsd-mi" data-act="reset"><span class="hsd-mi-ico"></span><span class="hsd-mi-text">Reset Undo History</span></button>' +
                '<button type="button" role="menuitem" tabindex="-1" class="hsd-mi" data-act="toggle"><span class="hsd-mi-ico"></span><span class="hsd-mi-text">Turn Off Undo History</span></button>' +
              '</div>' +
            '</div>' +
            '<div class="hsd-under">' +
              '<div class="hsd-btns">' +
                '<button type="button" class="hsd-btn" data-act="undo" aria-keyshortcuts="Control+Z Meta+Z">' + svg('undo') + '<span>Undo</span></button>' +
                '<button type="button" class="hsd-btn" data-act="redo" aria-keyshortcuts="Control+Shift+Z Meta+Shift+Z">' + svg('redo') + '<span>Redo</span></button>' +
              '</div>' +
              '<p class="hsd-tip"></p>' +
              '<button type="button" class="hsd-btn hsd-btn-ghost" data-act="resetall">' + svg('reset') + '<span>Reset</span></button>' +
            '</div>' +
            '<p class="hsd-note" id="hsd-morph-note"></p>' +
          '</div>' +
          '<aside class="hsd-side" aria-label="Undo History panel">' +
            '<div class="hsd-side-tab" aria-hidden="true">Undo History</div>' +
            '<section class="hsd-panel" aria-labelledby="hsd-movie-h">' +
              '<h3 class="hsd-ptitle" id="hsd-movie-h">Movie Mode</h3>' +
              '<div class="hsd-movie">' +
                '<button type="button" class="hsd-play" aria-label="Play the session as a timelapse">' + svg('play', 'hsd-ico hsd-ico-play') + svg('pause', 'hsd-ico hsd-ico-pause') + '<span class="hsd-play-text">Play</span></button>' +
                '<div class="hsd-fps">' +
                  '<label class="hsd-fps-label" for="hsd-fps-input">Frames per Step</label>' +
                  '<div class="hsd-stepper">' +
                    '<button type="button" class="hsd-step-btn" data-d="-1" aria-label="Fewer frames per step">&minus;</button>' +
                    '<input id="hsd-fps-input" class="hsd-fps-input" type="number" min="1" max="30" step="1" value="4" inputmode="numeric">' +
                    '<button type="button" class="hsd-step-btn" data-d="1" aria-label="More frames per step">+</button>' +
                  '</div>' +
                '</div>' +
              '</div>' +
              '<div class="hsd-prog" aria-hidden="true"><div class="hsd-prog-fill"></div></div>' +
              '<p class="hsd-small hsd-movie-info"></p>' +
            '</section>' +
            '<section class="hsd-panel" aria-labelledby="hsd-mem-h">' +
              '<h3 class="hsd-ptitle" id="hsd-mem-h">Memory</h3>' +
              '<p class="hsd-read hsd-read-step"></p>' +
              '<div class="hsd-meter" aria-hidden="true">' +
                '<div class="hsd-meter-row"><span class="hsd-meter-k">Full copy</span><div class="hsd-meter-bar"><i class="hsd-meter-full" style="width:100%"></i></div></div>' +
                '<div class="hsd-meter-row"><span class="hsd-meter-k">Stored</span><div class="hsd-meter-bar"><i class="hsd-meter-step"></i></div><b class="hsd-meter-pct hsd-pct-step"></b></div>' +
              '</div>' +
              '<p class="hsd-read hsd-read-total"></p>' +
              '<div class="hsd-meter" aria-hidden="true">' +
                '<div class="hsd-meter-row"><span class="hsd-meter-k">Full copies</span><div class="hsd-meter-bar"><i class="hsd-meter-full" style="width:100%"></i></div></div>' +
                '<div class="hsd-meter-row"><span class="hsd-meter-k">Stored</span><div class="hsd-meter-bar"><i class="hsd-meter-total"></i></div><b class="hsd-meter-pct hsd-pct-total"></b></div>' +
              '</div>' +
              '<p class="hsd-small hsd-xor-note">Each stored value is the XOR of the old and new float bits, so the same record works forwards and backwards.</p>' +
            '</section>' +
          '</aside>' +
        '</div>' +
        '<div class="hsd-sr" aria-live="polite" aria-atomic="true"></div>' +
      '</div>';

    function q(sel) { return host.querySelector(sel); }
    var root = q('.hsd-root');
    var stage = q('.hsd-stage');
    var canvas = q('.hsd-canvas');
    var ctx = canvas.getContext('2d');
    var hint = q('.hsd-hint');
    var hudLabel = q('.hsd-steplabel');
    var flag = q('.hsd-flag');
    var toastEl = q('.hsd-toast');
    var scrub = q('.hsd-scrub');
    var dot = q('.hsd-dot');
    var track = q('.hsd-track');
    var rail = q('.hsd-rail');
    var fillEl = q('.hsd-fill');
    var ticksEl = q('.hsd-ticks');
    var handle = q('.hsd-handle');
    var mtick = q('.hsd-mtick');
    var countEl = q('.hsd-count');
    var menu = q('.hsd-menu');
    var menuItems = menu.querySelectorAll('.hsd-mi');
    var toolEls = host.querySelectorAll('.hsd-tool');
    var ranges = host.querySelectorAll('.hsd-range');
    var undoBtn = q('[data-act="undo"]');
    var redoBtn = q('[data-act="redo"]');
    var resetAllBtn = q('[data-act="resetall"]');
    var tipEl = q('.hsd-tip');
    var noteEl = q('.hsd-note');
    var playBtn = q('.hsd-play');
    var playText = q('.hsd-play-text');
    var fpsInput = q('.hsd-fps-input');
    var progFill = q('.hsd-prog-fill');
    var movieInfo = q('.hsd-movie-info');
    var readStep = q('.hsd-read-step');
    var readTotal = q('.hsd-read-total');
    var meterStep = q('.hsd-meter-step');
    var meterTotal = q('.hsd-meter-total');
    var pctStep = q('.hsd-pct-step');
    var pctTotal = q('.hsd-pct-total');
    var srEl = q('.hsd-sr');

    /* ------------------------------------------------------------ geometry */

    var pos = new Float32Array(2 * N);          // the live shape
    var posBits = new Uint32Array(pos.buffer);
    var base = new Float32Array(2 * N);         // the shape at the current step (what history knows)
    var baseBits = new Uint32Array(base.buffer);
    var initial = new Float32Array(2 * N);      // the starting blob
    var scratch = new Float32Array(2 * N);      // neighbour copy for smooth / inflate
    var morphBuf = new Float32Array(2 * N);     // the shape at the morph target step
    var acc = new Uint32Array(2 * N);           // scrub accumulator: all XORs folded here, then written once
    var touched = new Uint16Array(N);
    var mark = new Uint8Array(N);
    var keyLev = new Uint8Array(N);
    var rimLev = new Uint8Array(N);

    (function makeBlob() {
      for (var i = 0; i < N; i++) {
        var t = (i / N) * Math.PI * 2;
        var c = Math.cos(t), s = Math.sin(t);
        var egg = 1 + 0.07 * -s;                  // a little wider at the top
        var wob = 1 + 0.012 * Math.sin(3 * t + 0.6);
        initial[2 * i] = 132 * c * egg * wob;
        initial[2 * i + 1] = 150 * s * wob - 8;
      }
    })();
    pos.set(initial);
    base.set(initial);

    /* ------------------------------------------------------------- history */

    var steps = [];        // { idx: Uint16Array|null, xor: Uint32Array, stored: k, bytes, label }
    var cursor = 0;        // number of applied steps
    var morphTarget = -1;  // a cursor position, or -1
    var historyOn = true;
    var offDirty = false;  // changes made while history was off
    var totalBytes = 0;

    function changedCount() {
      var k = 0;
      for (var j = 0; j < 2 * N; j += 2) {
        if (posBits[j] !== baseBits[j] || posBits[j + 1] !== baseBits[j + 1]) k++;
      }
      return k;
    }

    // Record the difference between the live shape and the current step as one new step.
    function commit(label) {
      var k = changedCount();
      if (!k) return null;
      var dropped = 0;
      if (cursor < steps.length) {             // new branch: the future goes
        dropped = steps.length - cursor;
        for (var d = cursor; d < steps.length; d++) totalBytes -= steps[d].bytes;
        steps.length = cursor;
        if (morphTarget > cursor) morphTarget = -1;
      }
      var dense = k * 10 >= N * 8;              // never store more than a full copy would take
      var idx = dense ? null : new Uint16Array(k);
      var xor = new Uint32Array(dense ? 2 * N : 2 * k);
      var w = 0;
      for (var i = 0; i < N; i++) {
        var a = 2 * i, x0 = posBits[a] ^ baseBits[a], x1 = posBits[a + 1] ^ baseBits[a + 1];
        if (dense) { xor[a] = x0; xor[a + 1] = x1; continue; }
        if (x0 !== 0 || x1 !== 0) { idx[w] = i; xor[2 * w] = x0; xor[2 * w + 1] = x1; w++; }
      }
      baseBits.set(posBits);
      var rec = {
        idx: idx, xor: xor, stored: dense ? N : k, changed: k,
        bytes: xor.byteLength + (idx ? idx.byteLength : 0), label: label
      };
      steps.push(rec);
      totalBytes += rec.bytes;
      if (steps.length > CAP) {
        totalBytes -= steps[0].bytes;
        steps.shift();
        morphTarget = morphTarget > 0 ? morphTarget - 1 : -1;
      }
      cursor = steps.length;
      return { stored: rec.stored, dropped: dropped };
    }

    // Move to step t: fold the XOR records of every step in between, then write once.
    function seek(t) {
      t = clamp(t | 0, 0, steps.length);
      if (t === cursor) return false;
      var a = t < cursor ? t : cursor, b = t < cursor ? cursor : t;
      var nt = 0, s, j, i, rec, idx, x, n;
      for (s = a; s < b; s++) {
        rec = steps[s]; idx = rec.idx; x = rec.xor;
        n = idx ? idx.length : N;
        for (j = 0; j < n; j++) {
          i = idx ? idx[j] : j;
          if (!mark[i]) { mark[i] = 1; touched[nt++] = i; }
          acc[2 * i] ^= x[2 * j];
          acc[2 * i + 1] ^= x[2 * j + 1];
        }
      }
      for (j = 0; j < nt; j++) {
        i = touched[j]; mark[i] = 0;
        posBits[2 * i] ^= acc[2 * i]; posBits[2 * i + 1] ^= acc[2 * i + 1];
        baseBits[2 * i] ^= acc[2 * i]; baseBits[2 * i + 1] ^= acc[2 * i + 1];
        acc[2 * i] = 0; acc[2 * i + 1] = 0;
      }
      cursor = t;
      return true;
    }

    /* -------------------------------------------------------------- brushes */

    var tool = 'grab', radius = 44, strength = 0.6;
    var st = { active: false, id: -1, x: 0, y: 0, tool: 'grab', r: 44, s: 0.6, orient: 1, user: false };

    function orientation() {
      var a = 0;
      for (var i = 0; i < N; i++) {
        var j = i + 1 === N ? 0 : i + 1;
        a += pos[2 * i] * pos[2 * j + 1] - pos[2 * j] * pos[2 * i + 1];
      }
      return a >= 0 ? 1 : -1;
    }

    function dab(kind, cx, cy, dx, dy, R, s) {
      var R2 = R * R, i, x, y, ex, ey, d2, w, f, a, b, tx, ty, len, amt, ax, ay;
      if (kind !== 'grab' && kind !== 'morph') scratch.set(pos);
      for (i = 0; i < N; i++) {
        x = pos[2 * i]; y = pos[2 * i + 1];
        ex = x - cx; ey = y - cy; d2 = ex * ex + ey * ey;
        if (d2 >= R2) continue;
        w = 1 - d2 / R2; f = w * w * s;
        if (kind === 'grab') {
          x += dx * f; y += dy * f;
        } else if (kind === 'morph') {
          x += (morphBuf[2 * i] - x) * f; y += (morphBuf[2 * i + 1] - y) * f;
        } else if (kind === 'smooth') {
          a = i < 2 ? i + N - 2 : i - 2;
          b = i + 2 >= N ? i + 2 - N : i + 2;
          var a1 = i === 0 ? N - 1 : i - 1, b1 = i + 1 === N ? 0 : i + 1;
          ax = (scratch[2 * a] + 2 * scratch[2 * a1] + 2 * scratch[2 * b1] + scratch[2 * b]) / 6;
          ay = (scratch[2 * a + 1] + 2 * scratch[2 * a1 + 1] + 2 * scratch[2 * b1 + 1] + scratch[2 * b + 1]) / 6;
          x += (ax - x) * f * 0.55; y += (ay - y) * f * 0.55;
        } else {
          a = i === 0 ? N - 1 : i - 1; b = i + 1 === N ? 0 : i + 1;
          tx = scratch[2 * b] - scratch[2 * a]; ty = scratch[2 * b + 1] - scratch[2 * a + 1];
          len = Math.sqrt(tx * tx + ty * ty) || 1;
          amt = R * 0.06 * f * (kind === 'deflate' ? -1 : 1) * st.orient / len;
          x += ty * amt; y -= tx * amt;
        }
        pos[2 * i] = clamp(x, -BX, BX);
        pos[2 * i + 1] = clamp(y, BY0, BY1);
      }
    }

    function beginStroke(mx, my, kind, r, s, user) {
      st.active = true; st.x = mx; st.y = my; st.tool = kind; st.r = r; st.s = s; st.user = user;
      st.orient = orientation();
      if (kind !== 'grab') dab(kind, mx, my, 0, 0, r, s);
    }

    function moveStroke(mx, my) {
      var dx = mx - st.x, dy = my - st.y, dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < 1e-4) return;
      var n = Math.ceil(dist / Math.max(1, st.r * 0.2)), sx = dx / n, sy = dy / n, k;
      for (k = 0; k < n; k++) {
        if (st.tool === 'grab') dab('grab', st.x + sx * k, st.y + sy * k, sx, sy, st.r, 0.5 + 0.5 * st.s);
        else dab(st.tool, st.x + sx * (k + 1), st.y + sy * (k + 1), 0, 0, st.r, st.s);
      }
      st.x = mx; st.y = my;
    }

    function endStroke(labelOverride) {
      if (!st.active) return null;
      st.active = false; st.id = -1;
      var res = null;
      if (historyOn) {
        res = commit(labelOverride || TOOL_INFO[st.tool].label);
        if (res) {
          flashStart(false);
          if (morphTarget < 0 && tool === 'morph') {
            tool = 'grab';
            toast('The morph target was on the dropped branch, so it was cleared');
          }
          if (res.dropped) toast('New branch: ' + res.dropped + ' later step' + (res.dropped === 1 ? '' : 's') + ' dropped');
          if (st.tool === 'morph') toast('Morph stroke recorded as step ' + cursor);
        }
      } else if (changedCount()) {
        offDirty = true;
      }
      if (st.user) hideHint();
      uiDirty = true; requestDraw();
      return res;
    }

    /* ------------------------------------------------------- scripted start */

    function scripted() {
      var R = rng(20241014);
      var plan = [
        ['grab', 'Pull: neck', 72, 1, [[-22, 138], [-24, 168], [-26, 196], [-28, 222]]],
        ['grab', 'Pull: jaw', 54, 0.9, [[84, 104], [92, 114], [100, 124], [106, 131]]],
        ['grab', 'Pull: nose', 30, 1, [[127, 6], [140, 12], [153, 19], [166, 27]]],
        ['grab', 'Push: eye socket', 22, 0.9, [[131, -24], [125, -23], [119, -22]]],
        ['grab', 'Pull: brow', 26, 0.8, [[124, -50], [129, -51], [134, -51]]],
        ['inflate', 'Inflate: skull', 74, 0.85, [[-30, -168], [-80, -140], [-118, -98], [-140, -40]]],
        ['grab', 'Pull: chin', 30, 0.9, [[106, 118], [112, 121], [118, 122]]],
        ['deflate', 'Deflate: throat', 46, 0.8, [[78, 150], [62, 162], [44, 176]]],
        ['grab', 'Pull: lips', 15, 0.8, [[124, 46], [128, 47], [132, 48]]],
        ['grab', 'Push: mouth', 12, 0.8, [[126, 63], [122, 63], [118, 63]]],
        ['smooth', 'Smooth: back of head', 58, 0.7, [[-132, -70], [-136, -20], [-126, 40], [-104, 96]]],
        ['smooth', 'Smooth: face', 22, 0.35, [[132, -64], [134, -40], [146, 20], [128, 60], [116, 100]]]
      ];
      for (var p = 0; p < plan.length; p++) {
        var it = plan[p], pts = it[4];
        beginStroke(pts[0][0], pts[0][1], it[0], it[2], it[3], false);
        for (var k = 1; k < pts.length; k++) {
          // a seeded wobble, like a hand-made stroke
          moveStroke(pts[k][0] + (R() - 0.5) * 2.4, pts[k][1] + (R() - 0.5) * 2.4);
        }
        endStroke(it[1]);
      }
      flashStep = -1;
    }

    /* ------------------------------------------------------------ view/draw */

    var cssW = 0, cssH = 0, dpr = 1, scale = 1, ox = 0, oy = 0;
    var bgCanvas = document.createElement('canvas');
    var bgCtx = bgCanvas.getContext('2d');
    var dashGhost = [6, 5];
    var COL = {};
    var keyPal = [[], []], rimPal = [[], []];
    var gradBase, gradSpec, gradShadow;
    var hover = { on: false, x: 0, y: 0 };
    var flashStep = -1, flashT0 = 0, flashHold = false, flashTimer = 0;
    var needDraw = false, uiDirty = true, rafId = 0;

    function cssVar(name, fb) {
      var v = getComputedStyle(root).getPropertyValue(name).trim();
      return v || fb;
    }

    function rgbOf(color) {
      var c = bgCtx;
      c.fillStyle = '#000';
      c.fillStyle = color;
      var v = c.fillStyle;
      if (v.charAt(0) === '#') {
        return parseInt(v.substr(1, 2), 16) + ',' + parseInt(v.substr(3, 2), 16) + ',' + parseInt(v.substr(5, 2), 16);
      }
      var m = v.match(/\d+(\.\d+)?/g);
      return m ? m[0] + ',' + m[1] + ',' + m[2] : '255,255,255';
    }

    function readColors() {
      COL.accent = rgbOf(cssVar('--hsd-accent', '#ff8a3d'));
      COL.accent2 = rgbOf(cssVar('--hsd-accent-2', '#5fd0ff'));
      COL.accent3 = rgbOf(cssVar('--hsd-accent-3', '#b391ff'));
      COL.ok = rgbOf(cssVar('--hsd-ok', '#6ee7a8'));
      COL.toolRing = {
        grab: 'rgba(' + COL.accent + ',0.95)', inflate: 'rgba(' + COL.accent + ',0.95)',
        deflate: 'rgba(' + COL.accent + ',0.95)', smooth: 'rgba(' + COL.accent2 + ',0.95)', morph: 'rgba(' + COL.accent3 + ',1)'
      };
      COL.toolFill = {
        grab: 'rgba(' + COL.accent + ',0.07)', inflate: 'rgba(' + COL.accent + ',0.07)',
        deflate: 'rgba(' + COL.accent + ',0.07)', smooth: 'rgba(' + COL.accent2 + ',0.07)', morph: 'rgba(' + COL.accent3 + ',0.10)'
      };
      COL.dot = 'rgb(' + COL.accent + ')';
      COL.ghost = 'rgba(' + COL.accent3 + ',0.9)';
      COL.ghostFill = 'rgba(' + COL.accent3 + ',0.07)';

      // Key light (warm, upper left) and rim light (cool, right) palettes, built once.
      var widthsA = [0.34, 0.62], q2, v, a;
      for (var w = 0; w < 2; w++) {
        for (q2 = 0; q2 < KEY_LEVELS; q2++) {
          v = (q2 / (KEY_LEVELS - 1)) * 2 - 1;
          if (v >= 0) { a = Math.pow(v, 1.5) * 0.5 * widthsA[w]; keyPal[w][q2] = a < 0.004 ? null : 'rgba(255,243,226,' + a.toFixed(3) + ')'; }
          else { a = Math.pow(-v, 1.05) * 0.78 * widthsA[w]; keyPal[w][q2] = a < 0.004 ? null : 'rgba(38,24,16,' + a.toFixed(3) + ')'; }
        }
        for (q2 = 0; q2 < RIM_LEVELS; q2++) {
          v = q2 / (RIM_LEVELS - 1);
          a = v * v * (w === 0 ? 0.32 : 0.85);
          rimPal[w][q2] = a < 0.004 ? null : 'rgba(' + COL.accent2 + ',' + a.toFixed(3) + ')';
        }
      }

      // Gradients in unit space, mapped onto the shape's bounds when drawn.
      gradBase = ctx.createRadialGradient(0.34, 0.26, 0.02, 0.4, 0.34, 0.92);
      gradBase.addColorStop(0, '#efe1cf');
      gradBase.addColorStop(0.32, '#d2bba3');
      gradBase.addColorStop(0.72, '#a68c75');
      gradBase.addColorStop(1, '#6f5948');
      gradSpec = ctx.createRadialGradient(0.33, 0.2, 0, 0.33, 0.2, 0.3);
      gradSpec.addColorStop(0, 'rgba(255,252,246,0.42)');
      gradSpec.addColorStop(1, 'rgba(255,252,246,0)');
      gradShadow = ctx.createRadialGradient(0.5, 0.5, 0, 0.5, 0.5, 0.5);
      gradShadow.addColorStop(0, 'rgba(0,0,0,0.55)');
      gradShadow.addColorStop(0.6, 'rgba(0,0,0,0.22)');
      gradShadow.addColorStop(1, 'rgba(0,0,0,0)');
    }

    function layout() {
      var w = stage.clientWidth, h = stage.clientHeight;
      var r = Math.min(window.devicePixelRatio || 1, 3);
      if (!w || !h) return;
      if (w === cssW && h === cssH && r === dpr) return;
      cssW = w; cssH = h; dpr = r;
      canvas.width = Math.max(1, Math.round(w * dpr));
      canvas.height = Math.max(1, Math.round(h * dpr));
      var topPad = 44, bottomPad = scrub.offsetHeight + 22;
      var availH = Math.max(80, h - topPad - bottomPad);
      scale = Math.min((w - 24) / VW, availH / VH);
      ox = w / 2;
      oy = topPad + availH / 2 - VIEW_CY * scale;
      dashGhost[0] = 6 / scale; dashGhost[1] = 5 / scale;
      buildBackground();
      needDraw = true;
      draw();
    }

    function buildBackground() {
      var W = canvas.width, H = canvas.height;
      bgCanvas.width = W; bgCanvas.height = H;
      var c = bgCtx;
      c.setTransform(1, 0, 0, 1, 0, 0);
      var g = c.createRadialGradient(W * 0.5, H * 0.38, 0, W * 0.5, H * 0.45, Math.max(W, H) * 0.75);
      g.addColorStop(0, '#22262f');
      g.addColorStop(0.55, '#16191f');
      g.addColorStop(1, '#0d0f13');
      c.fillStyle = g;
      c.fillRect(0, 0, W, H);

      // Perspective grid floor.
      var hy = (oy + FLOOR_Y * scale - 26 * scale) * dpr; // horizon a little above the contact line
      var cx = ox * dpr, i, t, y, sp = 46 * scale * dpr;
      c.save();
      c.beginPath(); c.rect(0, hy, W, H - hy); c.clip();
      c.lineWidth = Math.max(1, dpr);
      c.strokeStyle = 'rgba(255,255,255,0.075)';
      c.beginPath();
      for (i = 1; i < 16; i++) {
        t = i / 15;
        y = hy + (H - hy) * t * t;
        c.moveTo(0, y); c.lineTo(W, y);
      }
      for (i = -24; i <= 24; i++) {
        c.moveTo(cx + i * sp * 0.22, hy);
        c.lineTo(cx + i * sp * 2.2, H + (H - hy));
      }
      c.stroke();
      // Blender-style axis lines.
      c.lineWidth = Math.max(1.2, 1.4 * dpr);
      c.strokeStyle = 'rgba(255,82,82,0.30)';
      y = hy + (H - hy) * 0.16;
      c.beginPath(); c.moveTo(0, y); c.lineTo(W, y); c.stroke();
      c.strokeStyle = 'rgba(140,210,90,0.24)';
      c.beginPath(); c.moveTo(cx, hy); c.lineTo(cx, H); c.stroke();
      // Fade the floor into the horizon.
      var fg = c.createLinearGradient(0, hy, 0, Math.min(H, hy + (H - hy) * 0.7));
      fg.addColorStop(0, 'rgba(19,21,27,1)');
      fg.addColorStop(1, 'rgba(19,21,27,0)');
      c.fillStyle = fg;
      c.fillRect(0, hy, W, H - hy);
      c.restore();
      // Soft horizon glow.
      var hg = c.createLinearGradient(0, hy - 60 * dpr, 0, hy + 40 * dpr);
      hg.addColorStop(0, 'rgba(255,138,61,0)');
      hg.addColorStop(0.6, 'rgba(255,170,110,0.05)');
      hg.addColorStop(1, 'rgba(255,138,61,0)');
      c.fillStyle = hg;
      c.fillRect(0, hy - 60 * dpr, W, 100 * dpr);
      // Vignette.
      var vg = c.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.35, W / 2, H / 2, Math.max(W, H) * 0.78);
      vg.addColorStop(0, 'rgba(0,0,0,0)');
      vg.addColorStop(1, 'rgba(0,0,0,0.45)');
      c.fillStyle = vg;
      c.fillRect(0, 0, W, H);
    }

    function tracePath(buf) {
      var lx = buf[2 * N - 2], ly = buf[2 * N - 1];
      ctx.moveTo((lx + buf[0]) * 0.5, (ly + buf[1]) * 0.5);
      for (var i = 0; i < N; i++) {
        var j = i + 1 === N ? 0 : i + 1;
        ctx.quadraticCurveTo(buf[2 * i], buf[2 * i + 1], (buf[2 * i] + buf[2 * j]) * 0.5, (buf[2 * i + 1] + buf[2 * j + 1]) * 0.5);
      }
      ctx.closePath();
    }

    function strokeRuns(lev, pal, width) {
      ctx.lineWidth = width;
      var i = 0, start, qv, j, nx, pv;
      while (i < N) {
        qv = lev[i]; start = i;
        while (i + 1 < N && lev[i + 1] === qv) i++;
        if (pal[qv] !== null) {
          pv = start === 0 ? N - 1 : start - 1;
          ctx.beginPath();
          ctx.moveTo((pos[2 * pv] + pos[2 * start]) * 0.5, (pos[2 * pv + 1] + pos[2 * start + 1]) * 0.5);
          for (j = start; j <= i; j++) {
            nx = j + 1 === N ? 0 : j + 1;
            ctx.quadraticCurveTo(pos[2 * j], pos[2 * j + 1], (pos[2 * j] + pos[2 * nx]) * 0.5, (pos[2 * j + 1] + pos[2 * nx + 1]) * 0.5);
          }
          ctx.strokeStyle = pal[qv];
          ctx.stroke();
        }
        i++;
      }
    }

    function draw() {
      needDraw = false;
      if (!cssW) return;
      var W = canvas.width, H = canvas.height, i, a, b, tx, ty, len, nx, ny, kd, rd;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.drawImage(bgCanvas, 0, 0);

      // Bounds and lighting levels.
      var minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9, area = 0;
      for (i = 0; i < N; i++) {
        var x = pos[2 * i], y = pos[2 * i + 1];
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
        b = i + 1 === N ? 0 : i + 1;
        area += x * pos[2 * b + 1] - pos[2 * b] * y;
      }
      var orient = area >= 0 ? 1 : -1;
      for (i = 0; i < N; i++) {
        a = i === 0 ? N - 1 : i - 1; b = i + 1 === N ? 0 : i + 1;
        tx = pos[2 * b] - pos[2 * a]; ty = pos[2 * b + 1] - pos[2 * a + 1];
        len = Math.sqrt(tx * tx + ty * ty) || 1;
        nx = orient * ty / len; ny = -orient * tx / len;
        kd = nx * -0.55 + ny * -0.835;           // key light from the upper left
        rd = nx * 0.93 + ny * -0.37;             // rim light from the right
        keyLev[i] = Math.round((kd + 1) * 0.5 * (KEY_LEVELS - 1));
        rimLev[i] = rd > 0 ? Math.round(rd * (RIM_LEVELS - 1)) : 0;
      }

      var k = dpr * scale, bw = maxX - minX, bh = maxY - minY;

      // Contact shadow on the floor.
      var sw = bw * 1.05, sh = 26;
      ctx.setTransform(k * sw, 0, 0, k * sh, dpr * ox + k * ((minX + maxX) * 0.5 - sw * 0.5), dpr * oy + k * (Math.min(maxY, FLOOR_Y) - sh * 0.5 + 4));
      ctx.fillStyle = gradShadow;
      ctx.fillRect(0, 0, 1, 1);

      // Clay body.
      ctx.save();
      ctx.setTransform(k, 0, 0, k, dpr * ox, dpr * oy);
      ctx.beginPath();
      tracePath(pos);
      ctx.clip();
      ctx.setTransform(k * bw, 0, 0, k * bh, dpr * ox + k * minX, dpr * oy + k * minY);
      ctx.fillStyle = gradBase;
      ctx.fillRect(-0.05, -0.05, 1.1, 1.1);
      ctx.fillStyle = gradSpec;
      ctx.fillRect(0, 0, 1, 1);
      ctx.setTransform(k, 0, 0, k, dpr * ox, dpr * oy);
      ctx.lineJoin = 'round';
      ctx.lineCap = 'butt';
      strokeRuns(keyLev, keyPal[0], 46);
      strokeRuns(keyLev, keyPal[1], 18);
      strokeRuns(rimLev, rimPal[0], 16);
      strokeRuns(rimLev, rimPal[1], 5);
      ctx.restore();

      ctx.setTransform(k, 0, 0, k, dpr * ox, dpr * oy);
      ctx.beginPath();
      tracePath(pos);
      ctx.lineWidth = 1.3 / scale;
      ctx.strokeStyle = 'rgba(18,10,6,0.6)';
      ctx.stroke();

      // Morph target ghost.
      if (morphTarget >= 0 && (tool === 'morph' || menuOpen)) {
        ctx.beginPath();
        tracePath(morphBuf);
        ctx.fillStyle = COL.ghostFill;
        ctx.fill();
        ctx.setLineDash(dashGhost);
        ctx.lineWidth = 1.6 / scale;
        ctx.strokeStyle = COL.ghost;
        ctx.stroke();
        ctx.setLineDash(EMPTY_DASH);
      }

      // The points stored by the current step.
      var fa = flashAlpha();
      if (fa > 0 && flashStep >= 0 && flashStep < steps.length && flashStep === cursor - 1) {
        var rec = steps[flashStep], idx = rec.idx, n = idx ? idx.length : N, xr = rec.xor, rr = 2.4 / scale, p;
        ctx.globalAlpha = fa;
        ctx.beginPath();
        for (i = 0; i < n; i++) {
          p = idx ? idx[i] : i;
          if (!idx && xr[2 * i] === 0 && xr[2 * i + 1] === 0) continue;
          ctx.moveTo(pos[2 * p] + rr, pos[2 * p + 1]);
          ctx.arc(pos[2 * p], pos[2 * p + 1], rr, 0, 6.2832);
        }
        ctx.lineWidth = 1.6 / scale;
        ctx.strokeStyle = 'rgba(20,10,4,0.7)';
        ctx.stroke();
        ctx.fillStyle = COL.dot;
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      // Brush cursor.
      if ((hover.on || st.active) && !playing) {
        var hx = st.active ? st.x : hover.x, hy = st.active ? st.y : hover.y;
        var r = (st.active ? st.r : radius);
        var t = tool === 'morph' && morphTarget < 0 ? 'grab' : tool;
        ctx.beginPath();
        ctx.arc(hx, hy, r, 0, 6.2832);
        ctx.fillStyle = COL.toolFill[t];
        ctx.fill();
        ctx.lineWidth = 1.5 / scale;
        ctx.strokeStyle = COL.toolRing[t];
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(hx, hy, r * 0.5 * strength + 1 / scale, 0, 6.2832);
        ctx.lineWidth = 1 / scale;
        ctx.stroke();
      }
      ctx.setTransform(1, 0, 0, 1, 0, 0);
    }
    var EMPTY_DASH = [];

    function flashAlpha() {
      if (flashStep < 0) return 0;
      if (flashHold) return 1;
      var e = performance.now() - flashT0;
      if (reducedMotion()) return e < 1600 ? 1 : 0;
      if (e < 700) return 1;
      return Math.max(0, 1 - (e - 700) / 900);
    }

    function flashStart(hold) {
      flashStep = cursor - 1;
      flashHold = hold;
      flashT0 = performance.now();
      if (reducedMotion() && !hold) {
        clearTimeout(flashTimer);
        flashTimer = setTimeout(requestDraw, 1650);
      }
    }

    function requestDraw() {
      needDraw = true;
      if (!rafId) rafId = requestAnimationFrame(frame);
    }

    var lastFrameT = 0;
    function frame(now) {
      rafId = 0;
      if (playing) advancePlayback(now);
      if (uiDirty) updateUI();
      var animating = flashStep >= 0 && !flashHold && !reducedMotion() && flashAlpha() > 0;
      if (needDraw || animating) draw();
      if ((playing || animating) && !rafId) rafId = requestAnimationFrame(frame);
    }

    /* ------------------------------------------------------------ playback */

    var playing = false, framesPerStep = 4, playAcc = 0, playLast = 0, resumeMid = false;

    function play() {
      if (playing || !steps.length || !historyOn) return false;
      endStroke();
      closeMenu(false);
      if (!resumeMid || cursor >= steps.length) seek(0);
      resumeMid = false;
      playing = true; playAcc = 0; playLast = 0;
      flashStep = -1;
      root.classList.add('hsd-playing');
      announce('Movie Mode playing');
      uiDirty = true; requestDraw();
      return true;
    }

    function pause(finished) {
      if (!playing) return;
      playing = false;
      resumeMid = !finished;
      root.classList.remove('hsd-playing');
      announce(finished ? 'Movie Mode finished at step ' + cursor : 'Paused at step ' + cursor);
      uiDirty = true; requestDraw();
    }

    function advancePlayback(now) {
      if (!playLast) playLast = now;
      playAcc += Math.min(now - playLast, 250);
      playLast = now;
      var stepMs = framesPerStep * FRAME_MS;
      while (playAcc >= stepMs - 0.5) {
        playAcc -= stepMs;
        if (cursor >= steps.length) { pause(true); flashStart(false); return; }
        seek(cursor + 1);
        flashStep = cursor - 1; flashHold = true;
        uiDirty = true; needDraw = true;
      }
    }

    function setFps(n) {
      n = clamp(Math.round(+n || 4), 1, 30);
      framesPerStep = n;
      if (String(fpsInput.value) !== String(n)) fpsInput.value = n;
      uiDirty = true; requestDraw();
      return n;
    }

    /* ------------------------------------------------------------ actions */

    function gotoStep(t, opts) {
      if (!historyOn) return false;
      if (playing && !(opts && opts.fromPlayback)) pause(false);
      endStroke();
      resumeMid = false;
      var moved = seek(t);
      if (moved) {
        flashStart(!!(opts && opts.hold));
        uiDirty = true; requestDraw();
      }
      return moved;
    }

    function undo() { if (cursor > 0) gotoStep(cursor - 1); }
    function redo() { if (cursor < steps.length) gotoStep(cursor + 1); }

    function setMorphTarget() {
      if (!historyOn) return false;
      endStroke();
      morphTarget = cursor;
      morphBuf.set(pos);
      toast('Morph target set at step ' + cursor);
      uiDirty = true; requestDraw();
      return true;
    }

    function setTool(t, viaMenu) {
      if (!TOOL_INFO[t]) return;
      if (t === 'morph' && morphTarget < 0) {
        toast('Set a morph target first: History Menu, then Set Morph Target Here');
        dot.classList.remove('hsd-nudge'); void dot.offsetWidth; dot.classList.add('hsd-nudge');
        return;
      }
      tool = t;
      if (viaMenu) toast('Morph History brush selected');
      uiDirty = true; requestDraw();
    }

    function deletePast() {
      if (!historyOn || cursor === 0) return;
      if (playing) pause(false);
      endStroke();
      var n = cursor;
      for (var i = 0; i < n; i++) totalBytes -= steps[i].bytes;
      steps.splice(0, n);
      morphTarget = morphTarget >= n ? morphTarget - n : -1;
      cursor = 0;
      flashStep = -1;
      if (morphTarget < 0 && tool === 'morph') tool = 'grab';
      toast('Deleted ' + n + ' past step' + (n === 1 ? '' : 's'));
      uiDirty = true; requestDraw();
    }

    function resetHistory() {
      if (playing) pause(false);
      endStroke();
      steps.length = 0; totalBytes = 0; cursor = 0; morphTarget = -1; flashStep = -1;
      base.set(pos); offDirty = false; resumeMid = false;
      if (tool === 'morph') tool = 'grab';
      toast('Undo history reset');
      uiDirty = true; requestDraw();
    }

    function toggleHistory() {
      if (playing) pause(false);
      endStroke();
      if (historyOn) {
        historyOn = false; offDirty = false;
        toast('Undo history off: strokes are not recorded');
      } else {
        historyOn = true;
        var res = commit('Changes While Off');
        offDirty = false;
        if (res) { flashStart(false); toast('Changes made while off recorded as step ' + cursor); }
        else toast('Undo history on');
      }
      uiDirty = true; requestDraw();
    }

    function resetAll() {
      if (playing) pause(false);
      endStroke();
      pos.set(initial); base.set(initial);
      steps.length = 0; totalBytes = 0; cursor = 0; morphTarget = -1; flashStep = -1;
      historyOn = true; offDirty = false; resumeMid = false;
      if (tool === 'morph') tool = 'grab';
      hint.classList.remove('hsd-hint-off');
      toast('Back to the starting blob');
      uiDirty = true; requestDraw();
    }

    /* ----------------------------------------------------------------- UI */

    var toastTimer = 0;
    function toast(text) {
      toastEl.textContent = text;
      toastEl.classList.add('hsd-toast-on');
      clearTimeout(toastTimer);
      toastTimer = setTimeout(function () { toastEl.classList.remove('hsd-toast-on'); }, 2600);
    }

    var srTimer = 0, srPending = '';
    function announce(text) {
      srPending = text;
      if (srTimer) return;
      srTimer = setTimeout(function () { srTimer = 0; srEl.textContent = srPending; }, 700);
    }

    function hideHint() { hint.classList.add('hsd-hint-off'); }

    var lastAnnounced = -1;
    function updateUI() {
      uiDirty = false;
      var len = steps.length, frac = len ? cursor / len : 0;
      countEl.textContent = historyOn ? cursor + ' / ' + CAP : 'Off';
      track.setAttribute('aria-valuemax', String(len));
      track.setAttribute('aria-valuenow', String(cursor));
      track.setAttribute('aria-valuetext', historyOn ? 'Step ' + cursor + ' of ' + len : 'Undo history is off');
      var locked = playing || !historyOn || !len;
      track.setAttribute('aria-disabled', locked ? 'true' : 'false');
      scrub.classList.toggle('hsd-locked', playing);
      scrub.classList.toggle('hsd-off', !historyOn);
      fillEl.style.transform = 'scaleX(' + frac + ')';
      handle.style.left = (frac * 100) + '%';
      if (len > 0 && len <= 140) { ticksEl.style.backgroundSize = (100 / len) + '% 100%'; ticksEl.hidden = false; }
      else ticksEl.hidden = true;
      if (morphTarget >= 0 && historyOn) {
        mtick.hidden = false;
        mtick.style.left = (len ? morphTarget / len * 100 : 0) + '%';
      } else mtick.hidden = true;

      var rec = cursor > 0 ? steps[cursor - 1] : null;
      hudLabel.textContent = !historyOn ? 'History off' : rec ? 'Step ' + cursor + ' · ' + rec.label : 'Step 0 · Starting shape';
      flag.hidden = historyOn && !playing;
      flag.textContent = playing ? 'Movie Mode · scrubber locked' : 'Undo history off · strokes are not recorded';
      flag.classList.toggle('hsd-flag-warn', !historyOn);

      undoBtn.disabled = !historyOn || cursor === 0;
      redoBtn.disabled = !historyOn || cursor >= len;

      for (var i = 0; i < toolEls.length; i++) {
        var el = toolEls[i], t = el.getAttribute('data-tool');
        var on = t === tool;
        el.setAttribute('aria-checked', on ? 'true' : 'false');
        el.tabIndex = on ? 0 : -1;
        if (t === 'morph') el.setAttribute('aria-disabled', morphTarget < 0 ? 'true' : 'false');
      }
      tipEl.textContent = TOOL_INFO[tool].hint;
      noteEl.textContent = morphTarget < 0
        ? 'Morph History needs a target: open the History Menu (the dot left of the scrubber) and choose Set Morph Target Here.'
        : 'Morph target: step ' + morphTarget + '. The violet tick on the scrubber marks it.';
      noteEl.classList.toggle('hsd-note-set', morphTarget >= 0);
      root.classList.toggle('hsd-tool-morph', tool === 'morph');

      // Movie Mode
      playBtn.disabled = !len || !historyOn;
      playBtn.setAttribute('aria-label', playing ? 'Pause Movie Mode' : 'Play the session as a timelapse');
      playBtn.classList.toggle('hsd-is-playing', playing);
      playText.textContent = playing ? 'Pause' : 'Play';
      progFill.style.transform = 'scaleX(' + frac + ')';
      var sps = 60 / framesPerStep;
      movieInfo.textContent = (playing ? 'Playing step ' + cursor + ' of ' + len : len ? 'Replays steps 0 to ' + len : 'No steps to play yet') +
        ' · ' + (sps >= 10 ? Math.round(sps) : sps.toFixed(1)) + ' steps/s at 60 fps. History is not changed.';

      // Memory readout
      var full = N * 8;
      if (rec) {
        readStep.innerHTML = 'This step stored <b class="hsd-em">' + fmtInt(rec.stored) + ' points</b> (' + fmtBytes(rec.bytes) +
          ') — a full copy would be <b>' + fmtInt(N) + ' points</b> (' + fmtBytes(full) + ').';
        var pf = rec.bytes / full;
        meterStep.style.width = Math.max(0.6, Math.min(100, pf * 100)) + '%';
        pctStep.textContent = Math.max(1, Math.round(pf * 100)) + '%';
      } else {
        readStep.innerHTML = 'Step 0 is the starting shape, so nothing is stored for it. A full copy would be <b>' +
          fmtInt(N) + ' points</b> (' + fmtBytes(full) + ').';
        meterStep.style.width = '0%';
        pctStep.textContent = '0%';
      }
      var fullAll = len * full;
      if (len) {
        var ratio = fullAll / Math.max(1, totalBytes);
        readTotal.innerHTML = 'Whole history: <b>' + len + ' step' + (len === 1 ? '' : 's') + '</b> in <b class="hsd-em">' + fmtBytes(totalBytes) +
          '</b>. Full copies of every step would take <b>' + fmtBytes(fullAll) + '</b> (' + (ratio >= 10 ? Math.round(ratio) : ratio.toFixed(1)) + '× more).';
        meterTotal.style.width = Math.max(0.6, Math.min(100, totalBytes / fullAll * 100)) + '%';
        pctTotal.textContent = Math.max(1, Math.round(totalBytes / fullAll * 100)) + '%';
      } else {
        readTotal.innerHTML = 'Whole history: <b>0 steps</b>. Sculpt a stroke to record one.';
        meterTotal.style.width = '0%';
        pctTotal.textContent = '0%';
      }

      if (cursor !== lastAnnounced && !playing) {
        lastAnnounced = cursor;
        if (historyOn) announce('Step ' + cursor + ' of ' + len);
      }
      if (menuOpen) updateMenu();
    }

    /* -------------------------------------------------------------- menu */

    var menuOpen = false;
    function updateMenu() {
      for (var i = 0; i < menuItems.length; i++) {
        var it = menuItems[i], act = it.getAttribute('data-act'), dis = false;
        if (act === 'target') { dis = !historyOn || playing; it.querySelector('[data-aux]').textContent = 'step ' + cursor; }
        else if (act === 'morph') dis = morphTarget < 0;
        else if (act === 'past') dis = !historyOn || playing || cursor === 0;
        else if (act === 'reset') dis = playing || (!steps.length && historyOn);
        else if (act === 'toggle') { dis = playing; it.querySelector('.hsd-mi-text').textContent = historyOn ? 'Turn Off Undo History' : 'Turn On Undo History'; }
        it.setAttribute('aria-disabled', dis ? 'true' : 'false');
      }
    }

    function openMenu(focusFirst) {
      if (menuOpen) return;
      menuOpen = true;
      updateMenu();
      menu.hidden = false;
      dot.setAttribute('aria-expanded', 'true');
      requestDraw();
      if (focusFirst) focusMenuItem(0, 1);
    }

    function closeMenu(returnFocus) {
      if (!menuOpen) return;
      menuOpen = false;
      menu.hidden = true;
      dot.setAttribute('aria-expanded', 'false');
      requestDraw();
      if (returnFocus) dot.focus();
    }

    function focusMenuItem(from, dir) {
      var n = menuItems.length;
      for (var k = 0; k < n; k++) {
        var i = ((from + k * dir) % n + n) % n;
        if (menuItems[i].getAttribute('aria-disabled') !== 'true') { menuItems[i].focus(); return; }
      }
      menuItems[0].focus();
    }

    function runMenu(act) {
      if (act === 'target') setMorphTarget();
      else if (act === 'morph') setTool('morph', true);
      else if (act === 'past') deletePast();
      else if (act === 'reset') resetHistory();
      else if (act === 'toggle') toggleHistory();
    }

    dot.addEventListener('click', function (e) {
      if (menuOpen) closeMenu(false); else openMenu(e.detail === 0);
    });
    dot.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); openMenu(true); }
    });
    menu.addEventListener('click', function (e) {
      var it = e.target.closest ? e.target.closest('.hsd-mi') : null;
      if (!it || it.getAttribute('aria-disabled') === 'true') return;
      closeMenu(true);
      runMenu(it.getAttribute('data-act'));
    });
    menu.addEventListener('keydown', function (e) {
      var i = Array.prototype.indexOf.call(menuItems, document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); focusMenuItem(i + 1, 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); focusMenuItem(i - 1, -1); }
      else if (e.key === 'Home') { e.preventDefault(); focusMenuItem(0, 1); }
      else if (e.key === 'End') { e.preventDefault(); focusMenuItem(menuItems.length - 1, -1); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenu(true); }
      else if (e.key === 'Tab') closeMenu(false);
    });
    document.addEventListener('pointerdown', function (e) {
      if (menuOpen && !menu.contains(e.target) && !dot.contains(e.target)) closeMenu(false);
    }, true);

    /* ---------------------------------------------------- canvas pointers */

    function toModelX(cx) { return (cx - ox) / scale; }
    function toModelY(cy) { return (cy - oy) / scale; }

    var canvasRect = null;

    canvas.addEventListener('pointerdown', function (e) {
      if (st.active || (e.pointerType === 'mouse' && e.button !== 0)) return;
      if (menuOpen) closeMenu(false);
      try { canvas.focus({ preventScroll: true }); } catch (err) { canvas.focus(); }
      if (playing) { pause(false); return; }
      e.preventDefault();
      var t = tool === 'morph' && morphTarget < 0 ? 'grab' : tool;
      canvasRect = canvas.getBoundingClientRect();
      var mx = toModelX(e.clientX - canvasRect.left), my = toModelY(e.clientY - canvasRect.top);
      var s = strength;
      if (e.pointerType === 'pen' && e.pressure > 0) s *= Math.max(0.15, e.pressure);
      try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      beginStroke(mx, my, t, radius, s, true);
      st.id = e.pointerId;
      hover.on = e.pointerType !== 'touch';
      requestDraw();
    });

    canvas.addEventListener('pointermove', function (e) {
      if (st.active && e.pointerId === st.id) {
        moveStroke(toModelX(e.clientX - canvasRect.left), toModelY(e.clientY - canvasRect.top));
        hover.x = st.x; hover.y = st.y;
        requestDraw();
      } else if (!st.active && e.pointerType !== 'touch') {
        var r = canvas.getBoundingClientRect();
        hover.on = true; hover.x = toModelX(e.clientX - r.left); hover.y = toModelY(e.clientY - r.top);
        requestDraw();
      }
    });

    function pointerEnd(e) {
      if (st.active && e.pointerId === st.id) {
        endStroke();
        if (e.pointerType === 'touch') hover.on = false;
      }
    }
    canvas.addEventListener('pointerup', pointerEnd);
    canvas.addEventListener('pointercancel', pointerEnd);
    canvas.addEventListener('lostpointercapture', pointerEnd);
    canvas.addEventListener('pointerleave', function () {
      if (!st.active && hover.on) { hover.on = false; requestDraw(); }
    });
    canvas.addEventListener('contextmenu', function (e) { if (st.active) e.preventDefault(); });

    /* --------------------------------------------------------- scrubber */

    var scrubbing = false, scrubId = -1;
    function scrubAt(clientX) {
      var r = rail.getBoundingClientRect();
      var f = r.width ? clamp((clientX - r.left) / r.width, 0, 1) : 0;
      gotoStep(Math.round(f * steps.length), { hold: true });
    }
    track.addEventListener('pointerdown', function (e) {
      if (playing || !historyOn || !steps.length || (e.pointerType === 'mouse' && e.button !== 0)) return;
      e.preventDefault();
      track.focus({ preventScroll: true });
      scrubbing = true; scrubId = e.pointerId;
      try { track.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      track.classList.add('hsd-dragging');
      scrubAt(e.clientX);
      flashStart(true); requestDraw();
    });
    track.addEventListener('pointermove', function (e) {
      if (scrubbing && e.pointerId === scrubId) scrubAt(e.clientX);
    });
    function scrubEnd(e) {
      if (!scrubbing || e.pointerId !== scrubId) return;
      scrubbing = false; scrubId = -1;
      track.classList.remove('hsd-dragging');
      flashStart(false); requestDraw();
    }
    track.addEventListener('pointerup', scrubEnd);
    track.addEventListener('pointercancel', scrubEnd);
    track.addEventListener('lostpointercapture', scrubEnd);
    track.addEventListener('keydown', function (e) {
      if (playing || !historyOn) return;
      var t;
      if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') t = cursor - 1;
      else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') t = cursor + 1;
      else if (e.key === 'PageDown') t = cursor - 10;
      else if (e.key === 'PageUp') t = cursor + 10;
      else if (e.key === 'Home') t = 0;
      else if (e.key === 'End') t = steps.length;
      else return;
      e.preventDefault();
      gotoStep(clamp(t, 0, steps.length));
    });

    /* ------------------------------------------------------ other controls */

    function onToolClick(e) {
      var t = e.currentTarget.getAttribute('data-tool');
      setTool(t, false);
    }
    for (var bi = 0; bi < toolEls.length; bi++) {
      toolEls[bi].addEventListener('click', onToolClick);
    }
    q('.hsd-tools').addEventListener('keydown', function (e) {
      var d = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0;
      if (!d) return;
      e.preventDefault();
      var i = TOOL_ORDER.indexOf(tool), n = TOOL_ORDER.length;
      for (var k = 1; k <= n; k++) {
        var t = TOOL_ORDER[((i + d * k) % n + n) % n];
        if (t === 'morph' && morphTarget < 0) continue;
        setTool(t, false); updateUI();
        host.querySelector('.hsd-tool[data-tool="' + t + '"]').focus();
        return;
      }
    });

    function syncRange(el) {
      var k = el.getAttribute('data-k'), v = +el.value;
      var f = (v - +el.min) / (+el.max - +el.min);
      el.style.setProperty('--hsd-f', f.toFixed(4));
      var out = el.parentNode.querySelector('output');
      if (k === 'radius') { radius = v; out.textContent = String(v); }
      else { strength = v / 100; out.textContent = strength.toFixed(2); }
    }
    for (var ri = 0; ri < ranges.length; ri++) {
      syncRange(ranges[ri]);
      ranges[ri].addEventListener('input', function (e) { syncRange(e.currentTarget); requestDraw(); });
    }

    undoBtn.addEventListener('click', undo);
    redoBtn.addEventListener('click', redo);
    resetAllBtn.addEventListener('click', resetAll);
    playBtn.addEventListener('click', function () { if (playing) pause(false); else play(); });
    fpsInput.addEventListener('change', function () { setFps(fpsInput.value); });
    fpsInput.addEventListener('input', function () {
      var v = +fpsInput.value;
      if (v >= 1 && v <= 30) setFps(v);
    });
    var stepBtns = host.querySelectorAll('.hsd-step-btn');
    for (var si = 0; si < stepBtns.length; si++) {
      stepBtns[si].addEventListener('click', function (e) {
        setFps(framesPerStep + +e.currentTarget.getAttribute('data-d'));
      });
    }

    root.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && menuOpen) { closeMenu(true); return; }
      var mod = e.ctrlKey || e.metaKey;
      if (!mod || e.altKey) return;
      var tg = e.target;
      if (tg && tg.tagName === 'INPUT' && tg.type === 'number') return;
      var k = e.key.toLowerCase();
      if (k === 'z' && !e.shiftKey) { e.preventDefault(); if (!playing) undo(); }
      else if ((k === 'z' && e.shiftKey) || k === 'y') { e.preventDefault(); if (!playing) redo(); }
    });

    /* ------------------------------------------------------------ resize */

    if (window.ResizeObserver) new ResizeObserver(function () { layout(); }).observe(stage);
    window.addEventListener('resize', layout);
    if (mqReduce && mqReduce.addEventListener) mqReduce.addEventListener('change', requestDraw);

    /* -------------------------------------------------------------- start */

    readColors();
    scripted();
    layout();
    uiDirty = true;
    lastAnnounced = cursor;
    requestDraw();

    /* ---------------------------------------------------------- test API */

    function runStroke(points, kind, opts) {
      if (!points || !points.length) return false;
      if (playing) pause(false);
      endStroke();
      kind = kind || tool;
      if (!TOOL_INFO[kind]) return false;
      if (kind === 'morph' && morphTarget < 0) return false;
      var r = opts && opts.radius ? +opts.radius : radius;
      var s = opts && opts.strength != null ? clamp(+opts.strength, 0, 1) : strength;
      beginStroke(toModelX(points[0][0]), toModelY(points[0][1]), kind, r, s, true);
      for (var i = 1; i < points.length; i++) moveStroke(toModelX(points[i][0]), toModelY(points[i][1]));
      return !!endStroke();
    }

    window.HindsightDemo = {
      steps: function () { return steps.length; },
      pos: function () { return cursor; },
      goto: function (i) { gotoStep(+i); return cursor; },
      stroke: function (points, kind, opts) { return runStroke(points, kind, opts); },
      snapshot: function () { return Array.prototype.slice.call(pos); },
      setMorphTarget: function () { return setMorphTarget(); },
      morphTarget: function () { return morphTarget; },
      morphStroke: function (points, opts) { return runStroke(points, 'morph', opts); },
      play: function () { return play(); },
      pause: function () { pause(false); },
      isPlaying: function () { return playing; },
      framesPerStep: function (n) { return n == null ? framesPerStep : setFps(n); },
      historyEnabled: function () { return historyOn; },
      setTool: function (t) { setTool(t, false); return tool; },
      toCanvas: function (mx, my) { return [ox + mx * scale, oy + my * scale]; },
      reset: function () { resetAll(); },
      stats: function () {
        var rec = cursor > 0 ? steps[cursor - 1] : null;
        return {
          lastStepPoints: rec ? rec.stored : 0,
          lastStepBytes: rec ? rec.bytes : 0,
          fullPoints: N,
          fullBytes: N * 8,
          totalBytes: totalBytes,
          fullCopyBytes: steps.length * N * 8,
          steps: steps.length,
          pos: cursor
        };
      }
    };
  }
})();
