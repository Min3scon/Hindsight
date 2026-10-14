/* Hindsight website: "How it stays fast" animated diagram.
   Mounts into #hs-delta-diagram. Plain JS, no dependencies, no network. */
(function () {
  'use strict';

  var host = document.getElementById('hs-delta-diagram');
  if (!host || host.getAttribute('data-hsd2') === 'ready') return;
  host.setAttribute('data-hsd2', 'ready');

  var N = 48;
  var COLS = 8;
  var HIT = [19, 20, 21, 27, 28, 29];
  var XOR = ['3a7f', '0c12', '41e0', '1b9c', '06d4', '2f31'];

  var PHASES = [
    { k: 'mesh', t: 'Mesh', d: 1800,
      c: 'A mesh is many points. Here are 48 point positions.' },
    { k: 'stroke', t: 'Stroke', d: 2000,
      c: 'A brush stroke changes only a few of them: 6 points here.' },
    { k: 'record', t: 'Record', d: 2700,
      c: 'No full copy. Hindsight stores one small step record: the indices of the changed points and the XOR of their old and new float bits.' },
    { k: 'undo', t: 'Undo', d: 2100,
      c: 'Undo: apply record. XOR undoes itself, so the same record puts the old positions back.' },
    { k: 'redo', t: 'Redo', d: 2100,
      c: 'Redo: apply record again. One more XOR and the stroke is back.' },
    { k: 'history', t: 'History', d: 2300,
      c: 'Every step adds one tiny record. A full snapshot is kept only when the topology changes, such as a Voxel Remesh.' },
    { k: 'scrub', t: 'Scrub', d: 2900,
      c: 'Dragging the scrubber folds all the records of the drag into one buffer and writes the mesh once.' }
  ];

  var FLAGS = {
    mesh: [],
    stroke: ['changed'],
    record: ['changed', 'copy', 'record', 'count'],
    undo: ['undone', 'copy', 'record', 'count'],
    redo: ['changed', 'copy', 'record', 'count'],
    history: ['changed', 'copy', 'record', 'count', 'history'],
    scrub: ['copy', 'record', 'count', 'history', 'fold'],
    all: ['changed', 'copy', 'record', 'count', 'history', 'fold', 'summary']
  };

  var HIST = [
    { name: 'Draw', pts: 5 },
    { name: 'Voxel Remesh: full snapshot', pts: 48, full: true },
    { name: 'Clay Strips', pts: 9 },
    { name: 'Smooth', pts: 7 },
    { name: 'Inflate', pts: 4 },
    { name: 'This stroke', pts: 6, me: true }
  ];
  var SCRUB_FROM = 5;
  var SCRUB_TO = 1;

  var uid = 'hsd2-' + Math.random().toString(36).slice(2, 7);

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function badge(n) {
    return '<span class="hsd2-badge">' + n + '</span>';
  }
  function size(p) {
    return p.full ? '48 pts' : p.pts + ' pts · ' + p.pts * 16 + ' B';
  }

  /* ---------- markup ---------- */
  var chips = PHASES.map(function (p, i) {
    return '<li><button type="button" class="hsd2-chip" data-go="' + i + '" aria-label="Step ' + (i + 1) + ': ' + p.t + '">' +
      '<span class="hsd2-chip-n">' + (i + 1) + '</span><span class="hsd2-chip-t">' + p.t + '</span>' +
      '<i class="hsd2-chip-p" aria-hidden="true"></i></button></li>';
  }).join('') +
    '<li><button type="button" class="hsd2-chip hsd2-chip-all" data-go="all" aria-label="All steps at once">' +
    '<span class="hsd2-chip-t">All</span></button></li>';

  var cells = '';
  for (var i = 0; i < N; i++) {
    var hitAt = HIT.indexOf(i);
    var y = Math.round(Math.sin(i * 1.7) * 2.2 + Math.cos(i * 0.6) * 1.4);
    var col = i % COLS;
    cells += '<span class="hsd2-cell' + (hitAt > -1 ? ' is-hit' : '') + '" style="--y:' + y + 'px;--c:' + col + '">' +
      '<i></i>' + (hitAt > -1 ? '<b>' + i + '</b>' : '') + '</span>';
  }

  var mini = '';
  for (var j = 0; j < N; j++) mini += '<i></i>';

  var pairs = HIT.map(function (ix, k) {
    return '<span class="hsd2-pair" style="--k:' + k + '"><b>' + ix + '</b><code>' + XOR[k] + '</code></span>';
  }).join('');

  var dots = '';
  for (var d = 0; d < 6; d++) dots += '<i style="--k:' + d + '"></i>';

  function rail(cls, n, label, rev) {
    return '<div class="hsd2-flow hsd2-f-' + cls + (rev ? ' hsd2-rev' : '') + '">' +
      '<span class="hsd2-fl">' + badge(n) + label + '</span>' +
      '<span class="hsd2-track">' + dots + '</span></div>';
  }

  var rows = HIST.map(function (h, k) {
    var cls = 'hsd2-row' + (h.full ? ' is-full' : '') + (h.me ? ' is-me' : '') +
      (k > SCRUB_TO && k <= SCRUB_FROM ? ' is-fold' : '');
    var fd = SCRUB_FROM - k; /* fold order: newest first */
    return '<div class="' + cls + '" style="--k:' + k + ';--f:' + fd + '">' +
      '<span class="hsd2-row-n">' + (k + 1) + '</span>' +
      '<span class="hsd2-row-m"><span class="hsd2-row-name">' + esc(h.name) + '</span>' +
      '<span class="hsd2-row-bar"><i style="width:' + Math.max(6, Math.round(h.pts / 48 * 100)) + '%"></i></span></span>' +
      '<span class="hsd2-row-s">' + size(h) + '</span></div>';
  }).join('');

  var ticks = '';
  for (var t = 0; t < N; t++) ticks += '<i></i>';

  var legend = PHASES.map(function (p) {
    return '<li><b>' + p.t + '.</b> ' + esc(p.c) + '</li>';
  }).join('');

  host.innerHTML =
    '<figure class="hsd2" id="' + uid + '" aria-labelledby="' + uid + '-t">' +
    '<figcaption class="hsd2-sr" id="' + uid + '-t">How Hindsight stores sculpt history: small step records instead of full copies</figcaption>' +
    '<div class="hsd2-bar">' +
      '<ol class="hsd2-chips" aria-label="Diagram steps">' + chips + '</ol>' +
      '<div class="hsd2-ctl">' +
        '<button type="button" class="hsd2-btn" data-act="prev" aria-label="Previous step">' +
          '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3 5 8l5 5"/></svg><span>Prev</span></button>' +
        '<button type="button" class="hsd2-btn hsd2-play" data-act="play" aria-pressed="false">' +
          '<svg class="hsd2-i-play" viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.2v9.6L12.6 8z"/></svg>' +
          '<svg class="hsd2-i-pause" viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.5v9M11 3.5v9"/></svg>' +
          '<span>Autoplay</span></button>' +
        '<button type="button" class="hsd2-btn" data-act="next" aria-label="Next step">' +
          '<span>Next</span><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5"/></svg></button>' +
      '</div>' +
    '</div>' +
    '<p class="hsd2-cap" aria-live="polite" aria-atomic="true"><span class="hsd2-cap-n"></span><span class="hsd2-cap-t"></span></p>' +
    '<div class="hsd2-stage" aria-hidden="true">' +
      '<section class="hsd2-panel hsd2-mesh">' +
        '<header class="hsd2-ph">' + badge('1–2') + '<span>Mesh · 48 points</span><em class="hsd2-tag hsd2-written">written once</em></header>' +
        '<div class="hsd2-grid">' + cells + '<span class="hsd2-brush"></span></div>' +
        '<p class="hsd2-note"><span class="hsd2-key hsd2-key-a"></span>changed by the stroke</p>' +
      '</section>' +
      '<div class="hsd2-lane">' +
        rail('store', 3, 'store: old ⊕ new', false) +
        rail('undo', 4, 'undo: apply record', true) +
        rail('redo', 5, 'redo: apply record again', true) +
      '</div>' +
      '<section class="hsd2-panel hsd2-saved">' +
        '<header class="hsd2-ph">' + badge(3) + '<span>What gets saved</span></header>' +
        '<div class="hsd2-copy">' +
          '<div class="hsd2-card-h"><span>Full copy</span><span>48 points · 576 B</span></div>' +
          '<div class="hsd2-mini">' + mini + '</div>' +
          '<svg class="hsd2-x" viewBox="0 0 100 100" preserveAspectRatio="none"><path d="M3 6 97 94M97 6 3 94"/></svg>' +
          '<em class="hsd2-tag hsd2-tag-warn">not stored</em>' +
        '</div>' +
        '<div class="hsd2-rec">' +
          '<div class="hsd2-card-h"><span>Step record</span><span>6 points · 96 B</span></div>' +
          '<div class="hsd2-rec-key"><span class="hsd2-k-i">index</span><span class="hsd2-k-x">old ⊕ new bits</span></div>' +
          '<div class="hsd2-pairs">' + pairs + '</div>' +
          '<p class="hsd2-rule"><span class="hsd2-rule-u">new ⊕ record = old</span><span class="hsd2-rule-r">old ⊕ record = new</span></p>' +
          '<p class="hsd2-empty">No record yet</p>' +
        '</div>' +
      '</section>' +
      '<section class="hsd2-panel hsd2-hist">' +
        '<header class="hsd2-ph">' + badge('6–7') + '<span>History stack</span></header>' +
        '<div class="hsd2-list"><span class="hsd2-rail"></span><span class="hsd2-thumb"></span>' + rows + '</div>' +
        '<div class="hsd2-buf">' +
          '<span class="hsd2-buf-chips"><i style="--k:0"></i><i style="--k:1"></i><i style="--k:2"></i><i style="--k:3"></i></span>' +
          '<span class="hsd2-buf-t">4 records → 1 buffer → 1 mesh write</span>' +
        '</div>' +
      '</section>' +
    '</div>' +
    '<div class="hsd2-count">' +
      '<p class="hsd2-count-t"><b class="hsd2-num">0</b> points stored vs <b>48</b> for a full copy</p>' +
      '<span class="hsd2-ticks" aria-hidden="true">' + ticks + '</span>' +
    '</div>' +
    '<ol class="hsd2-legend">' + legend + '</ol>' +
    '</figure>';

  /* ---------- refs ---------- */
  var fig = host.querySelector('.hsd2');
  var chipBtns = [].slice.call(fig.querySelectorAll('.hsd2-chip'));
  var playBtn = fig.querySelector('[data-act="play"]');
  var capN = fig.querySelector('.hsd2-cap-n');
  var capT = fig.querySelector('.hsd2-cap-t');
  var num = fig.querySelector('.hsd2-num');
  var tickEls = [].slice.call(fig.querySelectorAll('.hsd2-ticks i'));
  var list = fig.querySelector('.hsd2-list');
  var rowEls = [].slice.call(fig.querySelectorAll('.hsd2-row'));
  var thumb = fig.querySelector('.hsd2-thumb');

  var mq = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  var cur = 0;
  var playing = false;
  var visible = !('IntersectionObserver' in window);
  var timer = 0;
  var remaining = 0;
  var startedAt = 0;
  var countRaf = 0;
  var countDelay = 0;

  function reduced() { return !!mq.matches; }
  function now() { return (window.performance && performance.now) ? performance.now() : Date.now(); }

  function setCount(n) {
    num.textContent = n;
    for (var k = 0; k < tickEls.length; k++) tickEls[k].className = k < n ? 'on' : '';
  }

  function runCount(animate) {
    cancelAnimationFrame(countRaf);
    clearTimeout(countDelay);
    if (!animate) { setCount(6); return; }
    setCount(0);
    countDelay = setTimeout(function () {
      var t0 = now();
      (function step() {
        var p = Math.min(1, (now() - t0) / 900);
        setCount(Math.round(p * 6));
        if (p < 1) countRaf = requestAnimationFrame(step);
      })();
    }, 650);
  }

  function placeThumb() {
    var target = rowEls[fig.classList.contains('hsd2-s-fold') ? SCRUB_TO : SCRUB_FROM];
    if (!target) return;
    thumb.style.top = (target.offsetTop + target.offsetHeight / 2) + 'px';
  }

  function render(i, animate) {
    cur = i;
    var key = i === 'all' ? 'all' : PHASES[i].k;
    var flags = FLAGS[key];
    var base = 'hsd2' + flags.map(function (f) { return ' hsd2-s-' + f; }).join('') +
      (playing ? ' hsd2-playing' : '') + (isHalted() ? ' hsd2-halt' : '');
    fig.className = base;
    void fig.offsetWidth; /* restart phase animations */
    fig.className = base + ' hsd2-p-' + key;
    fig.style.setProperty('--hsd2-dur', (i === 'all' ? 0 : PHASES[i].d) + 'ms');

    chipBtns.forEach(function (b) {
      var on = b.getAttribute('data-go') === String(i);
      if (on) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current');
    });

    if (i === 'all') {
      capN.textContent = 'Overview';
      capT.textContent = 'All seven steps at once, numbered below. Use Next or a step button to go through them one by one.';
    } else {
      capN.textContent = (i + 1) + ' / ' + PHASES.length;
      capT.textContent = PHASES[i].c;
    }

    if (flags.indexOf('count') > -1) runCount(animate && key === 'record' && !reduced());
    else { cancelAnimationFrame(countRaf); clearTimeout(countDelay); setCount(0); }

    placeThumb();
  }

  function isHalted() { return !visible || document.hidden; }

  function clearTimer() { clearTimeout(timer); timer = 0; }

  function schedule() {
    clearTimer();
    if (!playing || cur === 'all' || isHalted()) return;
    startedAt = now();
    timer = setTimeout(function () {
      timer = 0;
      remaining = PHASES[(cur + 1) % PHASES.length].d;
      render((cur + 1) % PHASES.length, true);
      schedule();
    }, Math.max(0, remaining));
  }

  function halt() {
    if (timer) remaining -= now() - startedAt;
    clearTimer();
    fig.classList.add('hsd2-halt');
  }

  function resume() {
    if (isHalted()) return;
    fig.classList.remove('hsd2-halt');
    schedule();
  }

  function setPlaying(on) {
    playing = on;
    playBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    fig.classList.toggle('hsd2-playing', on);
    if (!on) { clearTimer(); return; }
    if (cur === 'all') { remaining = PHASES[0].d; render(0, true); }
    else if (remaining <= 0) remaining = PHASES[cur].d;
    resume();
  }

  function go(i) {
    setPlaying(false);
    remaining = i === 'all' ? 0 : PHASES[i].d;
    render(i, !reduced());
  }

  var ORDER = PHASES.map(function (_, k) { return k; }).concat(['all']);
  function step(dir) {
    var at = ORDER.indexOf(cur);
    go(ORDER[(at + dir + ORDER.length) % ORDER.length]);
  }

  /* ---------- events ---------- */
  fig.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('button');
    if (!b || !fig.contains(b)) return;
    var act = b.getAttribute('data-act');
    var to = b.getAttribute('data-go');
    if (act === 'prev') step(-1);
    else if (act === 'next') step(1);
    else if (act === 'play') setPlaying(!playing);
    else if (to !== null) go(to === 'all' ? 'all' : +to);
  });

  fig.addEventListener('keydown', function (e) {
    if (!e.target.closest || !e.target.closest('.hsd2-bar')) return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
  });

  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) {
      visible = entries[entries.length - 1].isIntersecting;
      if (visible) resume(); else halt();
    }, { threshold: 0.2 }).observe(fig);
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) halt(); else resume();
  });

  if ('ResizeObserver' in window) new ResizeObserver(placeThumb).observe(list);
  else window.addEventListener('resize', placeThumb);

  function onMotionPref() {
    if (reduced()) { setPlaying(false); remaining = 0; render('all', false); }
  }
  if (mq.addEventListener) mq.addEventListener('change', onMotionPref);
  else if (mq.addListener) mq.addListener(onMotionPref);

  /* ---------- start ---------- */
  if (reduced()) {
    render('all', false);
  } else {
    remaining = PHASES[0].d;
    render(0, true);
    setPlaying(true);
  }
})();
