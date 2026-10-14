#!/usr/bin/env node
/*
 * Site QA for the Hindsight website (docs/), served exactly like GitHub Pages
 * under the /Hindsight/ base path. Prints PASS/FAIL/INFO lines and exits 1 on any FAIL.
 *
 *   node tests/site/site_qa.js            # screenshots go to $QA_OUT or ./tests/site/out
 */
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');
const { chromium } = require('/opt/node-tools/node_modules/playwright');

const DOCS = path.resolve(__dirname, '../../docs');
const OUT = process.env.QA_OUT || path.join(__dirname, 'out');
const CHROME = process.env.QA_CHROME || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const VERSION = '1.0.0-beta.1';
const PROD = 'https://min3scon.github.io/Hindsight/';
const EXTERNAL_OK = /^https:\/\/github\.com\/Min3scon\/Hindsight(?:[\/?#].*)?$/;
fs.mkdirSync(OUT, { recursive: true });

/* ----------------------------------------------------------------- results */
let fails = 0, passes = 0;
const failList = [];
function check(area, name, ok, detail) {
  const line = `${ok ? 'PASS' : 'FAIL'} [${area}] ${name}${detail !== undefined && detail !== '' ? ' -- ' + (typeof detail === 'string' ? detail : JSON.stringify(detail)) : ''}`;
  console.log(line);
  if (ok) passes++; else { fails++; failList.push(line); }
  return ok;
}
function info(area, msg) { console.log(`INFO [${area}] ${msg}`); }
const short = (a, n = 6) => (a.length > n ? a.slice(0, n).concat([`...(+${a.length - n})`]) : a);

/* ---------------------------------------------------------- static server */
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.avif': 'image/avif',
  '.gif': 'image/gif', '.ico': 'image/x-icon', '.json': 'application/json', '.xml': 'application/xml', '.txt': 'text/plain; charset=utf-8',
  '.zip': 'application/zip', '.woff2': 'font/woff2', '.woff': 'font/woff', '.mp4': 'video/mp4', '.webm': 'video/webm', '.webmanifest': 'application/manifest+json' };
function startServer() {
  const server = http.createServer((req, res) => {
    const u = new URL(req.url, 'http://x');
    let p;
    try { p = decodeURIComponent(u.pathname); } catch (e) { p = '/'; }
    const send404 = () => {
      const f = path.join(DOCS, '404.html');
      res.writeHead(404, { 'content-type': 'text/html; charset=utf-8' });
      res.end(fs.existsSync(f) ? fs.readFileSync(f) : 'Not found');
    };
    if (!p.startsWith('/Hindsight/')) {
      if (p === '/Hindsight') { res.writeHead(301, { location: '/Hindsight/' }); return res.end(); }
      return send404();
    }
    let file = path.normalize(path.join(DOCS, p.slice('/Hindsight/'.length)));
    if (file !== DOCS && !file.startsWith(DOCS + path.sep)) return send404();
    let st = null;
    try { st = fs.statSync(file); } catch (e) { /* missing */ }
    if (st && st.isDirectory()) {
      if (!p.endsWith('/')) { res.writeHead(301, { location: p + '/' }); return res.end(); }
      file = path.join(file, 'index.html');
      try { st = fs.statSync(file); } catch (e) { st = null; }
    }
    if (!st || !st.isFile()) return send404();
    res.writeHead(200, { 'content-type': TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream', 'content-length': st.size, 'cache-control': 'no-store' });
    if (req.method === 'HEAD') return res.end();
    fs.createReadStream(file).pipe(res);
  });
  return new Promise(r => server.listen(0, '127.0.0.1', () => r(server)));
}
async function get(url) {
  const r = await fetch(url, { redirect: 'follow' });
  return { status: r.status, buf: Buffer.from(await r.arrayBuffer()), type: r.headers.get('content-type') || '' };
}

/* ------------------------------------------------------------ zip reading */
function readZip(buf) {
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65557); i--) if (buf.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  if (eocd < 0) throw new Error('no end-of-central-directory record');
  const count = buf.readUInt16LE(eocd + 10);
  let off = buf.readUInt32LE(eocd + 16);
  const entries = [];
  for (let k = 0; k < count; k++) {
    if (buf.readUInt32LE(off) !== 0x02014b50) throw new Error('bad central directory entry');
    const method = buf.readUInt16LE(off + 10), csize = buf.readUInt32LE(off + 20), nlen = buf.readUInt16LE(off + 28),
      xlen = buf.readUInt16LE(off + 30), clen = buf.readUInt16LE(off + 32), lho = buf.readUInt32LE(off + 42);
    const name = buf.slice(off + 46, off + 46 + nlen).toString('utf8');
    entries.push({ name, method, csize, lho });
    off += 46 + nlen + xlen + clen;
  }
  const read = e => {
    const n = buf.readUInt16LE(e.lho + 26), x = buf.readUInt16LE(e.lho + 28);
    const data = buf.slice(e.lho + 30 + n + x, e.lho + 30 + n + x + e.csize);
    return e.method === 0 ? data : zlib.inflateRawSync(data);
  };
  return { entries, read };
}

/* -------------------------------------------------------- page utilities */
function watch(page) {
  const w = { errors: [], bad: [], bytes: 0, urls: [] };
  page.on('pageerror', e => w.errors.push('pageerror: ' + String(e).slice(0, 200)));
  page.on('console', m => { if (m.type() === 'error' && !/^Failed to load resource/.test(m.text())) w.errors.push('console: ' + m.text().slice(0, 200)); });
  page.on('requestfailed', r => { if (!/^data:|^blob:/.test(r.url())) w.bad.push('failed ' + r.url() + ' ' + (r.failure() || {}).errorText); });
  page.on('response', async r => {
    w.urls.push(r.url());
    if (r.status() >= 400) w.bad.push(r.status() + ' ' + r.url());
    if (/\.zip(\?|$)/.test(r.url())) return;
    try { w.bytes += (await r.body()).length; } catch (e) { /* redirects etc. */ }
  });
  w.mark = () => ({ e: w.errors.length, b: w.bad.length });
  w.since = m => w.errors.slice(m.e).concat(w.bad.slice(m.b));
  return w;
}
async function settle(page, ms = 600) {
  await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

/* The demo helpers run in the page. They find a point on the clay outline (via the
 * optional toCanvas() helper) so strokes really touch the shape. */
const PAGE_HELPERS = () => {
  window.__qaOverflow = VW => {
    const out = [];
    for (const e of document.body.querySelectorAll('*')) {
      const r = e.getBoundingClientRect();
      if (r.right <= VW + 1 || r.width === 0) continue;
      const p = e.parentElement; const pr = p && p.getBoundingClientRect();
      if (p && p !== document.body && pr.right > VW + 1) continue; // report the outermost overflowing element only
      let clip = false; for (let a = p; a && a !== document.body; a = a.parentElement) { const ox = getComputedStyle(a).overflowX; if (ox !== 'visible') { clip = true; break; } }
      if (clip) continue;
      out.push(e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className.trim() ? '.' + e.className.trim().split(/\s+/).join('.') : '') + ` right=${Math.round(r.right)}`);
    }
    return out.slice(0, 5);
  };
  window.__qa = {
    canvas() {
      return document.querySelector('#hs-demo canvas.hsd-canvas') || document.querySelector('#hs-demo canvas') || document.querySelector('#demo canvas');
    },
    outline(k) { // canvas-space coordinates of outline point k (fraction 0..1 around the shape)
      const D = window.HindsightDemo, s = D.snapshot(), n = s.length / 2, i = Math.floor(((k % 1) + 1) % 1 * n);
      if (typeof D.toCanvas === 'function') return D.toCanvas(s[2 * i], s[2 * i + 1]);
      const c = this.canvas().getBoundingClientRect();
      return [c.width * (0.3 + 0.4 * (k % 1)), c.height * 0.5];
    },
    pts(k, n, dx, dy) { const [x, y] = this.outline(k); const out = []; for (let i = 0; i < n; i++) out.push([x + (i - n / 2) * dx, y + (i - n / 2) * dy]); return out; },
  };
};

async function canvasBox(page) {
  const h = await page.evaluateHandle(() => window.__qa.canvas());
  const el = h.asElement();
  if (!el) return null;
  await el.scrollIntoViewIfNeeded().catch(() => {});
  await page.waitForTimeout(150);
  return el.boundingBox();
}

/* -------------------------------------------------------------- the suite */
(async () => {
  const server = await startServer();
  const BASE = `http://127.0.0.1:${server.address().port}/Hindsight/`;
  info('setup', `serving ${DOCS} at ${BASE}; screenshots -> ${OUT}`);
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });
  const watchers = {};
  try {
    /* ============ 1. viewports ============ */
    const VPS = [
      ['phone', { viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 }],
      ['tablet', { viewport: { width: 820, height: 1180 }, hasTouch: true }],
      ['desktop', { viewport: { width: 1440, height: 900 } }],
    ];
    let zipSizeShown = null;
    for (const [name, opts] of VPS) {
      const A = `layout ${name}`;
      const ctx = await browser.newContext(opts);
      await ctx.addInitScript(PAGE_HELPERS);
      const page = await ctx.newPage();
      const w = watch(page); watchers[name] = w;
      const resp = await page.goto(BASE, { waitUntil: 'load', timeout: 30000 }).catch(e => ({ status: () => 'goto error ' + e.message }));
      await settle(page);
      check(A, 'index.html loads with 200', resp.status() === 200, String(resp.status()));
      const firstLoadBytes = w.bytes;
      const s = await page.evaluate(VW => {
        const vis = el => { if (!el) return false; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
          return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity > 0.05 && r.right > 0 && r.left < innerWidth; };
        const ownText = el => (el.textContent || '').replace(/\s+/g, ' ').trim();
        const h1 = document.querySelector('h1');
        const nav = document.querySelector('nav');
        const navRoot = nav ? (nav.closest('header') || nav) : (document.querySelector('header'));
        const hero = h1 ? ((h1.parentElement && h1.parentElement.closest('.hero, #hero, [class~="hero"], section, header')) || h1.parentElement) : null;
        const badgeIn = (root, notIn) => root ? [...root.querySelectorAll('*')].filter(e => !(notIn && notIn !== root && notIn.contains(e)) && /^(public\s+)?beta$/i.test(ownText(e)) && vis(e)) : [];
        const navBadges = badgeIn(navRoot, hero && hero !== navRoot && !hero.contains(navRoot) ? hero : null);
        const heroBadges = hero && !hero.contains(navRoot) ? badgeIn(hero) : (hero ? badgeIn(hero, navRoot) : []);
        const bannerCands = [...document.querySelectorAll('body *')].filter(e => /Hindsight is in public beta/i.test(e.textContent || ''));
        const banner = bannerCands[bannerCands.length - 1];
        const dls = [...document.querySelectorAll('a')].filter(a => /download\s+beta/i.test(a.textContent || '') || /download\s+beta/i.test(a.getAttribute('aria-label') || ''));
        const dl = dls.map(a => ({ href: a.getAttribute('href'), download: a.hasAttribute('download'), text: ownText(a).slice(0, 90),
          parentText: ownText(a.parentElement).slice(0, 200), visible: vis(a) }));
        return { sw: document.documentElement.scrollWidth, bw: document.body.scrollWidth, iw: VW, overflow: window.__qaOverflow(VW),
          navBadge: navBadges.length > 0, heroBadge: heroBadges.length > 0, heroTag: hero ? hero.tagName + '.' + hero.className : null,
          banner: !!banner, bannerVisible: vis(banner), dl };
      }, opts.viewport.width);
      check(A, 'no console errors / page errors', w.errors.length === 0, short(w.errors, 4));
      check(A, 'no HTTP 4xx/5xx or failed requests', w.bad.length === 0, short(w.bad, 6));
      check(A, `no horizontal scroll (scrollWidth <= ${s.iw})`, s.sw <= s.iw && s.bw <= s.iw, `scrollWidth=${s.sw} body=${s.bw}; widest overflowing elements: ${s.overflow.join(' | ')}`);
      check(A, 'nav BETA badge visible', s.navBadge);
      check(A, 'hero BETA badge visible', s.heroBadge, s.heroTag || 'no h1/hero found');
      check(A, 'beta banner "Hindsight is in public beta" visible', s.bannerVisible, s.bannerVisible ? '' : (s.banner ? 'present but not visible' : 'text not found'));
      check(A, '"Download beta" button exists', s.dl.length > 0);
      if (s.dl.length) {
        const badHref = s.dl.filter(d => d.href !== 'download/hindsight.zip' || !d.download);
        check(A, 'all "Download beta" links: href="download/hindsight.zip" + download attr', badHref.length === 0, badHref);
        check(A, 'a "Download beta" button is visible', s.dl.some(d => d.visible));
        const sizeRe = /(\d+(?:[.,]\d+)?)\s?(KB|kB|MB|KiB|MiB)\b/;
        const good = s.dl.filter(d => d.text.includes(VERSION) && sizeRe.test(d.text));
        const near = s.dl.filter(d => d.parentText.includes(VERSION) && sizeRe.test(d.parentText));
        check(A, `"Download beta" shows ${VERSION} and a size`, good.length > 0,
          good.length ? '' : (near.length ? 'only in the surrounding text, not the button: ' + near[0].parentText : s.dl.map(d => d.text)));
        const m = (good[0] || near[0] || {}).text && sizeRe.exec((good[0] || near[0]).text) || (near[0] && sizeRe.exec(near[0].parentText));
        if (m) zipSizeShown = { n: parseFloat(m[1].replace(',', '.')), unit: m[2] };
      }
      await page.screenshot({ path: path.join(OUT, `${opts.viewport.width}-top.png`) });
      const m1 = w.mark();
      // scroll the whole page so lazy content/IntersectionObserver stuff loads, then full-page shot
      await page.evaluate(async () => { for (let y = 0; y < document.documentElement.scrollHeight; y += innerHeight * 0.8) { scrollTo(0, y); await new Promise(r => setTimeout(r, 120)); } scrollTo(0, 0); });
      await settle(page, 400);
      const sw2 = await page.evaluate(VW => [document.documentElement.scrollWidth, VW, window.__qaOverflow(VW)], opts.viewport.width);
      check(A, 'no horizontal scroll after scrolling through the page', sw2[0] <= sw2[1], `scrollWidth=${sw2[0]} viewport=${sw2[1]}; ${sw2[2].join(' | ')}`);
      check(A, 'no NEW errors / 4xx after scrolling through the page', w.since(m1).length === 0, short(w.since(m1), 4));
      await page.screenshot({ path: path.join(OUT, `${opts.viewport.width}-full.png`), fullPage: true }).catch(e => info(A, 'full screenshot failed ' + e.message));

      /* ============ 8. performance (desktop first load) ============ */
      if (name === 'desktop') {
        const P = 'perf';
        check(P, 'first-load bytes (non-zip) < 1 MB', firstLoadBytes < 1024 * 1024, `${(firstLoadBytes / 1024).toFixed(0)} KB first load, ${(w.bytes / 1024).toFixed(0)} KB after scrolling`);
        const pr = await page.evaluate(() => ({
          blocking: [...document.head.querySelectorAll('script[src]')].filter(s => !s.defer && !s.async && s.type !== 'module').map(s => s.getAttribute('src')),
          inlineHead: [...document.head.querySelectorAll('script:not([src])')].filter(s => s.type !== 'application/ld+json').map(s => s.textContent.length),
          imgs: [...document.querySelectorAll('img')].filter(i => !i.hasAttribute('alt') || !i.getAttribute('width') || !i.getAttribute('height')).map(i => (i.getAttribute('src') || '').slice(0, 60) + (i.hasAttribute('alt') ? '' : ' [no alt]') + (i.getAttribute('width') && i.getAttribute('height') ? '' : ' [no width/height]')),
          nImgs: document.querySelectorAll('img').length,
        }));
        check(P, 'no render-blocking <script src> in <head> without defer/async', pr.blocking.length === 0, pr.blocking);
        if (pr.inlineHead.length) info(P, `inline <script> blocks in <head> (bytes): ${pr.inlineHead.join(', ')}`);
        check(P, `every <img> has alt + width/height (${pr.nImgs} imgs)`, pr.imgs.length === 0, short(pr.imgs, 5));
      }

      /* ============ 4. demo (desktop + phone) ============ */
      if (name === 'desktop' || name === 'phone') await demoChecks(page, ctx, name);

      /* ============ 5. unmerge (desktop) ============ */
      if (name === 'desktop') {
        const U = 'unmerge';
        const um = await page.evaluate(() => {
          const X = window.HindsightExtras && window.HindsightExtras.unmerge;
          if (!X) return { missing: true };
          const j = X.joinedAt(), n = X.steps(), p0 = X.pos();
          const r = {};
          X.goto(0); r.at0 = X.isJoined();
          X.goto(j - 1); r.before = X.isJoined(); r.posBefore = X.pos();
          X.goto(j); r.at = X.isJoined();
          X.goto(n); r.end = X.isJoined(); r.posEnd = X.pos();
          X.goto(p0);
          return { j, n, ...r };
        }).catch(e => ({ error: e.message }));
        if (check(U, 'window.HindsightExtras.unmerge exists', !um.missing && !um.error, um.error)) {
          check(U, 'joinedAt() is a step inside 1..steps()', um.j >= 1 && um.j <= um.n, um);
          check(U, 'isJoined() false before joinedAt() (goto(joinedAt-1) and goto(0))', um.before === false && um.at0 === false, um);
          check(U, 'isJoined() true at/after joinedAt() (goto(joinedAt) and goto(steps))', um.at === true && um.end === true, um);
        }
        // a11y on desktop
        await a11yChecks(page);
      }
      check(A, 'no NEW console/page errors during the interaction tests', w.errors.slice(m1.e).length === 0, short(w.errors.slice(m1.e), 4));
      await ctx.close();
    }

    /* ============ 6. reduced motion ============ */
    {
      const R = 'reduced-motion';
      const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
      await ctx.addInitScript(() => { window.__raf = 0; const o = window.requestAnimationFrame; window.requestAnimationFrame = f => { window.__raf++; return o.call(window, f); }; });
      const page = await ctx.newPage();
      const w = watch(page);
      await page.goto(BASE, { waitUntil: 'load' });
      await settle(page);
      await page.evaluate(async () => { for (let y = 0; y < document.documentElement.scrollHeight; y += innerHeight * 0.8) { scrollTo(0, y); await new Promise(r => setTimeout(r, 80)); } scrollTo(0, 0); });
      await page.waitForTimeout(500);
      const rm = await page.evaluate(async () => {
        const inf = document.getAnimations().filter(a => a.playState === 'running' && a.effect && a.effect.getComputedTiming().iterations === Infinity)
          .map(a => { const t = a.effect.target; return (a.animationName || a.constructor.name) + ' on ' + (t ? t.tagName.toLowerCase() + (t.id ? '#' + t.id : '') + (t.className && typeof t.className === 'string' ? '.' + t.className.trim().split(/\s+/).join('.') : '') : '?'); });
        const r0 = window.__raf; await new Promise(r => setTimeout(r, 1500)); const raf = window.__raf - r0;
        const h1 = document.querySelector('h1'); const hr = h1 && h1.getBoundingClientRect();
        const videos = [...document.querySelectorAll('video')].filter(v => !v.paused).length;
        return { inf, raf, h1: !!(hr && hr.width > 0 && hr.height > 0), bodyH: document.body.scrollHeight, videos };
      });
      check(R, 'no running infinite CSS animations', rm.inf.length === 0, short(rm.inf, 6));
      check(R, 'page still renders (visible h1, non-trivial height)', rm.h1 && rm.bodyH > 1000, `bodyHeight=${rm.bodyH}`);
      check(R, 'no errors / 4xx with reduced motion', w.errors.length === 0 && w.bad.length === 0, short(w.errors.concat(w.bad), 4));
      info(R, `requestAnimationFrame calls during 1.5 s idle: ${rm.raf} (a continuous JS loop would be ~90); playing <video>s: ${rm.videos}`);
      if (rm.raf > 45) check(R, 'no continuous JS animation loop while idle', false, `${rm.raf} rAF calls in 1.5 s`);
      await page.screenshot({ path: path.join(OUT, 'reduced-motion-top.png') });
      await ctx.close();
    }

    /* ============ 2. links ============ */
    {
      const L = 'links';
      const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const page = await ctx.newPage();
      const pages = ['', '404.html'];
      const internal = new Map(), external = new Map(), absolute = [];
      const idsByPage = {};
      for (const pg of pages) {
        const url = BASE + pg;
        const r = await page.goto(url, { waitUntil: 'load' }).catch(() => null);
        if (!r || r.status() !== 200) { check(L, `${pg || 'index.html'} reachable for link scan`, false, r ? r.status() : 'error'); continue; }
        await settle(page, 300);
        const d = await page.evaluate(() => ({
          links: [...document.querySelectorAll('a[href]')].map(a => ({ raw: a.getAttribute('href'), abs: a.href })),
          absolute: [...document.querySelectorAll('[href^="/"],[src^="/"],[srcset^="/"],[poster^="/"]')].map(e => e.outerHTML.slice(0, 90)),
          ids: [...document.querySelectorAll('[id]')].map(e => e.id).concat([...document.querySelectorAll('a[name]')].map(e => e.name)),
        }));
        idsByPage[url.split('#')[0]] = new Set(d.ids);
        absolute.push(...d.absolute.map(x => (pg || 'index') + ': ' + x));
        for (const l of d.links) {
          if (/^(mailto:|tel:|javascript:)/i.test(l.raw)) { external.set(l.raw, pg || 'index'); continue; }
          if (l.abs.startsWith(BASE.replace(/\/Hindsight\/$/, ''))) internal.set(l.abs, { from: pg || 'index', raw: l.raw });
          else external.set(l.abs, pg || 'index');
        }
      }
      // raw source scan (HTML + CSS) for root-absolute URLs that would break under /Hindsight/
      const srcFiles = fs.readdirSync(DOCS).filter(f => /\.html$/.test(f)).map(f => path.join(DOCS, f))
        .concat(fs.existsSync(path.join(DOCS, 'assets')) ? fs.readdirSync(path.join(DOCS, 'assets')).filter(f => /\.(css|html|svg)$/.test(f)).map(f => path.join(DOCS, 'assets', f)) : []);
      for (const f of srcFiles) {
        const t = fs.readFileSync(f, 'utf8');
        const re = /\b(?:href|src|poster|action)\s*=\s*["']\/(?!\/)[^"']*["']|url\(\s*["']?\/(?!\/)[^)"']*/g;
        let m; while ((m = re.exec(t))) absolute.push(path.relative(DOCS, f) + ': ' + m[0].slice(0, 80));
      }
      check(L, 'no href/src starting with "/" (DOM + HTML/CSS sources)', absolute.length === 0, short(absolute, 6));
      const broken = [];
      for (const [u, meta] of internal) {
        const [noHash, hash] = u.split('#');
        let st;
        try { st = (await fetch(noHash, { method: 'GET' })).status; } catch (e) { st = 'ERR'; }
        if (st !== 200) { broken.push(`${meta.raw} (${meta.from}) -> ${st}`); continue; }
        if (hash) {
          let ids = idsByPage[noHash];
          if (!ids && /\.html?$|\/$/.test(noHash)) {
            const p2 = await ctx.newPage(); await p2.goto(noHash, { waitUntil: 'load' }).catch(() => {});
            ids = new Set(await p2.evaluate(() => [...document.querySelectorAll('[id]')].map(e => e.id)).catch(() => []));
            idsByPage[noHash] = ids; await p2.close();
          }
          if (ids && !ids.has(decodeURIComponent(hash))) broken.push(`${meta.raw} (${meta.from}) -> missing id "${hash}"`);
        }
      }
      check(L, `internal links resolve (${internal.size} unique)`, broken.length === 0, short(broken, 8));
      const badExt = [...external.keys()].filter(u => !EXTERNAL_OK.test(u));
      check(L, `external links only to github.com/Min3scon/Hindsight* (${external.size} unique)`, badExt.length === 0, badExt);
      info(L, 'external: ' + [...external.keys()].join(' '));
      const want = ['/issues/new/choose', '/releases', 'LICENSE'];
      for (const w0 of want) check(L, `has a link to the repo ${w0}`, [...external.keys()].some(u => EXTERNAL_OK.test(u) && u.includes(w0)));
      await ctx.close();
    }

    /* ============ 3. download ============ */
    {
      const Z = 'download';
      const r = await get(BASE + 'download/hindsight.zip');
      check(Z, 'GET download/hindsight.zip -> 200', r.status === 200, r.status);
      if (r.status === 200) {
        check(Z, 'zip starts with "PK"', r.buf.slice(0, 2).toString('latin1') === 'PK');
        try {
          const z = readZip(r.buf);
          const man = z.entries.find(e => e.name === 'blender_manifest.toml');
          check(Z, 'blender_manifest.toml at zip root', !!man, short(z.entries.map(e => e.name), 6));
          if (man) {
            const toml = z.read(man).toString('utf8');
            const v = (/^\s*version\s*=\s*"([^"]+)"/m.exec(toml) || [])[1];
            check(Z, `manifest version == ${VERSION}`, v === VERSION, v);
          }
          info(Z, `${r.buf.length} bytes, ${z.entries.length} entries`);
        } catch (e) { check(Z, 'zip central directory parses', false, e.message); }
        if (zipSizeShown) {
          const mult = /^M/i.test(zipSizeShown.unit) ? 1024 * 1024 : 1024;
          const shown = zipSizeShown.n * mult, ratio = shown / r.buf.length;
          check(Z, 'size shown on the button matches the real zip (+-20%)', ratio > 0.8 && ratio < 1.25, `shown ${zipSizeShown.n} ${zipSizeShown.unit}, real ${(r.buf.length / 1024).toFixed(1)} KB`);
        }
      }
    }

    /* ============ 7. meta ============ */
    {
      const M = 'meta';
      const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const page = await ctx.newPage();
      await page.goto(BASE, { waitUntil: 'load' });
      const m = await page.evaluate(() => {
        const c = s => { const e = document.querySelector(s); return e ? (e.getAttribute('content') || e.getAttribute('href')) : null; };
        return { title: document.title, ogTitle: c('meta[property="og:title"]'), ogImage: c('meta[property="og:image"]'), ogUrl: c('meta[property="og:url"]'),
          twCard: c('meta[name="twitter:card"]'), canonical: c('link[rel="canonical"]'), icon: c('link[rel~="icon"]'), apple: c('link[rel="apple-touch-icon"]'),
          desc: c('meta[name="description"]'), lang: document.documentElement.lang, ogW: c('meta[property="og:image:width"]'), ogH: c('meta[property="og:image:height"]') };
      });
      check(M, '<title> contains "beta"', /beta/i.test(m.title || ''), m.title);
      check(M, 'og:title contains "beta"', /beta/i.test(m.ogTitle || ''), m.ogTitle);
      check(M, 'meta description present', !!(m.desc && m.desc.length > 30), m.desc && m.desc.slice(0, 60));
      check(M, 'twitter:card present', !!m.twCard, m.twCard);
      check(M, 'canonical link present (production URL)', !!m.canonical && m.canonical.startsWith(PROD), m.canonical);
      check(M, 'og:image present and absolute https', !!m.ogImage && /^https:\/\//.test(m.ogImage), m.ogImage);
      if (m.ogImage && m.ogImage.startsWith(PROD)) {
        const local = BASE + m.ogImage.slice(PROD.length);
        const r = await get(local);
        check(M, 'og:image file exists on the site', r.status === 200, `${m.ogImage} -> ${r.status}`);
        check(M, 'og:image is a PNG (SVG previews are ignored by most social sites)', /\.png$/i.test(m.ogImage), m.ogImage);
      }
      check(M, 'favicon link present', !!m.icon, m.icon);
      if (m.icon) check(M, 'favicon resolves', (await get(new URL(m.icon, BASE).href)).status === 200, m.icon);
      check(M, 'apple-touch-icon link present and resolves', !!m.apple && (await get(new URL(m.apple, BASE).href)).status === 200, m.apple);
      check(M, '<html lang> set', !!m.lang, m.lang);
      const og = await get(BASE + 'og.png');
      if (check(M, 'og.png -> 200', og.status === 200, og.status)) {
        const isPng = og.buf.slice(1, 4).toString('latin1') === 'PNG';
        const W = isPng ? og.buf.readUInt32BE(16) : 0, H = isPng ? og.buf.readUInt32BE(20) : 0;
        check(M, 'og.png is 1200x630', isPng && W === 1200 && H === 630, isPng ? `${W}x${H}` : 'not a PNG');
      }
      for (const f of ['sitemap.xml', 'robots.txt']) {
        const r = await get(BASE + f);
        check(M, `${f} -> 200`, r.status === 200, r.status);
        if (r.status === 200 && f === 'sitemap.xml') check(M, 'sitemap.xml lists the production URL', r.buf.toString().includes(PROD), '');
        if (r.status === 200 && f === 'robots.txt') check(M, 'robots.txt points to the sitemap', /sitemap:\s*https:\/\/min3scon\.github\.io\/Hindsight\/sitemap\.xml/i.test(r.buf.toString()), '');
      }
      const nf = await get(BASE + 'does-not-exist');
      const has404 = fs.existsSync(path.join(DOCS, '404.html'));
      check(M, 'GET /Hindsight/does-not-exist -> 404', nf.status === 404, nf.status);
      check(M, 'custom docs/404.html served for missing paths', has404 && nf.buf.equals(fs.readFileSync(path.join(DOCS, '404.html'))), has404 ? '' : 'docs/404.html missing');
      if (has404) {
        const w = watch(page);
        await page.goto(BASE + 'does-not-exist', { waitUntil: 'load' });
        await settle(page, 300);
        const d = await page.evaluate(() => ({ home: [...document.querySelectorAll('a[href]')].some(a => /^(\.\/|\/Hindsight\/|https:\/\/min3scon\.github\.io\/Hindsight\/?$|index\.html|\.\/index\.html)$/.test(a.getAttribute('href')) || a.href.replace(/index\.html$/, '') === location.origin + '/Hindsight/'),
          title: document.title, sw: document.documentElement.scrollWidth, iw: innerWidth }));
        check(M, '404 page renders without errors / 4xx subresources', w.errors.length === 0 && w.bad.filter(b => !/does-not-exist/.test(b)).length === 0, short(w.errors.concat(w.bad.filter(b => !/does-not-exist/.test(b))), 4));
        check(M, '404 page links back to the home page', d.home, d.title);
        await page.screenshot({ path: path.join(OUT, '404.png') });
      }
      await ctx.close();
    }
  } catch (e) {
    check('suite', 'suite ran to completion', false, (e && e.stack || String(e)).split('\n').slice(0, 3).join(' | '));
  } finally {
    await browser.close().catch(() => {});
    server.close();
  }
  console.log(`\n${passes} passed, ${fails} failed`);
  if (fails) { console.log('\nFailures:'); failList.forEach(l => console.log('  ' + l)); }
  process.exit(fails ? 1 : 0);

  /* ------------------------------------------------------------- demo */
  async function demoChecks(page, ctx, vp) {
    const D = `demo ${vp}`;
    const has = await page.evaluate(() => !!window.HindsightDemo && !!window.__qa.canvas());
    if (!check(D, 'window.HindsightDemo exists and #hs-demo has a canvas', has)) return;
    const box = await canvasBox(page);
    if (!check(D, 'demo canvas has a size', !!box && box.width > 100 && box.height > 100, box)) return;
    await page.evaluate(() => { const H = window.HindsightDemo; if (H.isPlaying()) H.pause && H.pause(); H.goto(H.steps()); });
    const n0 = await page.evaluate(() => window.HindsightDemo.steps());
    const snaps = [await page.evaluate(() => window.HindsightDemo.snapshot())];
    const deltas = [];
    // 4 real mouse drags along the outline of the clay
    for (let k = 0; k < 4; k++) {
      const b = await canvasBox(page);
      const pts = await page.evaluate(k => window.__qa.pts(0.1 + k * 0.22, 10, 4, 3), k);
      const before = await page.evaluate(() => window.HindsightDemo.steps());
      await page.mouse.move(b.x + pts[0][0], b.y + pts[0][1]);
      await page.mouse.down();
      for (const p of pts.slice(1)) await page.mouse.move(b.x + p[0], b.y + p[1], { steps: 2 });
      await page.mouse.up();
      await page.waitForTimeout(120);
      const after = await page.evaluate(() => window.HindsightDemo.steps());
      deltas.push(after - before);
      snaps.push(await page.evaluate(() => window.HindsightDemo.snapshot()));
    }
    check(D, '4 mouse drag strokes: steps +1 each', deltas.every(d => d === 1), `deltas ${deltas.join(',')} (start ${n0})`);
    const changed = snaps.slice(1).map((s, i) => s.some((v, j) => v !== snaps[i][j]));
    check(D, 'each mouse stroke changed the shape', changed.every(Boolean), changed);
    if (vp === 'phone') {
      const b = await canvasBox(page);
      const pts = await page.evaluate(() => window.__qa.pts(0.5, 10, 4, 3));
      const before = await page.evaluate(() => window.HindsightDemo.steps());
      const cdp = await ctx.newCDPSession(page);
      const tp = p => ({ x: b.x + p[0], y: b.y + p[1], id: 1, radiusX: 2, radiusY: 2, force: 1 });
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [tp(pts[0])] });
      for (const p of pts.slice(1)) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [tp(p)] });
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      await page.waitForTimeout(200);
      const after = await page.evaluate(() => window.HindsightDemo.steps());
      const sy = await page.evaluate(() => scrollY);
      check(D, 'touch drag (CDP) on the canvas adds exactly one step', after - before === 1, `steps ${before} -> ${after}`);
      info(D, `scrollY after touch drag: ${sy}`);
      snaps.push(await page.evaluate(() => window.HindsightDemo.snapshot()));
    }
    // exact float equality going back and forth
    const nS = snaps.length - 1;
    const exact = await page.evaluate(({ n0, snaps }) => {
      const H = window.HindsightDemo, bad = [];
      const same = (a, b) => a.length === b.length && a.every((v, i) => v === b[i] || (v !== v && b[i] !== b[i]));
      for (let k = snaps.length - 1; k >= 0; k--) { H.goto(n0 + k); if (!same(H.snapshot(), snaps[k])) bad.push('back ' + k); }
      for (let k = 0; k < snaps.length; k++) { H.goto(n0 + k); if (!same(H.snapshot(), snaps[k])) bad.push('fwd ' + k); }
      for (const k of [2, 0, snaps.length - 1, 1, 3]) { if (k < snaps.length) { H.goto(n0 + k); if (!same(H.snapshot(), snaps[k])) bad.push('jump ' + k); } }
      return bad;
    }, { n0, snaps });
    check(D, `goto() back/forward/jumps reproduce snapshots exactly (${nS} strokes)`, exact.length === 0, exact);
    // keyboard on the on-screen scrubber
    const sel = await page.evaluate(() => {
      const c = ['#hs-demo .hsd-track', '#hs-demo [role="slider"]', '#hs-demo input[type="range"][aria-label*="istory" i]', '#hs-demo input[type="range"][aria-label*="scrub" i]'];
      return c.find(s => document.querySelector(s)) || null;
    });
    if (check(D, 'on-screen history scrubber found', !!sel, sel)) {
      const kb = [];
      await page.evaluate(() => { const H = window.HindsightDemo; H.goto(H.steps()); });
      await page.focus(sel);
      const posNow = () => page.evaluate(() => window.HindsightDemo.pos());
      const total = await page.evaluate(() => window.HindsightDemo.steps());
      const p0 = await posNow();
      await page.keyboard.press('ArrowLeft'); const pL = await posNow();
      await page.keyboard.press('ArrowLeft'); const pL2 = await posNow();
      await page.keyboard.press('ArrowRight'); const pR = await posNow();
      await page.keyboard.press('Home'); const pH = await posNow();
      await page.keyboard.press('End'); const pE = await posNow();
      kb.push({ p0, pL, pL2, pR, pH, pE, total });
      check(D, 'scrubber keys: ArrowLeft -1, ArrowRight +1, Home -> 0, End -> last', pL === p0 - 1 && pL2 === p0 - 2 && pR === p0 - 1 && pH === 0 && pE === total, kb[0]);
      await page.waitForTimeout(150);
      const aria = await page.evaluate(s => { const e = document.querySelector(s); return { now: e.getAttribute('aria-valuenow') || e.value, max: e.getAttribute('aria-valuemax') || e.max }; }, sel);
      check(D, 'scrubber exposes aria-valuenow/max matching pos()/steps()', +aria.now === pE && +aria.max === total, aria);
      // scrub back with the keyboard, then stroke: future truncated
      await page.keyboard.press('ArrowLeft'); await page.keyboard.press('ArrowLeft');
      const back = await posNow();
      const b = await canvasBox(page);
      const pts = await page.evaluate(() => window.__qa.pts(0.75, 8, 4, 3));
      await page.mouse.move(b.x + pts[0][0], b.y + pts[0][1]); await page.mouse.down();
      for (const p of pts.slice(1)) await page.mouse.move(b.x + p[0], b.y + p[1], { steps: 2 });
      await page.mouse.up(); await page.waitForTimeout(120);
      const tr = await page.evaluate(() => ({ steps: window.HindsightDemo.steps(), pos: window.HindsightDemo.pos() }));
      check(D, 'stroke after scrubbing back truncates the future', tr.steps === back + 1 && tr.pos === back + 1, { back, ...tr, before: total });
    }
    // morph
    const mr = await page.evaluate(() => {
      const H = window.HindsightDemo, q = window.__qa;
      H.goto(H.steps());
      const okT = H.setMorphTarget();
      const T = H.snapshot(), tStep = H.pos();
      const s1 = H.stroke(q.pts(0.3, 10, 3, 3)); const s2 = H.stroke(q.pts(0.32, 10, -3, 2));
      const S = H.snapshot(), before = H.steps();
      const centre = q.outline(0.31);
      const mpts = [[centre[0] - 4, centre[1] - 3], [centre[0], centre[1]], [centre[0] + 4, centre[1] + 3]];
      const okM = H.morphStroke(mpts);
      const A = H.snapshot(), after = H.steps();
      const toC = (x, y) => (typeof H.toCanvas === 'function' ? H.toCanvas(x, y) : [x, y]);
      const n = S.length / 2; let nearMoved = 0, nearCloser = 0, nearFarther = 0, farChanged = 0, farCount = 0;
      const dT = (P, i) => Math.hypot(P[2 * i] - T[2 * i], P[2 * i + 1] - T[2 * i + 1]);
      for (let i = 0; i < n; i++) {
        const c = toC(S[2 * i], S[2 * i + 1]);
        const dist = Math.min(...mpts.map(p => Math.hypot(p[0] - c[0], p[1] - c[1])));
        const moved = A[2 * i] !== S[2 * i] || A[2 * i + 1] !== S[2 * i + 1];
        if (dist > 160) { farCount++; if (moved) farChanged++; }
        else if (moved && dT(S, i) > 1e-6) { nearMoved++; if (dT(A, i) < dT(S, i) - 1e-6) nearCloser++; else if (dT(A, i) > dT(S, i) + 1e-6) nearFarther++; }
      }
      return { okT, tStep, s1, s2, okM, before, after, nearMoved, nearCloser, nearFarther, farCount, farChanged, mt: H.morphTarget ? H.morphTarget() : null };
    });
    check(D, 'setMorphTarget + strokes + morphStroke: steps +1 for the morph stroke', mr.after === mr.before + 1, mr);
    check(D, 'morph: points near the brush move toward the target', mr.nearMoved > 0 && mr.nearCloser >= mr.nearMoved * 0.8 && mr.nearFarther === 0, `near moved ${mr.nearMoved}, closer ${mr.nearCloser}, farther ${mr.nearFarther}`);
    check(D, 'morph: points far from the brush unchanged', mr.farCount > 0 && mr.farChanged === 0, `far ${mr.farCount}, changed ${mr.farChanged}`);
    // stats
    const st = await page.evaluate(() => window.HindsightDemo.stats());
    check(D, 'stats(): lastStepPoints < fullPoints (sparse step records)', st && st.lastStepPoints > 0 && st.lastStepPoints < st.fullPoints, st);
    // play
    const pl = await page.evaluate(async () => {
      const H = window.HindsightDemo, n = H.steps();
      H.framesPerStep(1); H.goto(0);
      const started = H.play();
      const t0 = performance.now(); let seenPlaying = H.isPlaying();
      while (performance.now() - t0 < 20000) { await new Promise(r => setTimeout(r, 50)); if (H.isPlaying()) seenPlaying = true; if (!H.isPlaying() && H.pos() === n) break; }
      return { n, after: H.steps(), pos: H.pos(), playing: H.isPlaying(), seenPlaying, ms: Math.round(performance.now() - t0), started };
    });
    check(D, 'play() reaches the last step with steps() unchanged', pl.pos === pl.n && pl.after === pl.n && !pl.playing, pl);
    const shot = await canvasBox(page);
    if (shot) await page.screenshot({ path: path.join(OUT, `demo-${vp}.png`) });
  }

  /* -------------------------------------------------------------- a11y */
  async function a11yChecks(page) {
    const A = 'a11y';
    await page.evaluate(() => { const H = window.HindsightDemo; if (H && H.isPlaying()) H.pause && H.pause(); scrollTo(0, 0); });
    const r = await page.evaluate(() => {
      const name = el => {
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
        const lb = el.getAttribute('aria-labelledby');
        if (lb) return lb.split(/\s+/).map(id => (document.getElementById(id) || {}).textContent || '').join(' ').trim();
        let t = '';
        const walk = n => { for (const c of n.childNodes) { if (c.nodeType === 3) t += c.textContent; else if (c.nodeType === 1) { if (c.getAttribute('aria-hidden') === 'true') continue; if (c.tagName === 'IMG') t += c.getAttribute('alt') || ''; else if (c.tagName === 'svg' || c.tagName === 'SVG') { const ti = c.querySelector('title'); t += ti ? ti.textContent : (c.getAttribute('aria-label') || ''); } else walk(c); } } };
        walk(el);
        return (t.trim() || el.getAttribute('title') || '').trim();
      };
      const hidden = el => !!el.closest('[aria-hidden="true"],[hidden]') || getComputedStyle(el).display === 'none';
      const imgs = [...document.querySelectorAll('img')].filter(i => !hidden(i) && i.getAttribute('role') !== 'presentation').filter(i => !i.hasAttribute('alt')).map(i => i.getAttribute('src'));
      const roleImgs = [...document.querySelectorAll('[role="img"]')].filter(e => !hidden(e) && !name(e)).map(e => e.outerHTML.slice(0, 80));
      const svgs = [...document.querySelectorAll('svg')].filter(s => !hidden(s) && !s.closest('a,button,[role="img"]') && s.getAttribute('role') !== 'img' && s.getAttribute('aria-hidden') !== 'true' && s.getBoundingClientRect().width > 0 && !s.querySelector('title'))
        .map(s => (s.parentElement ? s.parentElement.tagName.toLowerCase() + '.' + s.parentElement.className + ' > ' : '') + 'svg.' + (s.getAttribute('class') || ''));
      const buttons = [...document.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"]')].filter(b => !hidden(b) && !(b.value && b.tagName === 'INPUT') && !name(b)).map(b => b.outerHTML.slice(0, 90));
      const links = [...document.querySelectorAll('a[href]')].filter(a => !hidden(a) && !name(a)).map(a => a.outerHTML.slice(0, 90));
      const inputs = [...document.querySelectorAll('input:not([type="hidden"]), select, textarea')].filter(i => !hidden(i) && !i.getAttribute('aria-label') && !i.getAttribute('aria-labelledby') && !(i.labels && i.labels.length) && !i.title).map(i => i.outerHTML.slice(0, 90));
      return { h1: document.querySelectorAll('h1').length, imgs, roleImgs, svgs, buttons, links, inputs };
    });
    check(A, 'exactly one <h1>', r.h1 === 1, `${r.h1} h1 elements`);
    check(A, 'images have alt (img) / names (role=img)', r.imgs.length === 0 && r.roleImgs.length === 0, short(r.imgs.concat(r.roleImgs), 5));
    check(A, 'buttons have accessible names', r.buttons.length === 0, short(r.buttons, 5));
    check(A, 'links have accessible names', r.links.length === 0, short(r.links, 5));
    check(A, 'form controls have labels', r.inputs.length === 0, short(r.inputs, 5));
    if (r.svgs.length) info(A, `${r.svgs.length} visible standalone <svg> without aria-hidden/role=img/title (decorative ones should be aria-hidden): ${short(r.svgs, 4).join(' | ')}`);
    // focus indicators: baseline unfocused styles of every focusable element, then Tab x30
    await page.evaluate(() => {
      if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
      const sty = e => { const s = getComputedStyle(e); return { o: s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0 ? s.outlineStyle + ' ' + s.outlineWidth + ' ' + s.outlineColor : 'none', b: s.boxShadow }; };
      window.__qaBase = new Map();
      for (const e of document.querySelectorAll('a[href],button,input,select,textarea,summary,[tabindex],[contenteditable]')) window.__qaBase.set(e, sty(e));
      window.__qaSty = sty;
    });
    await page.mouse.click(2, 2).catch(() => {});
    await page.evaluate(() => { scrollTo(0, 0); if (document.activeElement) document.activeElement.blur(); });
    const noRing = [], seen = [];
    for (let i = 0; i < 30; i++) {
      await page.keyboard.press('Tab');
      await page.waitForTimeout(230);
      const f = await page.evaluate(() => {
        const e = document.activeElement;
        if (!e || e === document.body) return null;
        const base = window.__qaBase.get(e) || { o: 'none', b: 'none' };
        const now = window.__qaSty(e);
        // also accept a ring drawn by a wrapper via :focus-within (outline/box-shadow on the parent)
        const p = e.parentElement, pb = p && window.__qaBase.get(p);
        const ps = p ? window.__qaSty(p) : null;
        const parentRing = !!(p && ps && (ps.o !== 'none' || (ps.b !== 'none')) && (!pb || ps.o !== pb.o || ps.b !== pb.b));
        const r = e.getBoundingClientRect();
        const label = (e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className ? '.' + e.className.trim().split(/\s+/)[0] : '') + ' "' + ((e.getAttribute('aria-label') || e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 24)) + '"');
        return { label, ring: (now.o !== 'none' && now.o !== base.o) || (now.b !== 'none' && now.b !== base.b) || (now.o !== 'none' && base.o === now.o && false), parentRing, visible: r.width > 0 && r.height > 0, now, base };
      });
      if (!f) continue;
      seen.push(f.label);
      if (!f.ring && !f.parentRing) noRing.push(f.label);
    }
    check(A, `visible focus indicator on the first ${seen.length} Tab stops`, seen.length >= 10 && noRing.length === 0, noRing.length ? short(noRing, 6) : (seen.length < 10 ? 'only ' + seen.length + ' tab stops reached' : ''));
    info(A, 'tab order: ' + short(seen, 12).join(' | '));
  }
})().catch(e => { console.error('QA crashed', e); process.exit(2); });
