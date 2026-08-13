# theme-parity

Finds light/dark theme token defects across web, iOS, and Android — in one pass.

Two failure modes it exists for:

1. **A color token has no dark counterpart.** The light value is silently reused in dark mode. Nothing errors; the build passes.
2. **A token is referenced but never defined.** In CSS, `var(--missing)` without a fallback is *invalid at computed-value time* — the whole declaration is dropped. For `background-color`, that means **transparent**. No error, no warning, no build failure. It only shows up as "the background disappeared in dark mode."

Measured, not asserted:

```
background-color: red; background-color: var(--nope);   → rgba(0, 0, 0, 0)
background-color: red; background-color: var(--nope, blue); → rgb(0, 0, 255)
```

## Why this is a structural problem, not a discipline problem

Across 14 real projects sharing the same team and conventions:

| Token layer | Dark-pair coverage |
|---|---|
| Swift (`make(light:dark:)` style constructor) | **100%**, every project |
| CSS custom properties | **0%–64%** |

The difference is not diligence. The Swift constructor **takes both values as required arguments** — omitting dark does not compile. CSS lets you write `--x: #fff` and walk away; the dark value lives in another block, in another file, and nothing checks that it exists.

Pair-requiring APIs don't need a rule. Pair-optional APIs need a checker. This is the checker.

## Usage

```bash
python3 theme_parity.py <token-root> [--platform auto|css|xcassets|android|swift]
python3 theme_parity.py <token-root> --refs <view-dir>    # + undefined refs, hardcoded colors
python3 theme_parity.py <token-root> --json               # exit 1 on errors
```

Examples:

```bash
python3 theme_parity.py app/assets/stylesheets --refs app/views
python3 theme_parity.py Sources/DesignSystem --platform swift
python3 theme_parity.py app/src/main/res --platform android
```

## What it checks

| Check | Severity | Trust |
|---|---|---|
| `undefined-ref` — referenced token has no definition, no fallback | error | **High.** Existence is a mechanical fact. |
| `missing-dark` — no dark counterpart | error | **High**, but some tokens legitimately need none (logo colors, launcher backgrounds). |
| `identical-modes` — dark is defined but equal to light | warn | High. Defined ≠ adapted. |
| `hardcoded-color` — literal hex/rgb in templates | warn | Medium. Gradients and shadows are often fine. |
| `low-contrast` — WCAG pair check | warn/error | **Low by default.** Backgrounds are guessed from token names. Pass `--bg` to make it meaningful. |
| `cvd-collision` — categorical colors indistinguishable under color-vision deficiency | warn/error | Medium. Use `--group <prefix>` for speaker/tag/chart color sets. |

Sort your attention by that last column. A checker that reports 200 findings of mixed quality gets ignored entirely — which is worse than no checker.

## Limitations — read before trusting it

This was built against a specific stack and is honest about that:

- **CSS parsing is regex-based.** Nested rules, SCSS control flow, and CSS-in-JS are not properly handled. Flat custom-property blocks work well; complex preprocessor output may be misread.
- **The Swift loader keys on a `make(light:dark:)`-shaped declaration.** Projects using Asset Catalogs, `UITraitCollection` closures written differently, or another naming convention will read **zero tokens** — which the tool reports as a failure rather than a pass, but it still means no coverage.
- **Android reads `res/values/colors.xml` vs `values-night/`.** Jetpack Compose `Color.kt` / `lightColorScheme` is **not** parsed yet. On Compose-first projects the token count will be misleadingly small.
- **`low-contrast` infers backgrounds from token names** and produces false positives. It is a hint, not a verdict.
- **Verified on ~14 projects of similar shape** (Rails + Tailwind, SwiftUI). Behavior on other stacks is untested.
- Output messages are currently in Korean; the API and flags are English. i18n is not done.

If it reads zero tokens it exits with an error rather than reporting success — reporting "0 problems" when the real cause is "0 files parsed" is the exact failure this tool was written to prevent.

## Design notes

- **The checker never stores expected values.** It parses the source of truth directly. A checker holding its own copy of the canon goes green while the canon drifts.
- **No silent fallbacks.** "If dark is missing, assume light" makes an omission indistinguishable from a deliberate match. Missing fails; deliberate sameness must be written explicitly.
- **Generated output is excluded** (`builds/`, `dist/`, `node_modules/`, `.build/`, …). Counting generated files as definitions hides drift.
- **Definition-ness and color-parseability are separate.** Otherwise every non-color token (`--radius-*`, `--shadow-*`) is reported as undefined and the output becomes noise.

## Requirements

Python 3.8+. No dependencies.

## License

MIT
