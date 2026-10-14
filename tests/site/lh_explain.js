#!/usr/bin/env node
/* Print the parts of a saved Lighthouse report that explain a score below 100.
 *   node tests/site/lh_explain.js tests/site/out/lighthouse-mobile-home.json */
"use strict";
const l = require(require("path").resolve(process.argv[2]));
const a = l.audits;
const show = (id) => a[id] && console.log(id.padEnd(28), a[id].displayValue || "", a[id].score);
["first-contentful-paint", "largest-contentful-paint", "total-blocking-time", "speed-index", "cumulative-layout-shift"].forEach(show);
for (const [cat, c] of Object.entries(l.categories)) {
  if (!["performance", "accessibility", "best-practices", "seo"].includes(cat) || c.score === 1) continue;
  for (const ref of c.auditRefs) {
    const au = a[ref.id];
    if (!au || au.score === null || au.score >= 1 || ref.weight === 0) continue;
    console.log(`${cat}: ${ref.id} score=${au.score} ${au.displayValue || ""}`);
    if (au.details && au.details.items) {
      for (const it of au.details.items.slice(0, 5)) {
        console.log("    ", JSON.stringify(it.node ? { snippet: it.node.snippet, label: it.node.nodeLabel, explain: it.node.explanation } : it).slice(0, 400));
      }
    }
  }
}
