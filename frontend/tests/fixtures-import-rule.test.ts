// @vitest-environment node
/**
 * Verifies the ESLint `no-restricted-imports` rule that scopes
 * `@/dev-fixtures/*` to `src/api/**` and `tests/**` only. Features
 * consume typed hooks from `@/api` instead — not fixtures or `Fx*`
 * types — so the Phase-5 merge swap to real fetch calls is a
 * one-line change per hook.
 *
 * Node env so `import.meta.url` is a file:// URL.
 */
import { fileURLToPath } from "node:url";

import { ESLint } from "eslint";
import { beforeAll, describe, expect, it } from "vitest";

const ROOT = fileURLToPath(new URL("..", import.meta.url));

let eslint: ESLint;

beforeAll(() => {
  eslint = new ESLint({ cwd: ROOT });
});

async function lintAt(relativeFilePath: string, code: string) {
  const [result] = await eslint.lintText(code, { filePath: `${ROOT}/${relativeFilePath}` });
  return result?.messages ?? [];
}

const aliased =
  'import { FIXTURE_RUNS } from "@/dev-fixtures";\nexport const n = FIXTURE_RUNS.length;\n';
const relOneUp =
  'import { FIXTURE_RUNS } from "../dev-fixtures/runs";\nexport const n = FIXTURE_RUNS.length;\n';
const relTwoUp = 'import { sleep } from "../../dev-fixtures/delay";\nvoid sleep;\n';

function hasRestricted(messages: { ruleId: string | null }[]) {
  return messages.some((m) => m.ruleId === "no-restricted-imports");
}

describe("dev-fixtures import scope", () => {
  it("blocks the alias from src/components", async () => {
    const msgs = await lintAt("src/components/__synthetic__.tsx", aliased);
    expect(hasRestricted(msgs)).toBe(true);
  });

  it("blocks a relative import from src/components", async () => {
    const msgs = await lintAt("src/components/__synthetic__.tsx", relOneUp);
    expect(hasRestricted(msgs)).toBe(true);
  });

  it("blocks a deeper relative import from src/components/ui", async () => {
    const msgs = await lintAt("src/components/ui/__synthetic__.tsx", relTwoUp);
    expect(hasRestricted(msgs)).toBe(true);
  });

  it("blocks a relative import from src/app", async () => {
    const msgs = await lintAt("src/app/__synthetic__.tsx", relOneUp);
    expect(hasRestricted(msgs)).toBe(true);
  });

  it("BLOCKS the alias from src/features (features consume @/api, not fixtures)", async () => {
    const msgs = await lintAt("src/features/history/__synthetic__.tsx", aliased);
    expect(hasRestricted(msgs)).toBe(true);
  });

  it("allows the alias from src/api", async () => {
    const msgs = await lintAt("src/api/__synthetic__.ts", aliased);
    expect(hasRestricted(msgs)).toBe(false);
  });

  it("allows the alias from tests/", async () => {
    const msgs = await lintAt("tests/__synthetic__.test.ts", aliased);
    expect(hasRestricted(msgs)).toBe(false);
  });
});
