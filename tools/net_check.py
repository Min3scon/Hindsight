#!/usr/bin/env python3
"""Check many websites from this container at once and say WHY each one fails.

    python3 tools/net_check.py [URL ...] [--json FILE] [--timeout 6]

With no URLs it checks the list below. All checks run in parallel (about the time of the slowest one).
Each site gets one verdict:
  ok          the site answered (2xx or 3xx)
  proxy       this cloud environment's proxy refused the connection: the request never reached the site
              (curl: "CONNECT tunnel failed, response 403"; the proxy's status lists it as connect_rejected).
              Adding the host to the environment's allowed domains fixes it.
  site-check  the connection opened and the site itself refused: 403/429/503, usually a Cloudflare or bot
              check ("Just a moment", cf-ray / cf-mitigated headers). Allowing the host doesn't help.
  site-error  the site answered with another error (404, 500...)
  timeout     no answer in time
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import tempfile
import time

SITES = [
    "https://download.blender.org/release/", "https://www.blender.org/", "https://docs.blender.org/",
    "https://projects.blender.org/", "https://extensions.blender.org/", "https://blenderartists.org/",
    "https://www.reddit.com/", "https://old.reddit.com/", "https://en.wikipedia.org/", "https://stackoverflow.com/",
    "https://github.com/", "https://api.github.com/", "https://min3scon.github.io/Hindsight/",
    "https://pypi.org/", "https://crates.io/", "https://80.lv/", "https://superhivemarket.com/",
    "https://www.youtube.com/", "https://productionresultssa1.blob.core.windows.net/",
]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
_CHALLENGE = re.compile(rb"just a moment|attention required|cf-challenge|verify you are human|"
                        rb"blocked by network security|captcha", re.I)


def check(url: str, timeout: float) -> dict:
    t = time.time()
    with tempfile.TemporaryDirectory(prefix="netcheck_") as d:
        hdr, body = os.path.join(d, "h"), os.path.join(d, "b")
        p = subprocess.run(["curl", "-sS", "-L", "--max-redirs", "3", "-A", UA, "-D", hdr, "-o", body,
                            "-w", "%{http_code}", "--connect-timeout", str(min(timeout, 5)), "--max-time",
                            str(timeout), "-r", "0-65535", url], capture_output=True, text=True)
        code = int(p.stdout.strip() or 0) if p.stdout.strip().isdigit() else 0
        err = p.stderr.strip().splitlines()[-1] if p.stderr.strip() else ""
        h = open(hdr, "rb").read().decode("latin-1").lower() if os.path.exists(hdr) else ""
        b = open(body, "rb").read(65536) if os.path.exists(body) else b""
    cloudflare = "server: cloudflare" in h or "cf-ray:" in h
    challenge = "cf-mitigated" in h or bool(_CHALLENGE.search(b))
    if "CONNECT tunnel failed" in err:
        verdict, why = "proxy", "this environment's proxy refused the connection"
    elif code == 0:
        verdict, why = ("timeout", "no answer in time") if "timed out" in err.lower() else ("timeout", err[:120])
    elif 200 <= code < 400:
        verdict = "site-check" if challenge else "ok"
        why = "a challenge page came back" if challenge else "loads"
    elif code in (401, 403, 429, 503) or challenge:
        verdict, why = "site-check", ("Cloudflare" if cloudflare else "the site") + f" answered {code}"
    else:
        verdict, why = "site-error", f"the site answered {code}"
    return {"url": url, "verdict": verdict, "code": code, "why": why, "cloudflare": cloudflare,
            "challenge": challenge, "seconds": round(time.time() - t, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--json")
    ap.add_argument("--timeout", type=float, default=6.0)
    a = ap.parse_args()
    urls = a.urls or SITES
    t = time.time()
    with cf.ThreadPoolExecutor(max_workers=min(24, len(urls))) as ex:
        rows = list(ex.map(lambda u: check(u, a.timeout), urls))
    for r in rows:
        print(f"{r['verdict']:<11} {r['code']:>3}  {r['url']:<55} {r['why']}")
    print(f"{len(rows)} sites in {time.time() - t:.1f} s")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump({"checked": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "rows": rows}, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
