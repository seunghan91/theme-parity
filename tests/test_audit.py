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

# planted defect 1 — a *semantic* token with no .dark counterpart
check("missing-dark caught for semantic --surface-muted",
      "missing-dark" in kinds_for(data, "--surface-muted"))

# a raw scale step with no dark pair is CORRECT, not a defect: Radix/MD3/Tailwind
# all keep the palette mode-agnostic and switch modes at the semantic layer.
check("raw scale --brand-500 NOT flagged missing-dark",
      "missing-dark" not in kinds_for(data, "--brand-500"))

# but referencing that scale straight from a view removes the place where a
# different step could be pointed to in dark mode
check("raw-scale-ref reported when views use the palette directly",
      any(f["kind"] == "raw-scale-ref" for f in data.get("findings", [])))

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

print("case: codex-reported gaps")
data, code = run(os.path.join(FIX, "css"), "--refs", os.path.join(FIX, "views"))
check("commented-out definition does NOT count as defined",
      "undefined-ref" in kinds_for(data, "--ghost-token"))
check("var() reference inside a <style>/css block is checked",
      "undefined-ref" in kinds_for(data, "--only-in-css-missing"))
lits = {f["token"] for f in data.get("findings", []) if f["kind"] == "hardcoded-color"}
for lit in ("#fff",):
    check(f"short hex {lit} detected as hardcoded", lit in lits)
check("hsl() detected as hardcoded", any(t.startswith("hsl(") for t in lits))
check("oklch() detected as hardcoded", any(t.startswith("oklch(") for t in lits))

check("a URL glob (/path/*) does not swallow later declarations",
      "undefined-ref" not in kinds_for(data, "--url-guard-token"))

print("case: hex alpha byte order differs by platform")
sys.path.insert(0, os.path.join(HERE, ".."))
import theme_parity as _tp
_red = (1.0, 0.0, 0.0)
def _approx(v, x): return abs(v - x) < 0.01
css8 = _tp._hex_rgb("#FF000080")
check("CSS #RRGGBBAA reads as red at ~50% alpha",
      css8 and css8[0] == _red and _approx(css8[1], 0.5))
css4 = _tp._hex_rgb("#F008")
check("CSS #RGBA shorthand is parsed, not dropped",
      css4 is not None and css4[0] == _red)
and8 = _tp._hex_rgb("#80FF0000", order="argb")
check("Android #AARRGGBB reads as red at ~50% alpha",
      and8 and and8[0] == _red and _approx(and8[1], 0.5))
sw8 = _tp._hex_rgb("0x80FF0000")
check("Swift 0xAARRGGBB reads as red at ~50% alpha",
      sw8 and sw8[0] == _red and _approx(sw8[1], 0.5))
check("garbage hex returns None", _tp._hex_rgb("#zzz") is None)

print()

def test_all():
    """pytest 진입점.

    이 파일은 원래 순차 스크립트였다. pytest 는 `test_` 로 시작하는 함수만
    수집하므로, CI 가 pytest 를 돌리면 **0개 실행 후 초록**이 나왔다 —
    0건을 무결로 읽는, 이 도구가 막으려는 바로 그 함정을 테스트가 저지른 셈이다.
    모듈 임포트 시점에 위 검사들이 이미 실행되므로 여기서는 결과만 단언한다.
    """
    assert not failures, "실패: " + "; ".join(failures)


if failures:
    print(f"FAILED {len(failures)}:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all passed")
