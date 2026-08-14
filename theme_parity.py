#!/usr/bin/env python3
"""theme-parity — cross-platform light/dark design-token checker.

Two silent failure modes it exists for:

  1. A color token is defined for light with no dark counterpart. The light
     value is reused in dark mode; nothing errors and the build passes.
  2. A token is referenced but never defined. In CSS, `var(--missing)` with no
     fallback is invalid at computed-value time, so the whole declaration is
     dropped — for `background-color` that means transparent. No error, no
     warning, no build failure.

A related trap this checks for explicitly: a dark entry that *exists* but holds
the same value as light. Rules that only require "write both modes" pass such
tokens while they are, in effect, unadapted.

Usage:
  theme_parity.py <token-root> [--platform auto|xcassets|css|android|swift]
  theme_parity.py <token-root> --refs <view-dir>   # undefined refs + hardcoded
  theme_parity.py <token-root> --json              # exit 1 when errors exist
  theme_parity.py <token-root> --lang ko           # message language

Supported sources:
  xcassets  Xcode `.colorset/Contents.json` (recursive; float and 0x hex)
  css       CSS custom properties — `:root` = light; `.dark`,
            `[data-theme=dark]`, `@media (prefers-color-scheme: dark)` = dark
  android   `res/values/colors.xml` vs `res/values-night/colors.xml`
  swift     light/dark pair constructors, e.g. `make(light:dark:)`

See README for limitations. MIT licensed.
"""
import argparse
import colorsys
import glob
import json
import os
import re
import sys


# ── 색 계산 (WCAG 2.x) ──────────────────────────────────────────────────────
def _lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = (_lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hexs(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(c * 255))) for c in rgb)


def hue_deg(rgb):
    return colorsys.rgb_to_hsv(*rgb)[0] * 360


# ── 색각이상 시뮬 (Viénot 1999) ─────────────────────────────────────────────
_M = [[0.31399, 0.63951, 0.04649], [0.15537, 0.75789, 0.08670], [0.01775, 0.10945, 0.87256]]
_Mi = [[5.47221, -4.6419, 0.16963], [-1.1252, 2.29317, -0.1678], [0.02980, -0.19318, 1.16364]]
_SIM = {
    "protan": [[0, 1.05118294, -0.05116099], [0, 1, 0], [0, 0, 1]],
    "deutan": [[1, 0, 0], [0.9513092, 0, 0.04264193], [0, 0, 1]],
    "tritan": [[1, 0, 0], [0, 1, 0], [-0.86744736, 1.86727089, 0]],
}


def _mul(m, v):
    return [sum(m[i][j] * v[j] for j in range(3)) for i in range(3)]


def _unlin(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def simulate_cvd(rgb, kind):
    lms = _mul(_M, [_lin(c) for c in rgb])
    back = _mul(_Mi, _mul(_SIM[kind], lms))
    return tuple(_unlin(max(0, min(1, c))) for c in back)


def perceptual_gap(a, b):
    """러프한 지각 거리. 절대값보다 '어느 쌍이 가장 가까운가' 비교용."""
    return 100 * sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


# ── 메시지 (--lang) ─────────────────────────────────────────────────────────
# 문구를 코드에서 분리해 둔다. 판정 로직과 표현이 섞이면 언어를 늘릴 때마다
# 로직을 건드리게 되고, 그러다 판정이 바뀐다.
MSG = {
    "en": {
        "identical": "{hex} in both modes — defined but not adapted",
        "missing_dark": "no dark entry — the light value is reused in dark mode",
        "missing_light": "no light entry — the dark value is reused in light mode",
        "no_dark_mode": "no dark declaration anywhere — {n} semantic token(s) are light-only. This is \"dark mode not adopted\", not {n} separate defects",
        "dark_in_views": "no dark entry, but a `dark:` utility swaps it at the call "
                         "site — verify that every consumer does so",
        "dark_unparsed": "dark is declared as `{expr}` — an alias/function this tool "
                         "cannot resolve, so contrast and identical-value checks skip it",
        "light_unparsed": "light is declared as `{expr}` — same blind spot, opposite mode",
        "raw_scale": "{distinct} raw scale step(s) referenced directly in {sites} place(s) "
                     "(top: {top}) — bypasses the semantic layer, so there is no place to "
                     "point a different step in dark mode",
        "low_contrast": "{mode} worst {ratio:.2f}:1 on {bg}",
        "cvd": "{kind}: {a} ↔ {b} perceptual gap {gap:.1f} — not distinguishable by color alone",
        "undef": "referenced in {n} place(s) ({files}{more}) — never defined, no fallback; "
                 "resolves to transparent/invalid",
        "hardcoded": "{n} place(s) ({files}{more}) — bypasses tokens, stays fixed in dark mode",
        "hardcoded_total": "{distinct} literal color(s) · {sites} occurrence(s){capped}",
        "capped": " (top 15 listed)",
        "unresolved": "could not resolve dark symbol `{expr}` — blind spot",
        "header": "[{plat}] {n} tokens = {sem} semantic + {prim} primitive scale · "
                  "semantic with dark {d}/{sem} ({pct:.0f}%) · backgrounds {bgs}",
        "clean": "✅ no violations",
        "zero": "🔴 {plat}: read 0 tokens — check the path or format ({root})\n"
                "   (reporting 0 findings when nothing was parsed is the failure this tool prevents)",
        "nofmt": "no token source found: {root} (specify --platform)",
        "more": " et al.",
    },
    "ko": {
        "identical": "{hex} 가 양 모드 동일 — 정의는 됐지만 적응 안 됨",
        "missing_dark": "dark 항목 없음 — 라이트 값이 다크에서 그대로 쓰인다",
        "missing_light": "light 항목 없음 — 다크 값이 라이트에서 그대로 쓰인다",
        "no_dark_mode": "다크 선언이 어디에도 없다 — 시맨틱 {n}종이 라이트 전용. 결함 {n}건이 아니라 \"다크모드 미도입\" 한 건이다",
        "dark_in_views": "dark 항목은 없지만 쓰이는 자리에서 `dark:` 로 갈아끼운다 "
                         "— 소비처 전부가 그렇게 하는지 확인 필요",
        "dark_unparsed": "다크 값이 `{expr}` — alias/함수라 해석 못 함. 선언은 있으나 "
                         "대비·동일값 검사에서 빠진다(사각지대)",
        "light_unparsed": "라이트 값이 `{expr}` — 같은 사각지대, 반대 모드",
        "raw_scale": "원시 스케일 {distinct}종을 {sites}곳에서 직접 참조 (상위: {top}) "
                     "— 시맨틱 레이어를 우회해, 다크에서 다른 단계를 가리킬 지점이 없다",
        "low_contrast": "{mode} 최악 {ratio:.2f}:1 on {bg}",
        "cvd": "{kind}: {a} ↔ {b} 지각거리 {gap:.1f} — 색만으로 구분 불가 위험",
        "undef": "참조 {n}곳({files}{more}) — 정의 없음. 폴백도 없어 다크에서 투명/무효가 된다",
        "hardcoded": "{n}곳({files}{more}) — 토큰 우회, 다크에서 그대로 남는다",
        "hardcoded_total": "절대색 {distinct}종 · 총 {sites}곳{capped}",
        "capped": " (위는 상위 15종만)",
        "unresolved": "다크 값 심볼 `{expr}` 을 해석하지 못했다 — 검사 사각지대",
        "header": "[{plat}] 토큰 {n}개 = 시맨틱 {sem} + 원시스케일 {prim} · "
                  "시맨틱 다크 짝 {d}/{sem} ({pct:.0f}%) · 배경 {bgs}",
        "clean": "✅ 위반 없음",
        "zero": "🔴 {plat}: 토큰을 0개 읽었다 — 경로나 포맷을 확인하라 ({root})\n"
                "   (0건을 무결로 읽는 것이 이 도구가 예방하려는 실패다)",
        "nofmt": "토큰 정본을 찾지 못했다: {root} (--platform 으로 지정하라)",
        "more": " 외",
    },
}
LANG = "en"


def _m(key, **kw):
    return MSG[LANG][key].format(**kw)


# ── 공통 ────────────────────────────────────────────────────────────────────
# 의존성·빌드 산출물. 여기 있는 토큰은 우리 것이 아니라 검사 대상이 아니다.
# `builds`/`dist` 는 생성물이다. 생성물을 정의 소스로 세면 소스에 없는 토큰이
# '정의됨'으로 통과해 드리프트를 은폐한다 — 정본만 읽어야 한다.
# `.claude`/`worktrees` — 에이전트 워크트리·세션 사본. 레포 안에 레포가 통째로
# 들어앉아 있어 스캔하면 남의 브랜치 토큰이 이 프로젝트 결함으로 집계된다
# (실측: 한 레포에서 보고된 미정의 15종 중 대부분이 워크트리 사본이었다).
SKIP_DIRS = (".build", "builds", "node_modules", "Pods", "DerivedData", "vendor",
             "tmp", ".git", "build", "dist", ".next", "Carthage", "coverage",
             ".claude", "worktrees", ".worktrees", "Examples", "example")


def _walk(root, want):
    """want(파일명) 이 참인 파일 경로를 재귀로. 의존성 디렉토리는 건너뛴다."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if want(fn):
                yield os.path.join(dirpath, fn)


def _hex_rgb(h, order="rgba"):
    """hex 문자열 → ((r,g,b), alpha) 0..1.

    🔴 알파 바이트 위치가 플랫폼마다 다르다:
      CSS              `#RRGGBBAA` / `#RGBA`   → order="rgba"
      Android/Compose  `#AARRGGBB` / `0xAARRGGBB` → order="argb"
    한 함수가 둘을 뭉뚱그리면 CSS 의 `#FF000080`(빨강 50%)이 불투명 네이비로
    읽힌다 — 색이 틀린 채로 대비·동일값 판정이 진행된다.
    """
    s = h.strip().lstrip("#")
    if s.lower().startswith("0x"):
        s = s[2:]
        order = "argb"       # 0x 접두는 Swift/Compose 관례
    if not re.fullmatch(r"[0-9a-fA-F]+", s or ""):
        return None
    if len(s) in (3, 4):     # 축약형은 각 자리를 두 배로
        s = "".join(c * 2 for c in s)
    if len(s) == 8:
        if order == "argb":
            a, body = s[0:2], s[2:]
        else:
            a, body = s[6:8], s[0:6]
        return (tuple(int(body[i:i + 2], 16) / 255.0 for i in (0, 2, 4)),
                int(a, 16) / 255.0)
    if len(s) != 6:
        return None
    return (tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4)), 1.0)


# 원시 스케일(primitive palette) 판정 — `--color-primary-600`, `--gray-50` 처럼
# tint/shade 단계로 끝나는 이름.
#
# 🔴 원시 스케일에 다크 짝을 요구하면 안 된다. Radix·Material 3·Primer·Carbon·
# Tailwind 가 공통으로 쓰는 구조는 "스케일은 모드 무관 재료로 고정하고, 시맨틱
# 토큰이 모드별로 **다른 단계를 가리킨다**" 이다 (MD3: 같은 role 이 light 에서
# primary40, dark 에서 primary80). 스케일 자체를 50↔950 으로 뒤집는 방식은
# 표준이 아니다 — 모드 전환에 필요한 건 단순 반전이 아니라 대비·위계·채도
# 보정이라 브랜드색·중립색·상태색에 일괄 적용되지 않는다.
#
# 그래서 mode completeness 는 **시맨틱 토큰에만** 적용한다.
PRIMITIVE_SUFFIX = re.compile(r"-(?:50|950|[1-9]\d?00)$")


def is_primitive(name):
    return bool(PRIMITIVE_SUFFIX.search(name))


# ── 로더 ────────────────────────────────────────────────────────────────────
def load_colorsets(root):
    tokens = {}
    # 재귀. 예전에는 최상위만 glob 해서, colorset 이 하위 폴더에 있는 프로젝트는
    # "0개 찾음"으로 조용히 통과했다 — 0건을 무결로 읽는 전형적 함정.
    for cs in sorted(set(os.path.dirname(p) for p in
                         _walk(root, lambda f: f == "Contents.json")
                         if p.endswith(".colorset/Contents.json"))):
        name = os.path.basename(cs)[: -len(".colorset")]
        try:
            data = json.load(open(os.path.join(cs, "Contents.json")))
        except Exception:
            continue
        entry = {}
        for c in data.get("colors", []):
            if "color" not in c:
                continue
            dark = any(a.get("value") == "dark" for a in c.get("appearances") or [])
            v = c["color"].get("components")
            if not v:
                continue
            try:
                def one(x):
                    s = str(x).strip()
                    if s.startswith("0x"):
                        return int(s, 16) / 255.0
                    f = float(s)
                    return f if f <= 1.0 else f / 255.0
                entry["dark" if dark else "light"] = (
                    (one(v["red"]), one(v["green"]), one(v["blue"])),
                    float(v.get("alpha", 1)),
                )
            except Exception:
                pass
        if entry:
            tokens[name] = entry
    return tokens


def _last_top_level_semicolon(s):
    """따옴표 밖 마지막 `;` 의 인덱스. 없으면 -1."""
    q, idx = None, -1
    for i, ch in enumerate(s):
        if q:
            if ch == q and (i == 0 or s[i - 1] != "\\"):
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == ";":
            idx = i
    return idx


# 다크 블록에서 색으로 못 읽은 값 중 **실제 CSS 값**만 "선언됨"으로 친다.
# 아무 문자열이나 받으면 `content: "--x: #000"` 같은 문자열 리터럴이 선언으로
# 둔갑해, 그 토큰의 진짜 missing-dark 가 사라진다 (codex 지적).
DARK_VALUE_EXPR = re.compile(
    r"^(?:var\(|color-mix\(|light-dark\(|rgb|hsl|hwb|oklch|oklab|lab\(|lch\(|color\()",
    re.I)


def load_css(root):
    """CSS 커스텀 프로퍼티. :root=light, .dark/[data-theme=dark]/prefers-color-scheme=dark.

    한 파일 안에서 라이트/다크가 나란히 있어야 짝 누락을 셀 수 있다. 모드별 파일
    분리는 이 문제를 파일 레벨에서 재생산하므로, 여기서는 디렉토리 전체를 한 벌로 본다.
    """
    light, dark, dark_expr, light_expr = {}, {}, {}, {}
    files = sorted(_walk(root, lambda f: f.endswith((".css", ".scss"))))
    for path in files:
        try:
            css = open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        # 셀렉터 { ... } 블록을 훑어 다크 셀렉터인지 판정
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
            sel, body = m.group(1).strip(), m.group(2)
            # 🔴 셀렉터는 직전 `}` 이후 전부가 아니라 **마지막 `;` 이후**다.
            # 세미콜론으로 끝나는 at-rule(`@import`, `@custom-variant`, `@charset`)
            # 은 블록이 없으므로 다음 블록의 셀렉터 텍스트로 딸려온다. Tailwind v4
            # 의 `@custom-variant dark (&:where(.dark, .dark *));` 가 대표적인데,
            # 이 한 줄에 `.dark` 가 들어 있어 **바로 뒤 `@theme` 블록 전체가 다크로
            # 등록**된다. 실측(한 Tailwind v4 레포): 원시 스케일 43종이 통째로 "양 모드 동일"
            # 오탐으로 나왔고, 동시에 같은 블록의 시맨틱 토큰은 다크 짝이 있는 것으로
            # 둔갑해 **진짜 누락이 은폐**됐다. 오탐보다 이쪽이 더 나쁘다.
            #
            # 단 따옴표 안의 `;` 는 구분자가 아니다 — `:root[data-label="a;b"]` 에서
            # 통째로 잘라내면 `:root` 가 사라져 그 블록의 토큰을 **하나도 못 읽는다**.
            # 못 읽은 결과는 "위반 없음"으로 보이므로 이 실수는 조용히 통과한다.
            cut = _last_top_level_semicolon(sel)
            if cut >= 0:
                sel = sel[cut + 1:].strip()
            low = sel.lower()
            is_dark = (".dark" in low or 'data-theme="dark"' in low
                       or "data-theme='dark'" in low or "[data-theme=dark]" in low)
            # @media (prefers-color-scheme: dark) 안쪽인지 — 여는 위치로 판정
            head = css[:m.start()]
            opens = head.count("@media")
            if opens and re.search(
                    r"@media[^{]*prefers-color-scheme:\s*dark[^{]*\{(?:[^{}]|\{[^{}]*\})*$",
                    head, re.S):
                is_dark = True
            # `@theme` 은 Tailwind v4 의 토큰 정본이다. 여기를 라이트 소스로 세지
            # 않으면 v4 프로젝트의 팔레트가 통째로 안 읽힌다.
            if not (is_dark or ":root" in low or "html" in low or "body" in low
                    or low.startswith("@theme")):
                continue
            for name, val in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body):
                # `#` 없는 값은 색이 아니다. CSS 에 접두 없는 hex 색은 없는데
                # `#?` 로 열어두면 `--font-weight-bold: 700` 이 #770000 으로,
                # `--z-max: 9999` 가 #99999999 로 읽힌다 — 폰트 굵기에 대해
                # 대비 미달과 다크 짝 누락을 보고하게 된다(실측: 두 레포에서 재현).
                rgb = _hex_rgb(val) if re.fullmatch(r"\s*#[0-9a-fA-F]{3,8}\s*", val) else None
                if not rgb:
                    m2 = re.match(r"\s*rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)"
                                  r"(?:[,/\s]+([\d.]+))?\s*\)", val)
                    if m2:
                        rgb = (tuple(float(m2.group(i)) / 255.0 for i in (1, 2, 3)),
                               float(m2.group(4) or 1))
                if rgb:
                    (dark if is_dark else light)[name] = rgb
                    # 나중 선언이 이긴다 — 앞서 해석 못 한 alias 는 물러난다.
                    (dark_expr if is_dark else light_expr).pop(name, None)
                elif DARK_VALUE_EXPR.match(val.strip()) and not is_dark:
                    light_expr[name] = val.strip()
                    light.pop(name, None)
                elif is_dark and DARK_VALUE_EXPR.match(val.strip()):
                    # 값이 alias(`var(--x)`)나 함수(`color-mix(...)`)면 색으로는
                    # 못 읽는다. 그렇다고 없는 셈 치면 "다크 항목 없음 — 라이트
                    # 값이 그대로 쓰인다"는 **틀린 진단**이 나간다. 실제로는
                    # 선언이 있고, 우리가 못 읽을 뿐이다. 존재는 기록하고 값을
                    # 모른다는 사실을 따로 보고한다 — 사각지대는 사각지대로.
                    dark_expr[name] = val.strip()
                    dark.pop(name, None)
    tokens = {}
    for n, v in light.items():
        tokens[n] = {"light": v}
    for n, v in dark.items():
        tokens.setdefault(n, {})["dark"] = v
    for n, expr in dark_expr.items():
        if "dark" not in tokens.get(n, {}):
            tokens.setdefault(n, {})["dark_expr"] = expr
    for n, expr in light_expr.items():
        if "light" not in tokens.get(n, {}):
            tokens.setdefault(n, {})["light_expr"] = expr
    return tokens


def load_android(root):
    """res/values/colors.xml ↔ res/values-night/colors.xml"""
    light, dark = {}, {}
    for path in sorted(_walk(root, lambda f: f.endswith(".xml"))):
        parts = path.split(os.sep)
        if "colors.xml" not in path:
            continue
        vdir = next((p for p in parts if p.startswith("values")), None)
        if vdir is None:
            continue
        is_dark = "night" in vdir
        try:
            xml = open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for name, val in re.findall(r'<color\s+name="([^"]+)"\s*>([^<]+)</color>', xml):
            rgb = _hex_rgb(val, order="argb")   # Android 는 #AARRGGBB
            if rgb:
                (dark if is_dark else light)[name] = rgb
    tokens = {}
    for n, v in light.items():
        tokens[n] = {"light": v}
    for n, v in dark.items():
        tokens.setdefault(n, {})["dark"] = v
    return tokens


def load_swift(root):
    """`... = X.make(light: ...hex: 0xAABBCC), dark: <hex 또는 심볼>)` 쌍 선언.

    dark 가 심볼 참조(DarkHex.foo)면 같은 트리에서 그 상수의 hex 를 찾아 해석한다.
    해석 실패는 조용히 넘기지 않고 미해결로 남겨 호출부가 알 수 있게 한다.
    """
    files = sorted(_walk(root, lambda f: f.endswith(".swift")))
    consts, tokens, unresolved = {}, {}, []
    src_all = {}
    for p in files:
        try:
            src_all[p] = open(p, encoding="utf-8", errors="ignore").read()
        except Exception:
            pass
    # 1) 단순 상수 테이블: `static let foo = <hex>` / `= Color(hex: 0x..)`
    for src in src_all.values():
        for name, hx in re.findall(r"static\s+let\s+(\w+)\s*(?::[^=]+)?=\s*"
                                   r"(?:[\w.]*\(\s*hex:\s*)?(0x[0-9a-fA-F]{6,8})", src):
            consts[name] = hx
    # 2) light/dark 쌍
    pair = re.compile(
        r"static\s+let\s+(\w+)\s*(?::[^=]+)?=\s*[\w.]*make\(\s*"
        r"light:\s*[^,]*?(0x[0-9a-fA-F]{6,8})[^,]*,\s*"
        r"dark:\s*([^)]*?)\s*\)", re.S)
    for src in src_all.values():
        for name, lhex, dexpr in pair.findall(src):
            lv = _hex_rgb(lhex)
            dm = re.search(r"0x[0-9a-fA-F]{6,8}", dexpr)
            if dm:
                dv = _hex_rgb(dm.group(0))
            else:
                sym = re.search(r"(\w+)\s*$", dexpr.strip())
                key = sym.group(1) if sym else None
                dv = _hex_rgb(consts[key]) if key in consts else None
                if dv is None:
                    unresolved.append((name, dexpr.strip()))
            entry = {}
            if lv:
                entry["light"] = lv
            if dv:
                entry["dark"] = dv
            if entry:
                tokens[name] = entry
    return tokens, unresolved


# ── 감사 ────────────────────────────────────────────────────────────────────
BG_HINTS = ("surface", "background", "bg", "card", "sheet", "elevated", "canvas")
# 이름에 이게 들어가면 배경 후보에서 뺀다.
#  - inverse: 반전 표면. 그 위엔 전용 토큰(textOnInverse)만 얹히므로 일반 전경과 짝지으면 전부 오탐.
#  - border/hairline/divider/outline/stroke: 선이지 면이 아니다. 글자가 그 위에 얹히지 않는다.
#  - overlay/dim/ripple/scrim: 반투명 레이어. 합성 결과가 배경이라 단독 대비 계산이 무의미.
# glass/material/blur/vibrancy — iOS 계열의 반투명 재질. overlay 와 같은 이유로
# 뺀다: 뒤에 무엇이 깔리느냐로 실제 배경이 정해지므로 토큰 값 단독 대비는
# 의미가 없다 (실측: 이것 때문에 정상 전경색이 2.23:1 로 보고됐다).
BG_EXCLUDE = ("inverse", "border", "hairline", "divider", "outline", "stroke",
              "overlay", "dim", "ripple", "scrim", "shadow",
              "glass", "material", "blur", "vibrancy")
# 이 전경 토큰들은 전용 배경에만 얹히므로 일반 검사에서 제외한다.
FG_EXCLUDE = ("oninverse", "on_inverse", "inverse")

# 소비처의 다크 처리 — Tailwind 의 `dark:` 변형자, 또는 `.dark` 스코프 선택자.
DARK_CTX = re.compile(r"dark:|\.dark\b")
# 근접 창(문자). 한 클래스 속성 안의 라이트/다크 선언 쌍을 덮을 만큼 넓고,
# 무관한 다음 요소까지 삼키지 않을 만큼 좁게.
PROX = 400


def audit(tokens, bg_names=None, min_ratio=4.5):
    findings = []
    # 다크 선언이 **하나도** 없으면 그것은 토큰 N개의 결함이 아니라 "다크모드를
    # 도입하지 않았다"는 한 가지 사실이다. 45줄로 늘어놓으면 실제로 다크를 쓰는
    # 프로젝트의 진짜 누락과 구분되지 않는다.
    no_dark_at_all = not any("dark" in e or "dark_expr" in e for e in tokens.values())
    if no_dark_at_all:
        sem = [n for n in tokens if not is_primitive(n)]
        return ([{"kind": "no-dark-mode", "token": f"{len(sem)}종", "severity": "warn",
                  "detail": _m("no_dark_mode", n=len(sem))}] if sem else []), []
    if bg_names:
        bgs = [b for b in bg_names if b in tokens]
    else:
        bgs = [n for n in tokens
               if any(h in n.lower() for h in BG_HINTS)
               and not any(x in n.lower() for x in BG_EXCLUDE)]

    # 1) 정의는 됐으나 값이 같은 토큰 — 이번 사고의 근본 유형
    for n, e in sorted(tokens.items()):
        if "light" in e and "dark" in e and e["light"] == e["dark"]:
            findings.append({
                "kind": "identical-modes", "token": n, "severity": "warn",
                "detail": _m("identical", hex=hexs(e["light"][0])),
            })
        elif "dark" not in e:
            if "light" not in e:
                if "light_expr" in e:
                    findings.append({
                        "kind": "light-unparsed", "token": n, "severity": "warn",
                        "detail": _m("light_unparsed", expr=e["light_expr"]),
                    })
                    continue
                # 다크에만 존재. 값을 못 읽었더라도 라이트 누락은 라이트 누락이다
                # — 여기서 dark-unparsed(warn) 로 흘리면 다크 전용 토큰의 비대칭이
                # error 등급에서 통째로 빠진다 (codex 지적).
                findings.append({
                    "kind": "missing-light", "token": n, "severity": "error",
                    "detail": _m("missing_light"),
                })
                continue
            if is_primitive(n):
                continue        # 원시 스케일은 모드 무관이 정상 — 위 주석 참조
            if "dark_expr" in e:
                findings.append({
                    "kind": "dark-unparsed", "token": n, "severity": "warn",
                    "detail": _m("dark_unparsed", expr=e["dark_expr"]),
                })
                continue
            findings.append({
                "kind": "missing-dark", "token": n, "severity": "error",
                "detail": _m("missing_dark"),
            })
        elif "light" not in e:
            if "light_expr" in e:
                findings.append({
                    "kind": "light-unparsed", "token": n, "severity": "warn",
                    "detail": _m("light_unparsed", expr=e["light_expr"]),
                })
                continue
            # dark 만 있는 토큰. light-first 를 가정하고 조용히 통과시키면
            # 반대 방향 누락이 영영 안 보인다 — 방향을 대칭으로 둔다.
            findings.append({
                "kind": "missing-light", "token": n, "severity": "error",
                "detail": _m("missing_light"),
            })

    # 2) 전경↔배경 대비
    for mode in ("light", "dark"):
        for fg, fe in sorted(tokens.items()):
            if fg in bgs or mode not in fe or fe[mode][1] < 1:
                continue
            if any(x in fg.lower() for x in FG_EXCLUDE):
                continue
            # 선·구분자 토큰은 글자가 아니라 면 경계다 — 3:1 도 요구 대상이 아니다.
            if any(x in fg.lower() for x in ("border", "hairline", "divider", "outline", "stroke")):
                continue
            worst, worst_bg = None, None
            for bg in bgs:
                if mode not in tokens[bg] or tokens[bg][mode][1] < 1:
                    continue
                r = contrast(fe[mode][0], tokens[bg][mode][0])
                if worst is None or r < worst:
                    worst, worst_bg = r, bg
            if worst is None:
                continue
            if worst < 3.0:
                sev = "error"
            elif worst < min_ratio:
                sev = "warn"
            else:
                continue
            findings.append({
                "kind": "low-contrast", "token": fg, "mode": mode, "severity": sev,
                "ratio": round(worst, 2), "against": worst_bg,
                "detail": _m("low_contrast", mode=mode, ratio=worst, bg=worst_bg),
            })
    return findings, bgs


def _strip_comments(src):
    """/* */ · <!-- --> · // 줄주석 제거.

    주석 안의 `--x: #fff` 를 정의로 세면 실제로는 미정의인 토큰이 통과한다
    (codex 리뷰에서 재현됨). 반대로 주석 속 `var(--y)` 를 참조로 세면 오탐이 된다.
    문자열 리터럴까지 구분하려면 실제 파서가 필요하다 — 그건 알려진 한계다.
    """
    # `/*` 는 앞에 단어문자·슬래시가 없을 때만 주석 시작으로 본다.
    # 그렇게 하지 않으면 URL 경로(`/booking/manage/*`)나 glob 패턴이 주석을
    # 여는 것으로 오인되고, 그 뒤 첫 `*/` 까지의 **실제 코드가 통째로 삭제**된다.
    # 실측: 이 오인 하나가 토큰 정의 20줄을 지워 정상 토큰을 미정의로 보고했다.
    src = re.sub(r"(?<![\w/:.\-])/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"<!--.*?-->", " ", src, flags=re.S)
    src = re.sub(r"(?m)(?<![:\w/])//[^\n]*$", " ", src)
    return src


def collect_declared(*roots):
    """선언된 커스텀 프로퍼티 **이름 전부**. 색이 아닌 것(radius/shadow/spacing)과
    테마 블록·인라인 style 선언까지 포함한다.

    색 로더가 읽은 것만 '정의됨'으로 치면 `--radius-la` 같은 비색상 토큰이 전부
    미정의로 잡혀 오탐 더미가 된다. 오탐을 내는 검사기는 곧 무시당하므로,
    '정의 여부'와 '색으로 파싱되는가'는 분리해야 한다.
    """
    names = set()
    # 🔴 참조는 `.ts`/`.js` 에서 걷으면서(audit_refs) 선언은 걷지 않으면 비대칭이
    # 생긴다. 사용자별 테마를 TS 팔레트에 두고 인라인 style 로 주입하는 구조가
    # 흔한데, 그 토큰이 전부 "미정의"로 잡힌다 — 컴포넌트 한 계열이 통째로
    # 오탐이 된다(실측: 한 Svelte 레포의 프로필 컴포넌트 16종 전부).
    exts = (".css", ".scss", ".erb", ".html", ".haml", ".slim",
            ".vue", ".svelte", ".jsx", ".tsx",
            ".ts", ".js", ".mjs", ".cjs", ".json", ".rb")
    for root in roots:
        if not root or not os.path.exists(root):
            continue
        for path in _walk(root, lambda f: f.endswith(exts)):
            try:
                src = open(path, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            # 1) CSS 선언 및 JS/JSON 객체 키 — `--x: v`, `"--x": v`, `'--x': v`
            src = _strip_comments(src)
            names.update(re.findall(r"[\"']?(--[\w-]+)[\"']?\s*:", src))
            # 2) 프레임워크가 런타임에 주입하는 커스텀 프로퍼티. 정적 스캔으로는
            #    "선언"처럼 보이지 않지만 실제로는 항상 값이 들어간다. 이걸 빼면
            #    컴포넌트 로컬 토큰이 전부 미정의로 잡혀 오탐 더미가 된다
            #    (실측: 한 Svelte 프로젝트에서 보고 80곳 전부가 이 유형이었다).
            #    Svelte  `style:--x={v}`
            #    JSX     `style={{ "--x": v }}`  → 위 1) 이 처리
            #    Vue     `:style="{ '--x': v }"` → 위 1) 이 처리
            names.update(re.findall(r"style:(--[\w-]+)\s*=", src))
            # 3) JS 에서 직접 세팅 — setProperty("--x", v)
            names.update(re.findall(r"setProperty\(\s*[\"'](--[\w-]+)[\"']", src))
    return names


def audit_refs(view_root, defined, platform="css"):
    """뷰(템플릿)가 참조하는 토큰이 실재하는지 + 절대색 하드코딩.

    미정의 var 참조는 CSS 가 조용히 삼킨다 — 폴백 없는 `var(--없음)` 은 그 선언을
    무효로 만들어 background-color 면 transparent 가 된다. 빌드도 통과하고 에러도
    없어서, 다크에서만 배경이 사라지는 형태로 오래 살아남는다. 실측(Chromium):
      background-color: red; background-color: var(--nope);  → rgba(0,0,0,0)
    """
    findings = []
    # CSS/SCSS/JS/TS 도 포함한다. 이전에는 템플릿만 봐서 스타일시트 안의
    # `var(--없음)` 과 CSS-in-JS 를 통째로 놓쳤다 (codex 지적).
    # `.rb` — ViewComponent·Phlex·헬퍼는 클래스 문자열을 Ruby 쪽에 둔다. 템플릿만
    # 보면 그 프로젝트의 스타일 결정이 통째로 안 보인다(실측: 한 Rails 레포의 뱃지 6톤이
    # 전부 BadgeComponent 안에 있어 다크 처리 66건이 하나도 안 잡혔다).
    exts = (".erb", ".html", ".haml", ".slim", ".vue", ".svelte", ".jsx", ".tsx",
            ".xml", ".css", ".scss", ".js", ".ts", ".mjs", ".rb")
    seen_ref, hard, dark_handled = {}, {}, set()
    for path in sorted(_walk(view_root, lambda f: f.endswith(exts))):
        try:
            src = open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        src = _strip_comments(src)
        rel = os.path.relpath(path, view_root)
        for m in re.finditer(r"var\(\s*(--[\w-]+)\s*(,)?", src):
            if m.group(2):        # 폴백이 있으면 조용히 죽지 않는다
                continue
            # `var(--p${i})` · `var(--p{expr})` — 이름 뒤에 보간이 붙는 형태.
            # 잡힌 `--p` 는 토큰명이 아니라 접두사 조각이다. 이름이 하이픈으로
            # 끝나는 경우만 걸러서는 부족했다 (실측: 한 레포의 미정의 17곳 전부).
            if src[m.end(1):m.end(1) + 1] in ("$", "#", "{"):
                continue
            seen_ref.setdefault(m.group(1), []).append(rel)
            # 토큰에 다크 값이 없어도, 쓰이는 자리에서 `dark:` 유틸리티가 다른
            # 토큰을 가리키면 모드 전환은 이미 처리된 것이다 (Tailwind 관용구:
            # `bg-[color:var(--x-bg)] … dark:bg-transparent dark:text-[…-300]`).
            # 이걸 모르면 유틸리티 레이어에서 다크를 다루는 프로젝트는 시맨틱
            # 토큰 대부분이 missing-dark 로 뜬다(실측: 한 레포에서 66건).
            # 판정은 근접성으로만 한다 — 어느 요소에 걸린 선언인지까지는 파서
            # 없이 알 수 없으므로, "주변에 다크 처리가 아예 없다"는 확실한 쪽만
            # 결함으로 남기고 나머지는 사람이 볼 수 있게 종류를 나눈다.
            s = m.start()
            if DARK_CTX.search(src[max(0, s - PROX):s + PROX]):
                dark_handled.add(m.group(1))
        if platform == "android":
            # `@color/x` 는 Android 리소스 참조다. 플랫폼 구분 없이 수집하면
            # 웹 검사에서 ic_launcher_* 같은 것이 미정의 CSS 변수로 둔갑한다.
            for m in re.finditer(r"@color/([\w.]+)", src):
                seen_ref.setdefault(m.group(1), []).append(rel)
        # 하드코딩 절대색 — 토큰을 우회하므로 다크에서 그대로 남는다
        for m in re.finditer(
                r"(?<![\w-])#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})(?![\w-])"
                r"|(?:rgba?|hsla?|oklch|oklab|lab|lch)\([^)]*\)", src):
            hard.setdefault(m.group(0), []).append(rel)

    # 시맨틱 레이어 우회 — 뷰가 원시 스케일을 직접 박으면 모드별로 다른 단계를
    # 가리킬 지점이 사라진다. 다크에서 브랜드색을 한 단계 밝히려 해도 손잡이가 없다.
    raw = {n: len(f) for n, f in seen_ref.items() if is_primitive(n) and n in defined}
    if raw:
        top = sorted(raw.items(), key=lambda x: -x[1])
        findings.append({
            "kind": "raw-scale-ref", "token": f"{len(raw)}종", "severity": "warn",
            "distinct": len(raw), "sites": sum(raw.values()),
            "detail": _m("raw_scale", distinct=len(raw), sites=sum(raw.values()),
                         top=", ".join(f"{k}({v})" for k, v in top[:3])),
        })

    for name, files in sorted(seen_ref.items()):
        if name in defined:
            continue
        # 이름이 하이픈으로 끝나면 문자열 보간의 잘린 조각이다
        # (`--color-speaker-${i}`, `--surface-#{level}`). 실제 토큰명이 아니다.
        if name.endswith("-"):
            continue
        u = sorted(set(files))
        findings.append({
            "kind": "undefined-ref", "token": name, "severity": "error",
            "detail": _m("undef", n=len(files), files=", ".join(u[:2]),
                         more=_m("more") if len(u) > 2 else ""),
        })
    # 하드코딩은 종류가 수백 개일 수 있다. 상위만 개별 보고하되 **총계를 반드시**
    # 남긴다 — 상한에 걸린 수를 실제 수로 오독하면(전부 '15건'처럼 보인다)
    # 진단 자체가 거짓이 된다.
    ranked = sorted(hard.items(), key=lambda x: -len(x[1]))
    for lit, files in ranked[:15]:
        u = sorted(set(files))
        findings.append({
            "kind": "hardcoded-color", "token": lit, "severity": "warn",
            "detail": _m("hardcoded", n=len(files), files=", ".join(u[:2]),
                         more=_m("more") if len(u) > 2 else ""),
        })
    if ranked:
        findings.append({
            "kind": "hardcoded-total", "token": f"{len(ranked)}종",
            "severity": "warn", "distinct": len(ranked),
            "sites": sum(len(v) for v in hard.values()),
            "detail": _m("hardcoded_total", distinct=len(ranked),
                         sites=sum(len(v) for v in hard.values()),
                         capped=_m("capped") if len(ranked) > 15 else ""),
        })
    return findings, dark_handled


def audit_categorical(tokens, group_prefix, mode="dark"):
    """같은 역할의 색 묶음(화자·카테고리·태그)이 색각이상에서 구분되는지."""
    members = sorted(n for n in tokens if n.startswith(group_prefix) and mode in tokens[n])
    if len(members) < 2:
        return []
    out = []
    for kind in ("protan", "deutan", "tritan"):
        sims = {n: simulate_cvd(tokens[n][mode][0], kind) for n in members}
        pairs = [(perceptual_gap(sims[a], sims[b]), a, b)
                 for i, a in enumerate(members) for b in members[i + 1:]]
        d, a, b = min(pairs)
        if d < 25:
            out.append({
                "kind": "cvd-collision", "severity": "error" if d < 15 else "warn",
                "cvd": kind, "pair": [a, b], "gap": round(d, 1),
                "detail": _m("cvd", kind=kind, a=a, b=b, gap=d),
            })
    return out


def detect_platform(root):
    if any(True for _ in _walk(root, lambda f: f == "Contents.json")):
        if any(p.endswith(".colorset/Contents.json")
               for p in _walk(root, lambda f: f == "Contents.json")):
            return "xcassets"
    if any(True for _ in _walk(root, lambda f: f == "colors.xml")):
        return "android"
    if any(True for _ in _walk(root, lambda f: f.endswith((".css", ".scss")))):
        return "css"
    if any(True for _ in _walk(root, lambda f: f.endswith(".swift"))):
        return "swift"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="토큰 정본 경로 (xcassets · 스타일시트 · res · Sources)")
    ap.add_argument("--platform", default="auto",
                    choices=["auto", "xcassets", "css", "android", "swift"])
    ap.add_argument("--refs", help="뷰/템플릿 디렉토리 — 미정의 참조·하드코딩 검사")
    ap.add_argument("--bg", help="배경 토큰 쉼표구분 (미지정 시 이름으로 추정)")
    ap.add_argument("--group", action="append", default=[],
                    help="범주형 색 묶음 접두사 (예: speaker). 반복 지정 가능")
    ap.add_argument("--min", type=float, default=4.5)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--lang", default="en", choices=sorted(MSG))
    ap.add_argument("--ignore", action="append", default=[], metavar="GLOB",
                    help="토큰 이름 glob — 매치하면 결과에서 제외 (반복 지정 가능). "
                         "다크가 필요 없는 토큰(로고색·런처 배경)을 끄는 용도. "
                         "끄는 수단이 없으면 소음 때문에 도구 전체가 무시된다. "
                         "값이 --로 시작하므로 --ignore='--brand*' 처럼 = 로 붙여 쓴다.")
    ap.add_argument("--only", action="append", default=[], metavar="KIND",
                    help="이 kind 만 보고 (예: undefined-ref). 신뢰도 높은 검사만 "
                         "CI 게이트로 쓰고 나머지는 참고로 둘 때.")
    a = ap.parse_args()
    global LANG
    LANG = a.lang

    plat = a.platform if a.platform != "auto" else detect_platform(a.root)
    if plat is None:
        sys.exit(_m("nofmt", root=a.root))

    unresolved = []
    if plat == "xcassets":
        tokens = load_colorsets(a.root)
    elif plat == "css":
        tokens = load_css(a.root)
    elif plat == "android":
        tokens = load_android(a.root)
    else:
        tokens, unresolved = load_swift(a.root)

    # 0건은 "문제 없음"이 아니라 "못 읽었다"일 수 있다. 조용히 초록을 내지 않는다.
    if not tokens:
        msg = _m("zero", plat=plat, root=a.root)
        if a.json:   # CI 는 항상 JSON 을 파싱한다 — 이 경로만 평문이면 거기서 깨진다
            print(json.dumps({"platform": plat, "tokens": 0, "with_dark": 0,
                              "error": "no-tokens-parsed", "message": msg},
                             ensure_ascii=False, indent=2))
            sys.exit(1)
        sys.exit(msg)

    if a.bg:
        want = [b.strip() for b in a.bg.split(",")]
        unknown = [b for b in want if b not in tokens]
        if len(unknown) == len(want):
            sys.exit(f"--bg matched no token: {', '.join(unknown)}")
        for b in unknown:
            print(f"⚠ --bg: unknown token '{b}' ignored", file=sys.stderr)
    findings, bgs = audit(tokens, a.bg.split(",") if a.bg else None, a.min)
    for g in a.group:
        findings += audit_categorical(tokens, g)
    if a.refs and not os.path.exists(a.refs):
        sys.exit(f"--refs path does not exist: {a.refs}")
    if a.refs:
        # 정의 집합 = 색으로 파싱된 토큰 ∪ 선언된 모든 커스텀 프로퍼티 이름
        # (정본 트리 + 뷰 안 인라인 선언). 색 파서가 못 읽은 비색상 토큰을
        # 미정의로 오탐하지 않기 위해 반드시 분리해서 모은다.
        declared = set(tokens) | collect_declared(a.root, a.refs)
        ref_findings, dark_handled = audit_refs(a.refs, declared, platform=plat)
        findings += ref_findings
        # 토큰에 다크 값이 없어도 소비처에서 `dark:` 로 갈아끼우면 결함이 아니다.
        # 지우지 않고 종류만 바꾼다 — 근접성 판정이라 확신할 수 없고, 지워버리면
        # 진짜 누락이 이 경로로 조용히 사라진다.
        # 🔴 severity 는 낮추지 않는다. 근접 판정은 "이 토큰 근처에 다크 처리가
        # 있다"까지만 알 뿐, 소비처 **전부**가 그런지는 모른다. 열 곳에서 쓰이고
        # 한 곳만 다크를 다뤄도 재분류된다. 등급까지 내리면 CI 가 진짜 결함을
        # 통과시키므로, 바꾸는 것은 **종류(=읽는 사람의 분류)** 뿐이다.
        for f in findings:
            if f["kind"] == "missing-dark" and f["token"] in dark_handled:
                f["kind"] = "dark-handled-in-views"
                f["detail"] = _m("dark_in_views")
    for name, expr in unresolved:
        findings.append({"kind": "unresolved-dark", "token": name, "severity": "warn",
                         "detail": _m("unresolved", expr=expr)})

    if a.ignore:
        import fnmatch
        findings = [f for f in findings
                    if not any(fnmatch.fnmatch(str(f.get("token", "")), g)
                               for g in a.ignore)]
    if a.only:
        findings = [f for f in findings if f["kind"] in a.only]

    # 커버리지는 **시맨틱 토큰 기준**으로 센다. 원시 스케일을 분모에 넣으면
    # 모드 무관이 정상인 것들이 미달로 잡혀 수치가 실제보다 훨씬 나쁘게 보인다
    # (실측: 같은 레포가 16% → 22%, 그리고 그 16%가 오판의 출발점이었다).
    sem = {n: e for n, e in tokens.items() if not is_primitive(n)}
    n_prim = len(tokens) - len(sem)
    n_dark = sum(1 for e in sem.values() if "dark" in e or "dark_expr" in e)
    if a.json:
        print(json.dumps({"platform": plat, "tokens": len(tokens),
                          "semantic": len(sem), "primitive": n_prim,
                          "with_dark": n_dark, "backgrounds": bgs, "findings": findings},
                         ensure_ascii=False, indent=2))
    else:
        pct = n_dark / len(sem) * 100 if sem else 0
        print(_m("header", plat=plat, n=len(tokens), prim=n_prim, sem=len(sem),
                 d=n_dark, pct=pct, bgs=bgs) + "\n")
        if not findings:
            print(_m("clean"))
        order = {"undefined-ref": 0, "missing-dark": 1, "identical-modes": 2,
                 "dark-unparsed": 3, "dark-handled-in-views": 4,
                 "light-unparsed": 5}
        for f in sorted(findings, key=lambda x: (x["severity"] != "error",
                                                 order.get(x["kind"], 9), x["kind"])):
            mark = "🔴" if f["severity"] == "error" else "⚠"
            who = f.get("token") or "/".join(f.get("pair", []))
            print(f"{mark} [{f['kind']}] {who} — {f['detail']}")

    sys.exit(1 if any(f["severity"] == "error" for f in findings) else 0)


if __name__ == "__main__":
    main()
