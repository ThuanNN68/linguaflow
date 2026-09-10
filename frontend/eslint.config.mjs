import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    ".test-dist/**",
  ]),
  {
    files: ["src/features/chat/**/*.{ts,tsx}"],
    rules: {
      // The migrated chat keeps its proven UI markup and client-side state flow.
      "@next/next/no-img-element": "off",
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": "off",
      "react-hooks/purity": "off",
      "react-hooks/set-state-in-effect": "off",
      "react/no-unescaped-entities": "off",
    },
  },
  {
    files: ["src/features/auth/**/*.{ts,tsx}"],
    rules: {
      // Preserve the supplied authentication copy and punctuation verbatim.
      "react/no-unescaped-entities": "off",
    },
  },
]);

export default eslintConfig;
