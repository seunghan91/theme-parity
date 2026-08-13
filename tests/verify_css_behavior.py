#!/usr/bin/env python3
"""Reproduces the CSS behavior this tool is built on. Requires playwright.

Claim: `var(--undefined)` with no fallback makes the whole declaration invalid
at computed-value time, so a background falls back to transparent — and the
earlier value in the same block does not survive either.

    python3 tests/verify_css_behavior.py
"""
import pathlib
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("playwright not installed — pip install playwright && playwright install chromium")

HTML = """<div id=a style="background-color: red; background-color: var(--nope);">a</div>
<div id=b style="background-color: red; background-color: var(--nope, blue);">b</div>"""

tmp = pathlib.Path(__file__).with_name("_verify.html")
tmp.write_text(HTML)
try:
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page()
        pg.goto(tmp.as_uri())
        a = pg.eval_on_selector("#a", "e => getComputedStyle(e).backgroundColor")
        b = pg.eval_on_selector("#b", "e => getComputedStyle(e).backgroundColor")
        br.close()
finally:
    tmp.unlink(missing_ok=True)

print(f"no fallback   → {a}")
print(f"with fallback → {b}")
assert a == "rgba(0, 0, 0, 0)", f"expected transparent, got {a}"
assert b == "rgb(0, 0, 255)", f"expected blue, got {b}"
print("verified: an undefined var with no fallback drops the declaration entirely")
