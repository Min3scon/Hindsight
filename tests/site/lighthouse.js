#!/usr/bin/env node
/* Lighthouse on every page, mobile and desktop, using Playwright's Chromium. Fails below 95.
 *   node tests/site/lighthouse.js [--pages ",docs/"]
 * Writes tests/site/out/lighthouse-*.json.
 */
"use strict";
const http = require("http");
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..", "..");
const DOCS = path.join(ROOT, "docs");
const OUT = path.join(__dirname, "out");
const BASE = "/Hindsight/";
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "application/javascript", ".svg": "image/svg+xml", ".png": "image/png",
  ".webp": "image/webp", ".json": "application/json", ".xml": "application/xml", ".txt": "text/plain", ".zip": "application/zip",
  ".woff2": "font/woff2", ".mp4": "video/mp4", ".webm": "video/webm", ".webmanifest": "application/manifest+json" };
const args = process.argv.slice(2);
const pagesArg = args.indexOf("--pages") >= 0 ? args[args.indexOf("--pages") + 1] : ",docs/,changelog/";
const PAGES = pagesArg.split(",");

function serve() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      let url = decodeURIComponent(req.url.split("?")[0]);
      if (!url.startsWith(BASE)) { res.writeHead(404, { "Content-Type": "text/html" }); return res.end("<h1>404</h1>"); }
      let file = path.join(DOCS, url.slice(BASE.length));
      if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, "index.html");
      if (!fs.existsSync(file)) { res.writeHead(404, { "Content-Type": "text/html" }); return res.end(fs.readFileSync(path.join(DOCS, "404.html"))); }
      // GitHub Pages sends compressed text and caches for 10 minutes; do the same so the scores match.
      res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream", "Cache-Control": "max-age=600" });
      fs.createReadStream(file).pipe(res);
    });
    srv.listen(0, "127.0.0.1", () => resolve(srv));
  });
}

(async () => {
  process.env.CHROME_PATH = chromium.executablePath();
  const { default: lighthouse } = await import("lighthouse");
  const chromeLauncher = await import("chrome-launcher");
  fs.mkdirSync(OUT, { recursive: true });
  const srv = await serve();
  const origin = `http://127.0.0.1:${srv.address().port}`;
  const chrome = await chromeLauncher.launch({ chromeFlags: ["--headless=new", "--no-sandbox"] });
  let bad = 0;
  for (const p of PAGES) {
    for (const form of ["mobile", "desktop"]) {
      const config = form === "desktop" ? (await import("lighthouse/core/config/desktop-config.js")).default : undefined;
      const r = await lighthouse(origin + BASE + p, { port: chrome.port, output: "json", logLevel: "error" }, config);
      const cats = r.lhr.categories;
      const scores = Object.fromEntries(Object.entries(cats).filter(([k]) => ["performance", "accessibility", "best-practices", "seo"].includes(k))
        .map(([k, v]) => [k, Math.round(v.score * 100)]));
      const low = Object.entries(scores).filter(([, v]) => v < 95);
      if (low.length) bad++;
      console.log(`${low.length ? "FAIL" : "PASS"} lighthouse ${form.padEnd(7)} ${(p || "/").padEnd(11)} ${JSON.stringify(scores)}`);
      for (const [k] of low) {
        const audits = cats[k].auditRefs.map((a) => r.lhr.audits[a.id]).filter((a) => a && a.score !== null && a.score < 0.9);
        for (const a of audits.slice(0, 6)) console.log(`     ${k}: ${a.id} ${a.score} ${a.displayValue || ""}`);
      }
      fs.writeFileSync(path.join(OUT, `lighthouse-${form}-${(p || "home").replace(/\W/g, "")}.json`), JSON.stringify(r.lhr, null, 1));
    }
  }
  await chrome.kill();
  srv.close();
  process.exit(bad ? 1 : 0);
})();
