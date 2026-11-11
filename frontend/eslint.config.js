import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "src/api/schema.gen.ts"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // Only `src/api/**` and `tests/**` may import `@/dev-fixtures/*`.
      // Features consume typed hooks (`@/api/*`), never fixtures or
      // `Fx*` types directly — so the Phase-5 merge swap to real
      // fetch calls is a one-line change per hook, with no call-site
      // touch. Covers alias (`@/dev-fixtures`) + any relative path.
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: [
                "@/dev-fixtures",
                "@/dev-fixtures/*",
                "**/dev-fixtures",
                "**/dev-fixtures/*",
              ],
              message:
                "Dev fixtures may only be imported from src/api/** or tests/**. Features consume typed hooks from @/api. See src/dev-fixtures/README.md.",
            },
          ],
        },
      ],
    },
  },
  {
    // NOTE: this override turns no-restricted-imports fully OFF for
    // these paths. Any future import bans we add to the base config
    // must also be re-declared here, or they will silently not apply
    // under src/api/, src/dev-fixtures/ and tests/.
    files: [
      "src/api/**/*.{ts,tsx}",
      "src/dev-fixtures/**/*.{ts,tsx}",
      "tests/**/*.{ts,tsx}",
    ],
    rules: {
      "no-restricted-imports": "off",
    },
  },
  {
    files: ["vite.config.ts", "eslint.config.js", "tests/**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.node, ...globals.browser },
    },
  },
);
