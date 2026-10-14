#!/usr/bin/env node
/* Serve docs/ under /Hindsight/ the way GitHub Pages does (including 404.html). Local use only.
 *   node tools/serve_site.js [port]        default port 8765
 */
"use strict";
const http = require("http");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "docs");
const BASE = "/Hindsight/";
const PORT = +(process.argv[2] || process.env.PORT || 8765);
const TYPES = {
  ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8",
  ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".avif": "image/avif",
  ".json": "application/json", ".xml": "application/xml", ".txt": "text/plain; charset=utf-8", ".zip": "application/zip",
  ".woff2": "font/woff2", ".mp4": "video/mp4", ".webm": "video/webm", ".webmanifest": "application/manifest+json",
};

function send(res, code, file) {
  const type = TYPES[path.extname(file).toLowerCase()] || "application/octet-stream";
  const stat = fs.statSync(file);
  res.writeHead(code, { "Content-Type": type, "Content-Length": stat.size, "Cache-Control": "no-cache" });
  fs.createReadStream(file).pipe(res);
}

http.createServer((req, res) => {
  let url = decodeURIComponent(req.url.split("?")[0]);
  if (url === "/" || url === "/Hindsight") { res.writeHead(301, { Location: BASE }); return res.end(); }
  if (!url.startsWith(BASE)) return send(res, 404, path.join(ROOT, "404.html"));
  let rel = url.slice(BASE.length);
  let file = path.join(ROOT, rel);
  if (!file.startsWith(ROOT)) { res.writeHead(400); return res.end(); }
  if (fs.existsSync(file) && fs.statSync(file).isDirectory()) {
    if (!url.endsWith("/")) { res.writeHead(301, { Location: url + "/" }); return res.end(); }
    file = path.join(file, "index.html");
  }
  if (!fs.existsSync(file)) return send(res, 404, path.join(ROOT, "404.html"));
  send(res, 200, file);
}).listen(PORT, "127.0.0.1", () => console.log(`serving ${ROOT} at http://127.0.0.1:${PORT}${BASE}`));
