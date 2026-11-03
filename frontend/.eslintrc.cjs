module.exports = {
  root: true,
  env: { browser: true, es2022: true },
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react-hooks/recommended",
  ],
  ignorePatterns: ["dist", "playwright-report", "test-results", "blob-report", ".eslintrc.cjs"],
  parser: "@typescript-eslint/parser",
  plugins: ["react-refresh"],
  rules: {
    "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
    "@typescript-eslint/no-unused-vars": [
      "error",
      { argsIgnorePattern: "^_", varsIgnorePattern: "^_", ignoreRestSiblings: true },
    ],
  },
  overrides: [
    {
      // Node-side tooling: Playwright specs/fixtures are not React (`use` is a fixture, not a hook).
      files: ["e2e/**/*.ts", "playwright.config.ts", "vite.config.ts"],
      env: { browser: false, node: true },
      rules: { "react-hooks/rules-of-hooks": "off", "react-refresh/only-export-components": "off" },
    },
  ],
};
