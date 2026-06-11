// Flat ESLint config for the canary extension — the shape the managed
// npm-quality gate assumes (eslint resolvable via `npx --no-install`,
// warnings are failures). See arthur-debert/release#587.

import eslint from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["out/", "node_modules/", "*.vsix"] },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
);
