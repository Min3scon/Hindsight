#!/usr/bin/env node
/* Render the site's generated images with Playwright (Chromium):
 *   social cards  site/assets/img/og.png (1200x630), og-square.png (1200x1200)
 *                 .github/social-preview.png (1280x640, upload it by hand in the repo settings)
 *   icons         site/assets/img/favicon-32.png, icon-192.png, icon-512.png, apple-touch-icon.png
 * Run: node site/render_assets.js
 */
"use strict";
const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..");
const IMG = path.join(ROOT, "site", "assets", "img");
const CARD = "file:///" + path.join(ROOT, "site", "og", "card.html").replace(/\\/g, "/");

const CARDS = [
  { out: path.join(IMG, "og.png"), w: 1200, h: 630, vars: { safe: "64px 72px", title: "86px", tw: "11.5ch", sub: "30px" } },
  { out: path.join(IMG, "og-square.png"), w: 1200, h: 1200, vars: { safe: "96px", title: "116px", tw: "8.5ch", sub: "36px" } },
  { out: path.join(ROOT, ".github", "social-preview.png"), w: 1280, h: 640, vars: { safe: "72px 88px", title: "88px", tw: "12ch", sub: "30px" } },
];
const ICONS = [["favicon-32.png", 32], ["icon-192.png", 192], ["icon-512.png", 512], ["apple-touch-icon.png", 180]];

(async () => {
  const browser = await chromium.launch();
  for (const c of CARDS) {
    const page = await browser.newPage({ viewport: { width: c.w, height: c.h }, deviceScaleFactor: 1 });
    await page.goto(CARD);
    await page.evaluate(([w, h, v]) => {
      const s = document.documentElement.style;
      s.setProperty("--w", w + "px");
      s.setProperty("--h", h + "px");
      for (const [k, val] of Object.entries(v)) s.setProperty("--" + k, val);
    }, [c.w, c.h, c.vars]);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: c.out, type: "png" });
    console.log(path.relative(ROOT, c.out), Math.round(fs.statSync(c.out).size / 1024) + " KB");
    await page.close();
  }
  const svg = fs.readFileSync(path.join(IMG, "favicon.svg"), "utf8");
  for (const [name, size] of ICONS) {
    const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
    // The apple touch icon gets a solid background (iOS adds its own rounding).
    const bg = name.startsWith("apple") ? "#1d1d22" : "transparent";
    await page.setContent(`<html><body style="margin:0;background:${bg}">` +
      svg.replace("<svg ", `<svg width="${size}" height="${size}" `) + "</body></html>");
    await page.screenshot({ path: path.join(IMG, name), type: "png", omitBackground: bg === "transparent" });
    await page.close();
  }
  await browser.close();
  console.log("icons done");
})();
