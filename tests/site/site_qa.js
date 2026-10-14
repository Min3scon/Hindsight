#!/usr/bin/env node
/*
 * Site QA for docs/ (served like GitHub Pages under /Hindsight/). Prints PASS/FAIL lines, exits 1 on any FAIL.
 *
 *   node tests/site/site_qa.js [--browsers chromium,firefox,webkit] [--shots] [--quick]
 *
 * Checks: every page loads with no console errors in each browser; head tags (title, description,
 * canonical, Open Graph, Twitter, icons, manifest); every internal link and asset resolves; no external
 * requests; landing page weight before media; the demo (strokes, exact rewind and replay, keyboard undo,
 * morph target and Morph brush, Movie Mode, Reset, hundreds of steps), frame time while dragging with 4x
 * CPU throttling (Chromium), and touch on a phone viewport. --shots saves screenshots of every page at
 * 360, 390, 768, 1024, 1440 and 1920 px and a landscape phone into tests/site/out/.
 */
"use strict";
const http = require("http");
const fs = require("fs");
const path = require("path");
const pw = require("playwright");

const ROOT = path.resolve(__dirname, "..", "..");
const DOCS = path.join(ROOT, "docs");
const OUT = path.join(__dirname, "out");
const BASE = "/Hindsight/";
const PROD = "https://min3scon.github.io/Hindsight/";
const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const BROWSERS = opt("--browsers", "chromium,firefox,webkit").split(",");
const SHOTS = args.includes("--shots");
const QUICK = args.includes("--quick");
const PAGES = ["", "docs/", "changelog/", "404.html"];
const WIDTHS = [360, 390, 768, 1024, 1440, 1920];

let fails = 0, passes = 0;
const failList = [];
function check(area, name, ok, detail) {
  const line = `${ok ? "PASS" : "FAIL"} [${area}] ${name}${detail !== undefined && detail !== "" ? " -- " + (typeof detail === "string" ? detail : JSON.stringify(detail)) : ""}`;
  console.log(line);
  if (ok) passes++; else { fails++; failList.push(line); }
  return ok;
}

// ------------------------------------------------------------------------ server
const TYPES = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "application/javascript", ".svg": "image/svg+xml",
  ".png": "image/png", ".webp": "image/webp", ".json": "application/json", ".xml": "application/xml", ".txt": "text/plain",
  ".zip": "application/zip", ".woff2": "font/woff2", ".mp4": "video/mp4", ".webm": "video/webm", ".webmanifest": "application/manifest+json" };
function serve() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      let url = decodeURIComponent(req.url.split("?")[0].split("#")[0]);
      if (!url.startsWith(BASE)) url = "/404";
      let file = path.join(DOCS, url.slice(BASE.length));
      if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, "index.html");
      if (!fs.existsSync(file)) { res.writeHead(404, { "Content-Type": "text/html" }); return res.end(fs.readFileSync(path.join(DOCS, "404.html"))); }
      res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" });
      fs.createReadStream(file).pipe(res);
    });
    srv.listen(0, "127.0.0.1", () => resolve(srv));
  });
}

// ------------------------------------------------------------------------ static checks (no browser)
function staticChecks() {
  for (const p of PAGES) {
    const file = path.join(DOCS, p.endsWith(".html") ? p : p + "index.html");
    const html = fs.readFileSync(file, "utf8");
    const meta = (sel) => { const m = html.match(new RegExp(`<meta ${sel}="([^"]*)"`)); return m ? m[1] : null; };
    const title = (html.match(/<title>([^<]*)<\/title>/) || [])[1] || "";
    check("head", `${p || "/"} title under 60 chars`, title.length > 0 && title.length < 60, title);
    const desc = meta('name="description" content');
    check("head", `${p || "/"} description under 160 chars`, desc && desc.length <= 160, desc);
    if (p !== "404.html") {
      const canon = (html.match(/<link rel="canonical" href="([^"]+)"/) || [])[1];
      check("head", `${p || "/"} canonical`, canon === PROD + p, canon);
    }
    for (const prop of ["og:type", "og:title", "og:description", "og:url", "og:site_name", "og:locale", "og:image",
      "og:image:width", "og:image:height", "og:image:type", "og:image:alt"]) {
      check("head", `${p || "/"} ${prop}`, !!meta(`property="${prop}" content`));
    }
    check("head", `${p || "/"} og:image absolute https`, (meta('property="og:image" content') || "").startsWith("https://"));
    for (const name of ["twitter:card", "twitter:title", "twitter:description", "twitter:image", "twitter:image:alt", "theme-color", "color-scheme"]) {
      check("head", `${p || "/"} ${name}`, !!meta(`name="${name}" content`));
    }
    for (const rel of ['rel="icon" href="[^"]*favicon.svg"', 'sizes="32x32"', 'rel="apple-touch-icon"', 'rel="manifest"']) {
      check("head", `${p || "/"} ${rel.split('"')[1] || rel}`, new RegExp(rel).test(html));
    }
    check("perf", `${p || "/"} no render-blocking stylesheet`, !/<link[^>]+rel="stylesheet"/.test(html));
    check("perf", `${p || "/"} scripts deferred`, !/<script(?![^>]*\bdefer\b)[^>]*\bsrc=/.test(html));
    check("privacy", `${p || "/"} no external hosts`, !/(src|href)="https?:\/\/(?!min3scon\.github\.io|github\.com\/Min3scon)/.test(html),
      (html.match(/(src|href)="https?:\/\/[^"]+/g) || []).filter((u) => !/min3scon|Min3scon/.test(u)));
  }
  for (const f of ["sitemap.xml", "robots.txt", "site.webmanifest", "favicon.svg", "assets/img/og.png", "assets/img/og-square.png",
    "assets/img/favicon-32.png", "assets/img/icon-192.png", "assets/img/apple-touch-icon.png", "download/hindsight.zip"]) {
    check("files", f, fs.existsSync(path.join(DOCS, f)));
  }
  const og = path.join(DOCS, "assets/img/og.png");
  if (fs.existsSync(og)) {
    const b = fs.readFileSync(og);
    check("og", "og.png is 1200x630", b.readUInt32BE(16) === 1200 && b.readUInt32BE(20) === 630, [b.readUInt32BE(16), b.readUInt32BE(20)]);
    check("og", "og.png under 300 KB", b.length < 300 * 1024, Math.round(b.length / 1024) + " KB");
  }
}

// ------------------------------------------------------------------------ browser checks
async function pageChecks(name, browser, origin) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const external = [];
  for (const p of PAGES) {
    const page = await ctx.newPage();
    const errors = [];
    page.on("console", (m) => { if (m.type() === "error" && !(p === "404.html" && /404/.test(m.text()))) errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("request", (r) => { if (!r.url().startsWith(origin) && !r.url().startsWith("data:")) external.push(r.url()); });
    const bad = [];
    page.on("response", (r) => { if (r.status() >= 400 && !r.url().endsWith("404.html") && !r.url().includes("does-not-exist")) bad.push(r.status() + " " + r.url()); });
    const url = origin + BASE + (p === "404.html" ? "does-not-exist/" : p);
    await page.goto(url, { waitUntil: "load" });
    await page.evaluate(async () => { for (let y = 0; y < document.body.scrollHeight; y += 600) { window.scrollTo(0, y); await new Promise((r) => setTimeout(r, 60)); } });
    await page.waitForTimeout(400);
    check(name, `${p || "/"} no console errors`, errors.length === 0, errors.slice(0, 5));
    if (p !== "404.html") check(name, `${p || "/"} no failed requests`, bad.length === 0, bad.slice(0, 5));
    const hOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(name, `${p || "/"} no horizontal scroll at 1440`, hOverflow <= 0, hOverflow);
    if (name === "chromium") {
      // internal links resolve
      const links = await page.evaluate(() => [...document.querySelectorAll("a[href]")].map((a) => a.href));
      for (const href of [...new Set(links)]) {
        if (!href.startsWith(origin) || href.includes("does-not-exist")) continue;
        const u = href.split("#")[0];
        const r = await ctx.request.get(u);
        check("links", `${p || "/"} -> ${u.replace(origin, "")}`, r.status() === 200, r.status());
        const hash = href.split("#")[1];
        if (hash && u === url.split("#")[0]) {
          check("links", `${p || "/"} #${hash} exists`, await page.evaluate((h) => !!document.getElementById(h), hash));
        }
      }
      // alt text and labels
      const noAlt = await page.evaluate(() => [...document.images].filter((i) => !i.hasAttribute("alt")).map((i) => i.src));
      check("a11y", `${p || "/"} every image has alt`, noAlt.length === 0, noAlt);
      const unlabeled = await page.evaluate(() => [...document.querySelectorAll("button")].filter((b) => !(b.textContent.trim() || b.getAttribute("aria-label"))).length);
      check("a11y", `${p || "/"} every button has a label`, unlabeled === 0, unlabeled);
      const landmarks = await page.evaluate(() => ({ main: !!document.querySelector("main"), nav: !!document.querySelector("nav,header"), h1: document.querySelectorAll("h1").length }));
      check("a11y", `${p || "/"} landmarks and one h1`, landmarks.main && landmarks.nav && landmarks.h1 === 1, landmarks);
    }
    await page.close();
  }
  check(name, "no requests to other hosts", external.length === 0, [...new Set(external)].slice(0, 5));
  await ctx.close();
}

async function weight(browser, origin) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  let bytes = 0, media = 0;
  const seen = [];
  page.on("response", async (r) => {
    try {
      const b = (await r.body()).length;
      const isMedia = /\.(webp|png|jpg|webm|mp4|avif)(\?|$)/.test(r.url());
      if (isMedia) media += b; else { bytes += b; seen.push([r.url().replace(origin, ""), b]); }
    } catch (e) { /* redirects */ }
  });
  await page.goto(origin + BASE, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  check("perf", "landing page under 300 KB before media (at load)", bytes < 300 * 1024,
    Math.round(bytes / 1024) + " KB: " + seen.map(([u, b]) => `${u} ${Math.round(b / 1024)}K`).join(", "));
  console.log(`INFO [perf] media at load: ${Math.round(media / 1024)} KB`);
  await ctx.close();
}

// ------------------------------------------------------------------------ demo
async function demoChecks(name, browser, origin, opts = {}) {
  const ctx = await browser.newContext(opts.mobile ? { ...pw.devices["Pixel 7"] } : { viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(origin + BASE + "#demo-section", { waitUntil: "load" });
  await page.waitForFunction(() => window.__hindsightDemo, null, { timeout: 15000 });
  await page.locator("#demo").scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const tag = name + (opts.mobile ? " touch" : "");
  const r = await page.evaluate(() => window.__hindsightDemo.stageRect());
  const st = () => page.evaluate(() => window.__hindsightDemo.state());
  const hash = () => page.evaluate(() => window.__hindsightDemo.hash());

  async function stroke(k) {
    const r = await page.evaluate(() => window.__hindsightDemo.stageRect());   // clicks may have scrolled the page
    const x0 = r.x + r.w * (0.42 + 0.03 * (k % 5)), y0 = r.y + r.h * (0.45 + 0.03 * ((k * 7) % 5));
    if (opts.mobile) {
      const cdp = name === "chromium" ? await ctx.newCDPSession(page) : null;
      if (cdp) {
        await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: x0, y: y0 }] });
        for (let i = 1; i <= 10; i++) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: x0 + i * 7, y: y0 + i }] });
        await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
        await cdp.detach();
        return;
      }
    }
    await page.mouse.move(x0, y0);
    await page.mouse.down();
    for (let i = 1; i <= 10; i++) await page.mouse.move(x0 + i * 7, y0 + i);
    await page.mouse.up();
  }

  const hashes = [await hash()];
  for (let k = 0; k < 6; k++) { await stroke(k); hashes.push(await hash()); }
  let s = await st();
  check(tag, "demo: 6 strokes give 6 steps", s.steps === 6 && s.cursor === 6, s);
  const distinct = new Set(hashes).size;
  check(tag, "demo: each stroke changes the shape", distinct === 7, distinct);
  let exact = true;
  for (const p of [0, 6, 2, 5, 1, 3, 0, 6]) {
    await page.evaluate((x) => window.__hindsightDemo.goto(x), p);
    if ((await hash()) !== hashes[p]) exact = false;
  }
  check(tag, "demo: every jump is bit-exact", exact);
  if (!opts.mobile) {
    // drag the slider like a user
    const tr = await page.locator("#demo .track").boundingBox();
    await page.mouse.move(tr.x + tr.width - 1, tr.y + tr.height / 2);
    await page.mouse.down();
    await page.mouse.move(tr.x + tr.width * 0.5, tr.y + tr.height / 2, { steps: 6 });
    await page.mouse.up();
    s = await st();
    check(tag, "demo: dragging the slider moves through the history", s.cursor === 3 && (await hash()) === hashes[3], s);
    // keyboard undo / redo
    await page.locator("#demo .track").focus();
    await page.keyboard.press("Control+z");
    check(tag, "demo: Ctrl+Z steps back", (await st()).cursor === 2 && (await hash()) === hashes[2]);
    await page.keyboard.press("Control+Shift+z");
    check(tag, "demo: Ctrl+Shift+Z steps forward", (await st()).cursor === 3);
    await page.keyboard.press("End");
    check(tag, "demo: End goes to the last step", (await st()).cursor === 6 && (await hash()) === hashes[6]);
    // morph target and Morph brush
    await page.evaluate(() => window.__hindsightDemo.goto(2));
    await page.click("#demo [data-act=menu]");
    await page.click("#demo .menu [data-m=target]");
    check(tag, "demo: morph target set", (await st()).target === 2);
    await page.evaluate(() => window.__hindsightDemo.goto(6));
    await page.click("#demo [data-tool=morph]");
    const before = await hash();
    check(tag, "demo: Morph tool selected", (await st()).tool === "morph");
    await stroke(4);
    s = await st();
    check(tag, "demo: Morph brush paints and records a step", s.steps === 7 && (await hash()) !== before, s);
    // Movie Mode
    await page.click("#demo [data-tool=pull]");
    await page.evaluate(() => window.__hindsightDemo.goto(0));
    await page.click("#demo [data-act=play]");
    await page.waitForTimeout(150);
    const mid = await st();
    await page.waitForFunction(() => !window.__hindsightDemo.state().movie, null, { timeout: 8000 });
    s = await st();
    check(tag, "demo: Movie Mode plays to the end", mid.movie && s.cursor === s.steps, { mid, end: s });
    // hundreds of steps stay quick
    if (!QUICK) {
      const t0 = Date.now();
      for (let k = 0; k < 150; k++) await stroke(k);
      const t1 = Date.now();
      await page.evaluate(() => window.__hindsightDemo.goto(0));
      await page.evaluate(() => window.__hindsightDemo.goto(window.__hindsightDemo.state().steps));
      const jump = Date.now() - t1;
      s = await st();
      check(tag, "demo: 150 more strokes stay responsive", s.steps >= 150 && jump < 1500,
        { steps: s.steps, msPerStroke: Math.round((t1 - t0) / 150), fullJumpMs: jump });
    }
    // reset
    await page.click("#demo [data-act=reset]");
    s = await st();
    check(tag, "demo: Reset clears the history and the shape", s.steps === 0 && (await hash()) === hashes[0], s);
  }
  if (name === "chromium" && !opts.mobile) {
    // A mid-range machine: 4x CPU throttling. 60 fps needs the work for one pointer event plus one
    // frame to fit in 16.7 ms; the test checks the 90th percentile stays under 18 ms.
    const cdp = await ctx.newCDPSession(page);
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
    await page.evaluate(() => { const w = window.__hindsightDemo.work(); w.input.length = 0; w.render.length = 0; });
    const x0 = r.x + r.w * 0.4, y0 = r.y + r.h * 0.5;
    const before = await page.evaluate(() => window.__hindsightDemo.work());
    await page.mouse.move(x0, y0);
    await page.mouse.down();
    for (let i = 0; i < 60; i++) await page.mouse.move(x0 + i * 4, y0 + Math.sin(i / 6) * 24);
    await page.mouse.up();
    await page.waitForTimeout(100);
    const w = await page.evaluate(() => window.__hindsightDemo.work());
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 });
    const inp = w.input.slice(-60).sort((a, b) => a - b), ren = w.render.slice(-60).sort((a, b) => a - b);
    const pct = (a, q) => a[Math.min(a.length - 1, Math.floor(a.length * q))] || 0;
    const total90 = pct(inp, 0.9) + pct(ren, 0.9);
    check("perf", "demo: drag work per frame fits 60 fps with 4x CPU throttling (p90 input + render under 18 ms)",
      total90 < 18, { inputP50: +pct(inp, 0.5).toFixed(2), inputP90: +pct(inp, 0.9).toFixed(2),
        renderP50: +pct(ren, 0.5).toFixed(2), renderP90: +pct(ren, 0.9).toFixed(2), samples: inp.length });
  }
  check(tag, "demo: no console errors", errors.length === 0, errors.slice(0, 5));
  await ctx.close();
}

// ------------------------------------------------------------------------ screenshots
async function screenshots(name, browser, origin) {
  fs.mkdirSync(OUT, { recursive: true });
  const sizes = WIDTHS.map((w) => [w, w <= 400 ? 800 : 900]).concat([[844, 390]]);
  for (const [w, h] of sizes) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
    for (const p of PAGES) {
      const page = await ctx.newPage();
      await page.goto(origin + BASE + (p === "404.html" ? "nope/" : p), { waitUntil: "load" });
      await page.evaluate(async () => { for (let y = 0; y < document.body.scrollHeight; y += 500) { window.scrollTo(0, y); await new Promise((r) => setTimeout(r, 40)); } window.scrollTo(0, 0); });
      await page.waitForTimeout(500);
      const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      check(name, `${p || "/"} at ${w}x${h}: no horizontal scroll`, over <= 0, over);
      const file = path.join(OUT, `${name}-${(p || "home").replace(/[\/.]/g, "_")}-${w}x${h}.png`);
      await page.screenshot({ path: file, fullPage: true });
      await page.close();
    }
    await ctx.close();
  }
}

(async () => {
  staticChecks();
  const srv = await serve();
  const origin = `http://127.0.0.1:${srv.address().port}`;
  for (const name of BROWSERS) {
    const browser = await pw[name].launch();
    await pageChecks(name, browser, origin);
    if (name === "chromium") await weight(browser, origin);
    await demoChecks(name, browser, origin);
    if (name === "chromium" || name === "webkit") await demoChecks(name, browser, origin, { mobile: true });
    if (SHOTS) await screenshots(name, browser, origin);
    await browser.close();
  }
  srv.close();
  console.log(`\n${passes} passed, ${fails} failed`);
  if (fails) { console.log(failList.join("\n")); process.exit(1); }
})();
