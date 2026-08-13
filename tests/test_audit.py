#!/usr/bin/env python3
"""Negative tests: the checker must FAIL on known-broken fixtures.

A checker that has only been observed passing proves nothing. Each case below
plants a specific defect and asserts it is caught — and, just as importantly,
asserts that the legitimate cases next to it are NOT flagged. False positives
are what get a checker ignored, so they are tested as failures too.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "theme_parity.py")
FIX = os.path.join(HERE, "fixtures")

failures = []


def run(*args):
    p = subprocess.run([sys.executable, TOOL, *args, "--json"],
                       capture_output=True, text=True)
    try:
        return json.loads(p.stdout), p.returncode
    except json.JSONDecodeError:
        return {"findings": [], "_stderr": p.stderr, "_stdout": p.stdout}, p.returncode


def kinds_for(data, token):
    return {f["kind"] for f in data.get("findings", []) if f.get("token") == token}


def check(label, cond):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        failures.append(label)


print("case: css tokens + view refs")
data, code = run(os.path.join(FIX, "css"), "--refs", os.path.join(FIX, "views"))

# planted defect 1 — --brand-500 has no .dark counterpart
check("missing-dark caught for --brand-500",
      "missing-dark" in kinds_for(data, "--brand-500"))

# planted defect 2 — --brand-900 is referenced but never defined, no fallback
check("undefined-ref caught for --brand-900",
      "undefined-ref" in kinds_for(data, "--brand-900"))

# must NOT flag: reference carries a fallback, so it never silently dies
check("var() with fallback NOT flagged",
      "undefined-ref" not in kinds_for(data, "--nope"))

# must NOT flag: non-color token is defined; color parser just can't read it
check("non-color token --radius-md NOT flagged as undefined",
      "undefined-ref" not in kinds_for(data, "--radius-md"))

# must NOT flag: properly paired tokens
check("paired --surface-card NOT flagged missing-dark",
      "missing-dark" not in kinds_for(data, "--surface-card"))

check("hardcoded color reported", any(
    f["kind"] == "hardcoded-color" for f in data.get("findings", [])))
check("exit code is 1 when errors exist", code == 1)

print("case: android values vs values-night")
data, code = run(os.path.join(FIX, "android"), "--platform", "android")
check("missing-dark caught for text_main (no values-night entry)",
      "missing-dark" in kinds_for(data, "text_main"))
check("paired bg_card NOT flagged",
      "missing-dark" not in kinds_for(data, "bg_card"))

print("case: empty/unreadable root must NOT report success")
data, code = run(os.path.join(FIX, "css"), "--platform", "android")
check("reading 0 tokens exits non-zero instead of green", code != 0)

print("case: runtime-injected custom properties must NOT be flagged")
data, code = run(os.path.join(FIX, "css"), "--refs", os.path.join(FIX, "views"))
for tok in ("--stamp-color", "--stamp-rotation", "--t-bg", "--t-accent"):
    check(f"runtime-injected {tok} NOT flagged undefined",
          "undefined-ref" not in kinds_for(data, tok))

print()
if failures:
    print(f"FAILED {len(failures)}:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all passed")
