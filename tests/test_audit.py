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

print("case: Tailwind v4 — @theme source, at-rule before it")
data, code = run(os.path.join(FIX, "css"), "--refs", os.path.join(FIX, "views"))

# `@custom-variant dark (&:where(.dark, .dark *));` ends in a semicolon, so it
# is not a selector. Absorbing it as one puts `.dark` in the selector of the
# NEXT block and registers that whole block as dark — every token in it then
# reads as "defined in both modes, same value".
for tok in ("--color-tw-50", "--color-tw-500"):
    check(f"primitive {tok} in @theme NOT flagged identical-modes",
          "identical-modes" not in kinds_for(data, tok))
check("paired --color-tw-surface NOT flagged identical-modes",
      "identical-modes" not in kinds_for(data, "--color-tw-surface"))

# Control: @theme must still be READ, not skipped. If it were skipped these
# tokens would have no light entry at all, and the genuine gap below would be
# invisible.
check("@theme is read as the light source (no missing-light)",
      "missing-light" not in kinds_for(data, "--color-tw-surface"))
check("semantic --color-tw-muted with no dark pair IS still caught",
      "missing-dark" in kinds_for(data, "--color-tw-muted"))

# `700` and `9999` are valid hex digit strings. Treating a bare number as a
# color invents #770000 / #99999999 and then reports contrast for a font weight.
for tok in ("--tw-weight-bold", "--tw-z-top"):
    check(f"non-color value {tok} NOT read as a color",
          not kinds_for(data, tok))

print("case: tokens declared in .ts/.js are declared")
check("--ts-card-bg declared in a .ts palette NOT flagged undefined",
      "undefined-ref" not in kinds_for(data, "--ts-card-bg"))
check("--ts-card-text declared in a .ts palette NOT flagged undefined",
      "undefined-ref" not in kinds_for(data, "--ts-card-text"))
check("control: --ts-never-declared IS still flagged undefined",
      "undefined-ref" in kinds_for(data, "--ts-never-declared"))

print("case: dark handled by a utility at the call site, not on the token")
for tok in ("--util-light-bg", "--util-light-fg"):
    check(f"{tok} reclassified, not reported as missing-dark",
          kinds_for(data, tok) == {"dark-handled-in-views"})
# Reclassified, but NOT downgraded: proximity cannot prove every consumer
# handles dark, so letting this pass a CI gate would hide real gaps.
sev = {f["severity"] for f in data.get("findings", [])
       if f["kind"] == "dark-handled-in-views"}
check("dark-handled-in-views keeps error severity (classification, not amnesty)",
      sev == {"error"})
check("control: --util-orphan-bg with no dark handling IS still missing-dark",
      "missing-dark" in kinds_for(data, "--util-orphan-bg"))
check("control: --surface-muted (never consumed in views) stays missing-dark",
      "missing-dark" in kinds_for(data, "--surface-muted"))

print("case: dark value declared as an alias/function")
check("--alias-glass NOT reported as missing-dark",
      "missing-dark" not in kinds_for(data, "--alias-glass"))
check("--alias-glass reported as an unparsed dark value instead",
      "dark-unparsed" in kinds_for(data, "--alias-glass"))

print("case: adversarial — narrowing must not hide real defects")
# A quoted `;` is not a statement separator. Cutting there drops `:root` from
# the selector and the token vanishes entirely — silently, as a pass.
check("token under a selector with a quoted ';' is still read",
      "identical-modes" in kinds_for(data, "--semi-guard"))
# Recording "dark is declared" must not swallow the opposite asymmetry.
check("dark-only alias still reports missing-light",
      "missing-light" in kinds_for(data, "--dark-only-alias"))
# Later declaration wins. A stale literal would be checked for contrast and
# equality against a value that never reaches the screen.
check("last dark declaration wins when it is an unresolvable alias",
      kinds_for(data, "--cascade-last") == {"dark-unparsed"})
# A property name inside a string literal is not a declaration.
check("custom property named inside content:\"\" is not a dark declaration",
      "missing-dark" in kinds_for(data, "--string-guard"))

check("light declared as an alias is not reported as missing-light",
      "missing-light" not in kinds_for(data, "--alias-light-only"))
check("it is reported as light-unparsed instead",
      "light-unparsed" in kinds_for(data, "--alias-light-only"))
# A translucent material must not be used as a contrast baseline.
against = {f.get("against") for f in data.get("findings", [])
           if f["kind"] == "low-contrast"}
check("translucent --glass-panel is not treated as a background",
      "--glass-panel" not in against)

check("interpolated var(--name{expr}) is not a token reference",
      "undefined-ref" not in kinds_for(data, "--dyn"))
check("interpolated var(--name${expr}) is not a token reference either",
      "undefined-ref" not in kinds_for(data, "--dyn2"))

print("case: a project with no dark mode at all")
nd, nd_code = run(os.path.join(FIX, "nodark"))
nd_kinds = [f["kind"] for f in nd.get("findings", [])]
check("reported once as no-dark-mode, not per token",
      nd_kinds.count("no-dark-mode") == 1)
check("no per-token missing-dark noise", "missing-dark" not in nd_kinds)
check("it is a warning, not a build-breaking error", nd_code == 0)

print("case: reachability — a stylesheet the app never loads")
REACH = os.path.join(FIX, "reach")
RCSS = os.path.join(REACH, "app", "frontend", "css")
rd, _ = run(RCSS)
reach = rd.get("reachability", {})
unreach = {f["token"]: f for f in rd.get("findings", [])
           if f["kind"] == "unreachable-stylesheet"}

check("the entry graph reaches a verdict on this fixture",
      reach.get("status") == "ok")

# CONTROL PAIR. The *same* defect — a semantic token with no dark counterpart —
# is planted in a live sheet and in a dead one. Asserting only the withheld half
# cannot tell "reachability worked" apart from "the checker went quiet", so the
# live half is asserted first and every withheld claim is paired with one.
check("control: the identical defect in a LIVE stylesheet is still reported",
      "missing-dark" in kinds_for(rd, "--reach-live-muted"))
check("the same defect in an unreachable stylesheet is withheld",
      "missing-dark" not in kinds_for(rd, "--reach-orphan-muted"))
check("control: a sheet reached through a css @import counts as live",
      "missing-dark" in kinds_for(rd, "--reach-shared-muted"))
# An alias (`@/…`) the resolver cannot expand must not turn a live sheet into a
# corpse; the filename fallback exists precisely so this error leans toward
# reporting more, not less.
check("control: an unresolvable aliased import is rescued, not buried",
      "missing-dark" in kinds_for(rd, "--reach-aliased-muted"))
check("a Storybook-only stylesheet's defect is withheld too",
      "missing-dark" not in kinds_for(rd, "--reach-sb-muted"))

orphan = unreach.get("app/frontend/css/reach-orphan.css")
sbsheet = unreach.get("app/frontend/css/reach-sb.css")
check("the dead sheet is summarised once, not silently dropped", orphan is not None)
check("one summary per dead file, not one per token", len(unreach) == 2)
check("the summary states how many findings it silenced",
      bool(orphan) and orphan["findings_withheld"] == 2)
check("the Storybook-only sheet is identified as such",
      bool(sbsheet) and sbsheet["storybook_only"] is True)
check("a plain orphan is NOT mislabelled Storybook-only",
      bool(orphan) and orphan["storybook_only"] is False)
check("live stylesheets are not listed as unreachable",
      not any(n in k for k in unreach
              for n in ("reach-app.css", "reach-shared.css", "reach-aliased.css")))

nr, _ = run(RCSS, "--no-reachability")
check("--no-reachability turns the pass off",
      nr.get("reachability", {}).get("status") == "off")
check("... and the withheld defects come back as ordinary defects",
      "missing-dark" in kinds_for(nr, "--reach-orphan-muted")
      and "missing-dark" in kinds_for(nr, "--reach-sb-muted"))
check("... with no unreachable-stylesheet summaries",
      not any(f["kind"] == "unreachable-stylesheet" for f in nr.get("findings", [])))
# The bookkeeping has to close. A summary that under-reports what it silenced
# hides findings just as effectively as saying nothing at all.
kept = [f for f in rd.get("findings", []) if f["kind"] != "unreachable-stylesheet"]
withheld = sum(f["findings_withheld"] for f in unreach.values())
check("withheld counts account for every finding that disappeared",
      len(nr.get("findings", [])) - len(kept) == withheld)

ed, _ = run(RCSS, "--entry", os.path.join(REACH, ".storybook", "preview.js"))
enames = {os.path.basename(p) for p in ed.get("reachability", {}).get("unreachable", {})}
check("--entry replaces the inferred graph (app sheet now unreachable)",
      ed.get("reachability", {}).get("status") == "ok" and "reach-app.css" in enames)
check("... and the sheet that entry does load is not called unreachable",
      "reach-sb.css" not in enames)
bad, bad_code = run(RCSS, "--entry", os.path.join(REACH, "does-not-exist.ts"))
check("--entry with a nonexistent path fails loudly instead of inferring",
      bad_code != 0 and "--entry" in bad.get("_stderr", ""))

print("case: reachability undecided is NOT a verdict of unreachable")
# Losing the graph and proving a file dead look identical from the finding list.
# They are opposite facts, so the undecided case must withhold nothing.
ne, _ = run(os.path.join(FIX, "reach-noentry", "css"))
check("no entry point found → status unknown",
      ne.get("reachability", {}).get("status") == "unknown")
check("... nothing is declared unreachable",
      not any(f["kind"] == "unreachable-stylesheet" for f in ne.get("findings", [])))
check("... and the planted defect is still counted",
      "missing-dark" in kinds_for(ne, "--noentry-muted"))

nh, _ = run(os.path.join(FIX, "reach-nohit", "css"))
nh_reach = nh.get("reachability", {})
check("entry found but the graph hit no audited sheet → still unknown",
      nh_reach.get("status") == "unknown" and bool(nh_reach.get("entries")))
check("... a genuinely orphan sheet is NOT accused while undecided",
      not any(f["kind"] == "unreachable-stylesheet" for f in nh.get("findings", [])))
check("... every planted defect is still counted",
      "missing-dark" in kinds_for(nh, "--nohit-muted")
      and "missing-dark" in kinds_for(nh, "--nohit-orphan-muted"))

# Regression guard for every case above this section: the reachability pass runs
# on the main css fixture too, and must not quietly withhold anything there.
guard, _ = run(os.path.join(FIX, "css"), "--refs", os.path.join(FIX, "views"))
check("the main css fixture loses nothing to reachability",
      not any(f["kind"] == "unreachable-stylesheet" for f in guard.get("findings", [])))

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
