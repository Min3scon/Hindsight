/* Hindsight website extras: the Join / Unmerge demo (#hs-unmerge).
   Plain JS + SVG, no dependencies, no network requests.
   Test hook: window.HindsightExtras.unmerge = { steps(), pos(), goto(i), joinedAt(), isJoined() } */
(function () {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var PI = Math.PI, TAU = PI * 2;
  var CAP = 10000;
  var uidN = 0;

  /* ---------- small helpers ---------- */
  function uid(p) { uidN += 1; return 'hsx-' + p + '-' + uidN; }
  function S(tag, a, parent) {
    var n = document.createElementNS(NS, tag);
    if (a) for (var k in a) if (a[k] != null) n.setAttribute(k, a[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function H(tag, cls, parent, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    if (parent) parent.appendChild(n);
    return n;
  }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function easeIO(t) { t = clamp(t, 0, 1); return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
  function smooth(t) { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); }
  function bell(t) { return Math.sin(PI * clamp(t, 0, 1)); }
  function wrapA(a) { a = (a + PI) % TAU; if (a < 0) a += TAU; return a - PI; }
  function gauss(d, w) { d = wrapA(d) / w; return Math.exp(-0.5 * d * d); }
  function ellR(th, rx, ry) { var c = Math.cos(th), s = Math.sin(th); return 1 / Math.sqrt(c * c / (rx * rx) + s * s / (ry * ry)); }
  function r1(n) { return Math.round(n * 10) / 10; }
  function closedPath(p) {
    var n = p.length, d = 'M' + r1(p[0][0]) + ' ' + r1(p[0][1]);
    for (var i = 0; i < n; i++) {
      var p0 = p[(i - 1 + n) % n], a = p[i], b = p[(i + 1) % n], p3 = p[(i + 2) % n];
      d += 'C' + r1(a[0] + (b[0] - p0[0]) / 6) + ' ' + r1(a[1] + (b[1] - p0[1]) / 6) + ' ' +
        r1(b[0] - (p3[0] - a[0]) / 6) + ' ' + r1(b[1] - (p3[1] - a[1]) / 6) + ' ' + r1(b[0]) + ' ' + r1(b[1]);
    }
    return d + 'Z';
  }

  var mq = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  function reduced() { return !!(mq && mq.matches); }

  var ICON_PLAY = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M4.5 2.6v10.8L13.2 8z" fill="currentColor"/></svg>';
  var ICON_STOP = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="3.4" y="3.4" width="9.2" height="9.2" rx="1.8" fill="currentColor"/></svg>';

  /* ======================================================================
     Join / Unmerge demo
     ====================================================================== */
  function initUnmerge(mount) {
    var N = 9;          // recorded steps; positions run 0..N
    var J = 6;          // the join step
    var STEPS = [
      { tool: 'Start', who: '' },
      { tool: 'Clay Strips', who: 'torso' },
      { tool: 'Inflate', who: 'arm' },
      { tool: 'Crease', who: 'torso' },
      { tool: 'Pinch', who: 'arm' },
      { tool: 'Draw', who: 'torso' },
      { tool: 'Join', who: 'join' },
      { tool: 'Smooth', who: 'joined' },
      { tool: 'Clay Strips', who: 'joined' },
      { tool: 'Inflate', who: 'joined' }
    ];
    var NAMES = { torso: 'Torso', arm: 'Arm', joined: 'Torso (joined)', join: 'Torso + Arm' };

    /* scene geometry, in a 640 x 360 viewBox */
    var TC = [236, 196];                                   // torso centre
    var PHI = -16 * PI / 180, CP = Math.cos(PHI), SP = Math.sin(PHI);
    var A_JOIN = [369, 111], A_APART = [430, 124];         // arm centre joined / apart
    var JP = [311, 124];                                   // where the joined shapes fuse
    var VB_WIDE = '0 0 640 360', VB_NARROW = '126 22 420 330';

    function armXY(c, x, y) { return [c[0] + x * CP - y * SP, c[1] + x * SP + y * CP]; }

    function torsoR(th, a) {
      return ellR(th, 78, 102) +
        13 * gauss(th + 0.80, 0.40) + 13 * gauss(th + 2.34, 0.40) -        // shoulders
        6 * gauss(th - 0.30, 0.30) - 6 * gauss(th - (PI - 0.30), 0.30) +     // waist
        a[1] * 10 * gauss(th + 2.62, 0.24) -                                   // 1 clay strips, chest
        a[3] * 12 * gauss(th - 0.62, 0.20) +                                   // 3 crease, waist
        a[5] * 13 * gauss(th - 2.40, 0.28) +                                   // 5 draw, hip
        a[7] * 5 * gauss(th + 0.80, 0.34) +                                    // 7 smooth, shoulder
        a[9] * 12 * gauss(th - 1.35, 0.34);                                    // 9 inflate, belly
    }
    function torsoPts(a) {
      var pts = [], n = 120;
      for (var i = 0; i < n; i++) {
        var th = -PI + i * TAU / n, r = torsoR(th, a);
        pts.push([TC[0] + r * Math.cos(th), TC[1] + r * Math.sin(th)]);
      }
      return pts;
    }
    function armPts(a, c) {
      var pts = [], n = 72;
      for (var i = 0; i < n; i++) {
        var t = -PI + i * TAU / n, ct = Math.cos(t), st = Math.sin(t);
        var ry = 25 * (1 - 0.2 * ct);                    // tapers toward the hand
        var x = 72 * ct, y = ry * st;
        var nx = ry * ct, ny = 72 * st, nl = Math.sqrt(nx * nx + ny * ny) || 1;
        var d = 4 * gauss(t, 0.5) +                       // fist
          a[2] * 10 * gauss(t + 1.95, 0.32) -             // 2 inflate, biceps
          a[4] * (7 * gauss(t - 0.72, 0.2) + 6 * gauss(t + 0.72, 0.2)) +   // 4 pinch, wrist
          a[9] * 4 * gauss(t + 1.9, 0.4);                  // 9 inflate
        pts.push(armXY(c, x + nx / nl * d, y + ny / nl * d));
      }
      return pts;
    }
    function bridgePts(jj, a7) {
      var pts = [], n = 40, bx = (24 + 10 * a7) * jj, by = (25 + 7 * a7) * jj;
      for (var i = 0; i < n; i++) {
        var th = -PI + i * TAU / n, r = ellR(th, bx, by);
        var x = r * Math.cos(th), y = r * Math.sin(th);
        pts.push([JP[0] + x * CP - y * SP, JP[1] + x * SP + y * CP]);
      }
      return pts;
    }

    /* creases: one per sculpt step; 'arm' ones ride along with the arm */
    var CREASES = [
      { k: 1, p: [[199, 152], [221, 135], [249, 143]] },
      { k: 3, p: [[297, 210], [308, 230], [301, 252]] },
      { k: 5, p: [[184, 238], [194, 262], [219, 274]] },
      { k: 2, p: [[-46, -9], [-25, -22], [-2, -12]], arm: true },
      { k: 4, p: [[45, -13], [52, 0], [45, 13]], arm: true },
      { k: 8, p: [[258, 150], [300, 122], [356, 114]] },
      { k: 9, p: [[211, 270], [240, 287], [273, 274]] }
    ];
    /* where each step's brush lands (for the stroke ring) */
    var RINGS = {
      1: { p: [214, 146] }, 2: { p: [-24, -20], arm: true, cool: true }, 3: { p: [303, 231] },
      4: { p: [49, 0], arm: true, cool: true }, 5: { p: [198, 258] }, 7: { p: JP },
      8: { p: [304, 122] }, 9: { p: [242, 282] }
    };

    /* ---------- DOM ---------- */
    mount.textContent = '';
    var root = H('div', 'hsx-root hsx-um', mount);
    var stage = H('div', 'hsx-um-stage', root);
    stage.setAttribute('role', 'img');
    var svg = S('svg', { 'class': 'hsx-um-svg', viewBox: VB_WIDE, preserveAspectRatio: 'xMidYMid meet', 'aria-hidden': 'true', focusable: 'false' }, stage);
    var defs = S('defs', null, svg);

    function clayGrad(cx, cy, r, cool) {
      var id = uid('clay');
      var g = S('radialGradient', { id: id, gradientUnits: 'userSpaceOnUse', cx: cx, cy: cy, r: r, fx: cx - r * 0.05, fy: cy - r * 0.06 }, defs);
      if (cool) {
        [['0', '#effaff'], ['.3', '#b6dbef'], ['.72', '#5f8eaf'], ['1', '#27445a']].forEach(function (s) {
          S('stop', { offset: s[0], 'stop-color': s[1] }, g);
        });
      } else {
        S('stop', { offset: '0', 'class': 'hsx-um-stop-hi' }, g);
        S('stop', { offset: '.26', 'class': 'hsx-um-stop-1' }, g);
        S('stop', { offset: '.66', 'class': 'hsx-um-stop-2' }, g);
        S('stop', { offset: '1', 'class': 'hsx-um-stop-3' }, g);
      }
      return { el: g, url: 'url(#' + id + ')' };
    }
    var gWarm = clayGrad(196, 120, 300, false);   // one gradient in world space, so the fused shape has no seam
    var gCool = clayGrad(400, 96, 120, true);     // follows the arm while it is its own object
    var specId = uid('spec');
    var sg = S('radialGradient', { id: specId }, defs);
    S('stop', { offset: '0', 'stop-color': '#fff', 'stop-opacity': '.55' }, sg);
    S('stop', { offset: '1', 'stop-color': '#fff', 'stop-opacity': '0' }, sg);
    var shadowId = uid('shadow');
    var shg = S('radialGradient', { id: shadowId }, defs);
    S('stop', { offset: '0', 'stop-color': '#000', 'stop-opacity': '.55' }, shg);
    S('stop', { offset: '1', 'stop-color': '#000', 'stop-opacity': '0' }, shg);

    /* floor grid with Blender's red and green axis lines */
    var grid = S('g', { 'class': 'hsx-um-grid' }, svg);
    (function () {
      var hz = 236, bottom = 420, vp = [330, 150];
      for (var k = 1; k <= 9; k++) {
        var y = hz + (bottom - hz) * Math.pow(k / 9, 1.9);
        S('line', { x1: -500, y1: r1(y), x2: 1140, y2: r1(y) }, grid);
      }
      for (var xb = -1100; xb <= 1760; xb += 110) {
        var tTop = (hz - vp[1]) / (bottom - vp[1]);
        S('line', { x1: r1(lerp(vp[0], xb, tTop)), y1: hz, x2: xb, y2: bottom }, grid);
      }
      S('line', { 'class': 'hsx-ax-x', x1: -500, y1: 318, x2: 1140, y2: 318 }, grid);
      S('line', { 'class': 'hsx-ax-y', x1: r1(lerp(vp[0], 352, (hz - vp[1]) / (bottom - vp[1]))), y1: hz, x2: 352, y2: bottom }, grid);
    })();

    var shT = S('ellipse', { cx: TC[0] + 4, cy: 318, rx: 104, ry: 14, fill: 'url(#' + shadowId + ')' }, svg);
    var shA = S('ellipse', { cx: 0, cy: 318, rx: 70, ry: 9, fill: 'url(#' + shadowId + ')', opacity: '.75' }, svg);

    /* outline layer (strokes underneath), then fills on top: overlapping
       shapes read as one outline, separate shapes keep their own */
    var olG = S('g', null, svg);
    var oT = S('path', { 'class': 'hsx-um-ol is-warm' }, olG);
    var oB = S('path', { 'class': 'hsx-um-ol is-warm' }, olG);
    var oAc = S('path', { 'class': 'hsx-um-ol is-cool' }, olG);
    var oAw = S('path', { 'class': 'hsx-um-ol is-warm' }, olG);
    var fG = S('g', null, svg);
    var fT = S('path', { fill: gWarm.url, 'data-part': 'torso' }, fG);
    var fB = S('path', { fill: gWarm.url, 'data-part': 'bridge' }, fG);
    var fAw = S('path', { fill: gWarm.url, 'data-part': 'arm' }, fG);
    var fAc = S('path', { fill: gCool.url }, fG);

    var spT = S('ellipse', { cx: 0, cy: 0, rx: 36, ry: 21, fill: 'url(#' + specId + ')', transform: 'translate(204 142) rotate(-32)' }, svg);
    var spA = S('ellipse', { cx: 0, cy: 0, rx: 34, ry: 7, fill: 'url(#' + specId + ')' }, svg);

    var crG = S('g', null, svg);
    var creases = CREASES.map(function (c) {
      var g = S('g', null, crG);
      var dk = S('path', { 'class': 'hsx-um-crease-d', pathLength: '1', 'stroke-dasharray': '1 1' }, g);
      var lt = S('path', { 'class': 'hsx-um-crease-l', pathLength: '1', 'stroke-dasharray': '1 1', transform: 'translate(-.9 -1.5)' }, g);
      return { c: c, g: g, dk: dk, lt: lt };
    });

    var ring = S('circle', { 'class': 'hsx-um-ring is-warm', r: 24, opacity: 0 }, svg);

    var badge = S('g', { 'class': 'hsx-um-badge', opacity: 0 }, svg);
    var badgeRect = S('rect', { x: -66, y: -18, width: 132, height: 36, rx: 10 }, badge);
    var badgeText = S('text', { x: 0, y: 5.5, 'text-anchor': 'middle' }, badge);

    /* outliner + last-action overlays */
    var outl = H('div', 'hsx-um-outliner', stage);
    outl.setAttribute('aria-hidden', 'true');
    H('div', 'hsx-um-olh', outl, 'Objects');
    function makeRow(name, cool) {
      var r = H('div', 'hsx-um-row', outl);
      H('span', 'hsx-um-sw' + (cool ? ' is-cool' : ''), r);
      var nm = H('span', 'hsx-um-name', r, name);
      var tag = H('span', 'hsx-um-tag', nm, 'joined');
      var n = H('span', 'hsx-um-n', r, '');
      H('span', null, r);
      var hist = H('span', 'hsx-um-hist', r);
      return { el: r, tag: tag, n: n, hist: hist };
    }
    var rowT = makeRow('Torso', false), rowA = makeRow('Arm', true);
    var action = H('div', 'hsx-um-action', stage);
    action.setAttribute('aria-hidden', 'true');

    /* scrubber */
    var bar = H('div', 'hsx-scrub', root);
    var dot = H('span', 'hsx-scrub-dot', bar);
    dot.title = 'History Menu';
    dot.setAttribute('aria-hidden', 'true');
    var track = H('div', 'hsx-scrub-track', bar);
    track.tabIndex = 0;
    track.setAttribute('role', 'slider');
    track.setAttribute('aria-label', 'Undo history step');
    track.setAttribute('aria-valuemin', '0');
    track.setAttribute('aria-valuemax', String(N));
    track.setAttribute('aria-orientation', 'horizontal');
    var rail = H('div', 'hsx-scrub-rail', track);
    var fill = H('div', 'hsx-scrub-fill', rail);
    var ticks = H('div', 'hsx-scrub-ticks', track);
    for (var i = 1; i <= N; i++) {
      var who = STEPS[i].who;
      var tk = H('span', 'hsx-tick' + (who === 'arm' ? ' is-cool' : '') + (i === J ? ' is-join' : ''), ticks);
      tk.style.left = (i / N * 100) + '%';
      if (i === J) {
        var lab = H('span', 'hsx-tick-label', ticks, 'Join');
        lab.style.left = (i / N * 100) + '%';
      }
    }
    var handle = H('div', 'hsx-scrub-handle', track);
    var count = H('div', 'hsx-scrub-count', bar);
    count.setAttribute('aria-hidden', 'true');

    /* footer */
    var foot = H('div', 'hsx-um-foot', root);
    var play = H('button', 'hsx-btn hsx-um-play', foot);
    play.type = 'button';
    play.setAttribute('aria-pressed', 'false');
    var copy = H('div', 'hsx-um-copy', foot);
    H('p', 'hsx-um-caption', copy, 'Scrub back past a join and the original objects come back. Go forward and they merge again.');
    var hint = H('p', 'hsx-um-hint', copy);
    hint.innerHTML = 'Drag the bar, or focus it and press <kbd aria-hidden="true">←</kbd> <kbd aria-hidden="true">→</kbd><span class="hsx-sr">the left and right arrow keys</span>. The Join tick marks the Ctrl+J step.';
    var live = H('p', 'hsx-sr', root);
    live.setAttribute('aria-live', 'polite');

    /* ---------- state ---------- */
    var pos = N, vis = N, dragV = N, dragging = false, dir = -1;
    var raf = 0, last = 0, badgeT = -1e9, badgeSplit = false, autoTimer = 0, autoOn = false;

    function setPlayLabel() {
      play.innerHTML = (autoOn ? ICON_STOP : ICON_PLAY) + '<span>' + (autoOn ? 'Stop' : 'Play the cycle') + '</span>';
      play.setAttribute('aria-pressed', autoOn ? 'true' : 'false');
    }
    setPlayLabel();

    function amps(v) {
      var a = [0];
      for (var k = 1; k <= N; k++) a.push(easeIO(v - (k - 1)));
      return a;
    }

    function render(now) {
      var a = amps(vis), jj = a[J];
      var ac = [lerp(A_APART[0], A_JOIN[0], jj), lerp(A_APART[1], A_JOIN[1], jj)];
      var dT = closedPath(torsoPts(a)), dA = closedPath(armPts(a, ac));
      oT.setAttribute('d', dT); fT.setAttribute('d', dT);
      oAc.setAttribute('d', dA); oAw.setAttribute('d', dA); fAw.setAttribute('d', dA); fAc.setAttribute('d', dA);
      oAc.setAttribute('opacity', r1(1 - jj));
      oAw.setAttribute('opacity', r1(jj * 10) / 10);
      fAc.setAttribute('opacity', Math.round((1 - jj) * 100) / 100);
      if (jj > 0.002) {
        var dB = closedPath(bridgePts(jj, a[7]));
        oB.setAttribute('d', dB); fB.setAttribute('d', dB);
        oB.removeAttribute('display'); fB.removeAttribute('display');
      } else {
        oB.setAttribute('display', 'none'); fB.setAttribute('display', 'none');
      }
      gCool.el.setAttribute('cx', r1(ac[0] - 30)); gCool.el.setAttribute('cy', r1(ac[1] - 28));
      gCool.el.setAttribute('fx', r1(ac[0] - 36)); gCool.el.setAttribute('fy', r1(ac[1] - 35));
      shA.setAttribute('cx', r1(ac[0] + 14));
      shA.setAttribute('rx', r1(lerp(72, 56, jj)));
      var hs = armXY(ac, -14, -12);
      spA.setAttribute('transform', 'translate(' + r1(hs[0]) + ' ' + r1(hs[1]) + ') rotate(-16)');
      spA.setAttribute('opacity', r1(lerp(0.9, 0.55, jj)));

      creases.forEach(function (cr) {
        var p = cr.c.arm ? cr.c.p.map(function (q) { return armXY(ac, q[0], q[1]); }) : cr.c.p;
        var amp = a[cr.c.k];
        if (amp < 0.002) { cr.g.setAttribute('display', 'none'); return; }
        cr.g.removeAttribute('display');
        var d = 'M' + r1(p[0][0]) + ' ' + r1(p[0][1]) + 'Q' + r1(p[1][0]) + ' ' + r1(p[1][1]) + ' ' + r1(p[2][0]) + ' ' + r1(p[2][1]);
        cr.dk.setAttribute('d', d); cr.lt.setAttribute('d', d);
        var off = String(Math.round((1 - amp) * 1000) / 1000);
        cr.dk.setAttribute('stroke-dashoffset', off); cr.lt.setAttribute('stroke-dashoffset', off);
      });

      /* brush ring on the step currently being applied or removed */
      var k = Math.floor(vis) + 1, fr = vis - Math.floor(vis);
      var rg = RINGS[k];
      if (rg && fr > 0.01 && fr < 0.99 && !reduced()) {
        var rp = rg.arm ? armXY(ac, rg.p[0], rg.p[1]) : rg.p;
        ring.setAttribute('cx', r1(rp[0])); ring.setAttribute('cy', r1(rp[1]));
        ring.setAttribute('r', r1(18 + 10 * fr));
        ring.setAttribute('class', 'hsx-um-ring ' + (rg.cool ? 'is-cool' : 'is-warm'));
        ring.setAttribute('opacity', r1(bell(fr) * 10) / 10);
      } else {
        ring.setAttribute('opacity', 0);
      }

      /* Ctrl+J / Unmerge badge */
      var age = (now || 0) - badgeT, bo = 0;
      if (age >= 0 && age < 1500) bo = reduced() ? 1 : Math.min(1, age / 140) * (1 - smooth((age - 1100) / 400));
      if (bo > 0.001) {
        badge.setAttribute('class', 'hsx-um-badge' + (badgeSplit ? ' is-split' : ''));
        var lift = reduced() ? 0 : (1 - Math.min(1, age / 260)) * 6;
        badge.setAttribute('transform', 'translate(' + (JP[0] + 34) + ' ' + r1(JP[1] - 74 + lift) + ')');
        badge.setAttribute('opacity', r1(bo * 100) / 100);
      } else {
        badge.setAttribute('opacity', 0);
      }

      /* scrubber handle follows the pointer while dragging */
      var hv = dragging ? dragV : vis, f = clamp(hv / N, 0, 1);
      handle.style.left = 'calc(12px + (100% - 24px) * ' + f.toFixed(4) + ')';
      fill.style.width = (f * 100).toFixed(2) + '%';
    }

    function stepCount(who, p) {
      var c = 0;
      for (var k = 1; k <= p; k++) if (STEPS[k].who === who) c++;
      return c;
    }
    function fillHist(el, list) {
      var html = '';
      list.forEach(function (w) { html += '<i class="' + w + '"></i>'; });
      if (el.innerHTML !== html) el.innerHTML = html;
    }
    function plural(n) { return n + (n === 1 ? ' step' : ' steps'); }

    function updateUI() {
      var joined = pos >= J, st = STEPS[pos];
      count.innerHTML = pos + ' <span>/ ' + CAP + '</span>';
      track.setAttribute('aria-valuenow', String(pos));
      var vt;
      if (pos === 0) vt = 'Step 0 of ' + N + ': before any strokes. Two separate objects, Torso and Arm.';
      else if (pos === J) vt = 'Step ' + J + ' of ' + N + ': Join. Torso and Arm are now one object.';
      else vt = 'Step ' + pos + ' of ' + N + ': ' + st.tool + ' on ' + (joined ? 'the joined object' : NAMES[st.who]) + '. ' +
        (joined ? 'One joined object.' : 'Two separate objects, Torso and Arm.');
      track.setAttribute('aria-valuetext', vt);
      stage.setAttribute('aria-label', joined ?
        'Viewport: Torso and Arm joined into one clay object with a single outline.' :
        'Viewport: two separate clay objects, Torso in orange and Arm in blue, each with its own outline.');
      root.setAttribute('data-joined', joined ? 'true' : 'false');
      root.setAttribute('data-pos', String(pos));

      /* outliner */
      rowA.el.classList.toggle('is-gone', joined);
      rowT.tag.style.display = joined ? '' : 'none';
      var who = st.who;
      rowT.el.classList.toggle('is-active', pos > 0 && (who === 'torso' || joined));
      rowA.el.classList.toggle('is-active', pos > 0 && who === 'arm');
      var tl = [], al = [];
      for (var k = 1; k <= N; k++) {
        var w = STEPS[k].who, done = k <= pos;
        if (k > J && !joined) continue;
        if (joined) {
          if (k <= pos) tl.push(w === 'arm' ? 'is-cool' : w === 'join' ? 'is-join' : '');
        } else if (k < J) {
          if (w === 'torso' && done) tl.push('');
          if (w === 'arm' && done) al.push('is-cool');
        }
      }
      fillHist(rowT.hist, tl.length ? tl : ['is-empty']);
      fillHist(rowA.hist, al.length ? al : ['is-empty']);
      rowT.n.textContent = plural(joined ? pos : stepCount('torso', pos));
      rowA.n.textContent = plural(stepCount('arm', Math.min(pos, J - 1)));

      /* last action */
      if (pos === 0) action.innerHTML = '<b>Step 0</b> · nothing sculpted yet';
      else if (pos === J) action.innerHTML = '<b>Step ' + J + '</b> · Join (Ctrl+J) · Torso + Arm';
      else action.innerHTML = '<b>Step ' + pos + '</b> · ' + st.tool + ' · ' + (joined ? 'Torso (joined)' : NAMES[who]);
    }

    function kick() { if (!raf) { last = 0; raf = requestAnimationFrame(tick); } }
    function tick(now) {
      raf = 0;
      var dt = last ? Math.min(0.05, (now - last) / 1000) : 1 / 60;
      last = now;
      var goal = dragging ? dragV : pos;
      if (reduced()) vis = goal;
      else {
        vis += (goal - vis) * (1 - Math.exp(-dt * (dragging ? 16 : 6.5)));
        if (Math.abs(goal - vis) < 0.003) vis = goal;
      }
      render(now);
      if (vis !== goal || now - badgeT < 1600) raf = requestAnimationFrame(tick);
    }

    function setPos(p, quiet) {
      p = clamp(Math.round(p), 0, N);
      if (p === pos) { kick(); return; }
      var old = pos;
      pos = p;
      dir = p > old ? 1 : -1;
      if ((old >= J) !== (p >= J)) {
        badgeT = performance.now();
        badgeSplit = p < J;
        badgeText.textContent = '';
        if (badgeSplit) {
          badgeText.textContent = 'Unmerged';
          badgeRect.setAttribute('x', -62); badgeRect.setAttribute('width', 124);
        } else {
          var t1 = S('tspan', { 'class': 'hsx-um-kbd' }, badgeText); t1.textContent = 'Ctrl+J';
          var t2 = S('tspan', { dx: 8 }, badgeText); t2.textContent = 'Join';
          badgeRect.setAttribute('x', -66); badgeRect.setAttribute('width', 132);
        }
        if (!quiet) live.textContent = badgeSplit ?
          'Unmerged: Torso and Arm are separate objects again, each with its own history.' :
          'Joined: Torso and Arm are one object again.';
      }
      updateUI();
      kick();
    }

    /* ---------- pointer ---------- */
    function fromX(x) {
      var r = rail.getBoundingClientRect();
      return clamp((x - r.left) / (r.width || 1), 0, 1) * N;
    }
    track.addEventListener('pointerdown', function (e) {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      stopAuto();
      dragging = true;
      track.classList.add('is-dragging');
      try { track.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      if (document.activeElement !== track) track.focus({ preventScroll: true });
      dragV = fromX(e.clientX);
      setPos(dragV);
      kick();
      e.preventDefault();
    });
    track.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      dragV = fromX(e.clientX);
      setPos(dragV);
      kick();
    });
    function endDrag() {
      if (!dragging) return;
      dragging = false;
      track.classList.remove('is-dragging');
      kick();
    }
    track.addEventListener('pointerup', endDrag);
    track.addEventListener('pointercancel', endDrag);
    track.addEventListener('lostpointercapture', endDrag);

    /* ---------- keyboard ---------- */
    track.addEventListener('keydown', function (e) {
      var k = e.key, p = null;
      if ((e.ctrlKey || e.metaKey) && (k === 'z' || k === 'Z')) p = e.shiftKey ? pos + 1 : pos - 1;
      else if (e.ctrlKey || e.metaKey || e.altKey) return;
      else if (k === 'ArrowLeft' || k === 'ArrowDown') p = pos - 1;
      else if (k === 'ArrowRight' || k === 'ArrowUp') p = pos + 1;
      else if (k === 'Home') p = 0;
      else if (k === 'End') p = N;
      else if (k === 'PageDown') p = pos - 3;
      else if (k === 'PageUp') p = pos + 3;
      if (p === null) return;
      e.preventDefault();
      stopAuto();
      setPos(p);
    });

    /* ---------- autoplay: build up, scrub back past the join, forward again ---------- */
    var CYCLE = [[0, 900], [1, 620], [2, 620], [3, 620], [4, 620], [5, 820], [6, 1500], [7, 620], [8, 620], [9, 1300],
      [8, 420], [7, 420], [6, 700], [5, 1500], [4, 1100], [5, 700], [6, 1500], [7, 420], [8, 420], [9, 0]];
    function stopAuto() {
      if (autoTimer) clearTimeout(autoTimer);
      autoTimer = 0;
      if (autoOn) { autoOn = false; setPlayLabel(); }
    }
    function runAuto(i) {
      if (i >= CYCLE.length) { autoTimer = 0; autoOn = false; setPlayLabel(); return; }
      setPos(CYCLE[i][0]);
      autoTimer = setTimeout(function () { runAuto(i + 1); }, CYCLE[i][1] * (reduced() ? 1.25 : 1));
    }
    play.addEventListener('click', function () {
      if (autoOn) { stopAuto(); return; }
      autoOn = true;
      setPlayLabel();
      runAuto(0);
    });

    /* ---------- layout ---------- */
    function layout() {
      var narrow = root.clientWidth < 560;
      if (narrow !== root.classList.contains('is-narrow')) {
        root.classList.toggle('is-narrow', narrow);
        svg.setAttribute('viewBox', narrow ? VB_NARROW : VB_WIDE);
      }
    }
    layout();
    if ('ResizeObserver' in window) new ResizeObserver(layout).observe(root);
    else window.addEventListener('resize', layout);

    updateUI();
    render(0);

    return {
      steps: function () { return N; },
      pos: function () { return pos; },
      goto: function (i) { stopAuto(); setPos(i); return pos; },
      joinedAt: function () { return J; },
      isJoined: function () { return pos >= J; },
      isAnimating: function () { return !!raf; },
      playing: function () { return autoOn; }
    };
  }

  /* ---------- boot ---------- */
  function boot() {
    var api = window.HindsightExtras || {};
    var um = document.getElementById('hs-unmerge');
    if (um && !um.hasAttribute('data-hsx')) {
      um.setAttribute('data-hsx', 'ready');
      api.unmerge = initUnmerge(um);
    }
    window.HindsightExtras = api;
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
