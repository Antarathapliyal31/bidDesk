/**
 * WCAG AA contrast guard for design tokens (SPEC §11.5).
 *
 * This test reads src/styles/tokens.css and derives every pair from
 * the tokens themselves — no hard-coded hex values. It parses the
 * @theme, :root and .dark blocks, resolves var(--…) refs, and asserts
 * each foreground/background pair against 4.5:1 (normal text) in both
 * light and dark mode. If a semantic palette drift lowers contrast
 * below AA, this test fails before the UI reaches the screen.
 */
import { readFileSync } from "node:fs";
import { resolve as pathResolve } from "node:path";

import { describe, expect, it } from "vitest";

// Vitest runs from the frontend/ package root (package.json's cwd),
// so this is stable across jsdom/node environments.
const TOKENS_PATH = pathResolve(process.cwd(), "src/styles/tokens.css");
const RAW_CSS = readFileSync(TOKENS_PATH, "utf8");
// Strip /* ... */ comments so block-selector lookups can't match
// inline examples inside doc-comments (e.g. the `.dark { ... }` line
// in F2's note). Keep newlines so error messages still line up.
const CSS = RAW_CSS.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ""));

const AA_NORMAL = 4.5;
const AA_NON_TEXT = 3.0;

function hexToLinear(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16) / 255;
  const g = parseInt(h.slice(2, 4), 16) / 255;
  const b = parseInt(h.slice(4, 6), 16) / 255;
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  return [lin(r), lin(g), lin(b)];
}
function relativeLuminance(hex: string): number {
  const [r, g, b] = hexToLinear(hex);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  const [light, dark] = la > lb ? [la, lb] : [lb, la];
  return (light + 0.05) / (dark + 0.05);
}

/** Pull the body of the first block whose selector matches exactly. */
function extractBlock(css: string, selectorNeedle: string): string {
  const idx = css.indexOf(selectorNeedle);
  if (idx < 0) throw new Error(`tokens.css: block not found: ${selectorNeedle}`);
  const open = css.indexOf("{", idx);
  if (open < 0) throw new Error(`tokens.css: no opening brace after ${selectorNeedle}`);
  let depth = 1;
  let i = open + 1;
  while (depth > 0 && i < css.length) {
    const ch = css[i];
    if (ch === "{") depth++;
    else if (ch === "}") depth--;
    i++;
  }
  if (depth !== 0) throw new Error(`tokens.css: unbalanced braces for ${selectorNeedle}`);
  return css.slice(open + 1, i - 1);
}

function parseDecls(body: string): Record<string, string> {
  const map: Record<string, string> = {};
  const re = /--([a-z0-9-]+)\s*:\s*([^;]+);/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(body)) !== null) {
    const [, name, value] = m;
    if (name === undefined || value === undefined) continue;
    map[`--${name}`] = value.trim();
  }
  return map;
}

const theme = parseDecls(extractBlock(CSS, "@theme {"));
const root = parseDecls(extractBlock(CSS, ":root {"));
const dark = parseDecls(extractBlock(CSS, ".dark {"));

function mergeFor(mode: "light" | "dark"): Record<string, string> {
  return mode === "light" ? { ...theme, ...root } : { ...theme, ...root, ...dark };
}

const VAR_RE = /^var\((--[a-z0-9-]+)\)$/i;
const HEX_RE = /^#[0-9a-fA-F]{6}$/;

function resolve(name: string, mode: "light" | "dark"): string {
  const merged = mergeFor(mode);
  const initial = merged[name];
  if (initial === undefined) throw new Error(`${mode}: token not found: ${name}`);
  let value: string = initial;
  for (let i = 0; i < 10; i++) {
    const m: RegExpMatchArray | null = value.match(VAR_RE);
    if (!m) break;
    const ref = m[1];
    if (ref === undefined) break;
    const next = merged[ref];
    if (next === undefined) throw new Error(`${mode}: cannot resolve ${name} -> ${ref}`);
    value = next;
  }
  if (!HEX_RE.test(value)) {
    throw new Error(`${mode}: ${name} did not resolve to #rrggbb (got "${value}")`);
  }
  return value.toLowerCase();
}

/**
 * Shadcn-slot foreground/background pairs. These are mandatory
 * contrast pairs: anywhere the UI paints text on a surface, these
 * are the token pairs it uses.
 */
const shadcnPairs: Array<[fg: string, bg: string]> = [
  ["--foreground", "--background"],
  ["--card-foreground", "--card"],
  ["--popover-foreground", "--popover"],
  ["--primary-foreground", "--primary"],
  ["--secondary-foreground", "--secondary"],
  ["--muted-foreground", "--muted"],
  ["--accent-foreground", "--accent"],
  ["--destructive-foreground", "--destructive"],
  // Compositional pairs the UI actually renders:
  // - muted-foreground on card: sidebar "Recent runs" subtitle, the
  //   placeholder paragraph, meta rows inside document cards.
  // - foreground on accent: active nav link in the sidebar, hovered
  //   rows in the history table.
  // - injected-violet-foreground on injected-violet: the "Run with N
  //   injected faults" button in the New-bid SubmitBar.
  ["--muted-foreground", "--card"],
  ["--foreground", "--accent"],
  ["--color-injected-violet-foreground", "--color-injected-violet"],
];

/**
 * Semantic text tokens must stay readable on every surface they can
 * land on: --background (page), --card (document / section), --muted
 * (zebra / disabled). We do NOT paint them on --primary / --destructive
 * / --accent; those have their own *-foreground pairs above.
 */
const semanticTextTokens = [
  "--color-done-green-text",
  "--color-warn-amber-text",
  "--color-fail-red-text",
  "--color-run-blue-text",
  "--color-injected-violet-text",
];
const semanticSurfaces = ["--background", "--card", "--muted"];

function runPair(mode: "light" | "dark", fgName: string, bgName: string, threshold = AA_NORMAL) {
  const fg = resolve(fgName, mode);
  const bg = resolve(bgName, mode);
  const ratio = contrastRatio(fg, bg);
  if (ratio < threshold) {
    throw new Error(
      `${mode}: ${fgName} (${fg}) on ${bgName} (${bg}) = ${ratio.toFixed(2)}:1 ` +
        `(need ≥ ${threshold}:1)`,
    );
  }
  expect(ratio).toBeGreaterThanOrEqual(threshold);
}

describe("design-token contrast — WCAG AA (≥ 4.5:1)", () => {
  for (const mode of ["light", "dark"] as const) {
    describe(`${mode} mode · shadcn fg/bg slots`, () => {
      for (const [fg, bg] of shadcnPairs) {
        it(`${fg} on ${bg}`, () => runPair(mode, fg, bg));
      }
    });

    describe(`${mode} mode · semantic text on every surface`, () => {
      for (const fg of semanticTextTokens) {
        for (const bg of semanticSurfaces) {
          it(`${fg} on ${bg}`, () => runPair(mode, fg, bg));
        }
      }
    });

    describe(`${mode} mode · non-text UI (≥ 3:1)`, () => {
      // The sidebar's active-nav indicator paints --primary on
      // --accent. SPEC §11.5 WCAG AA requires ≥ 3:1 for non-text.
      it("--primary on --accent (active-nav indicator)", () =>
        runPair(mode, "--primary", "--accent", AA_NON_TEXT));
    });
  }

  describe("checker self-test", () => {
    it("reports < 4.5 for a known-failing pair (indigo-500 on white)", () => {
      // #6670e0 is the indigo-500 declared in @theme — intentionally
      // *not* used as a solid background under white text (fails AA
      // ~4.2:1). If this ever climbs above 4.5, the checker is broken.
      const ratio = contrastRatio("#6670e0", "#ffffff");
      expect(ratio).toBeLessThan(AA_NORMAL);
    });
    it("reports a very high ratio for black on white", () => {
      expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 0);
    });
    it("ignores --properties that appear inside /* … */ comments", () => {
      // We synthesize a snippet containing a declaration in a comment
      // and assert the parser doesn't see it. Protects against the
      // real-world bug we had: tokens.css doc-comment shows an example
      // `.dark { --background: ... }`, and the raw indexOf matched
      // the comment instead of the real .dark block.
      const snippet = `
        /* Example: .dark { --primary: #000000; } */
        :root { --primary: #ffffff; }
      `.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ""));
      const decls = parseDecls(extractBlock(snippet, ":root {"));
      expect(decls["--primary"]).toBe("#ffffff");
    });
  });
});
