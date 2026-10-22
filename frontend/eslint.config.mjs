import { FlatCompat } from "@eslint/eslintrc";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const currentDirectory = dirname(fileURLToPath(import.meta.url));
const compat = new FlatCompat({ baseDirectory: currentDirectory });

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  { ignores: [".next*/**", "node_modules/**", "vendor/**", "public/vendor/**", "next-env.d.ts", "test-results/**", "playwright-report/**"] },
];

export default eslintConfig;
