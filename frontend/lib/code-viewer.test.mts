import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

const source = readFileSync(resolve("frontend/components/code/code-viewer.tsx"), "utf-8");

test("code viewer does not use Monaco's cancellable loader hook", () => {
  assert.doesNotMatch(source, /\buseMonaco\b/);
});

test("code viewer captures Monaco from editor lifecycle callbacks", () => {
  assert.match(source, /beforeMount=\{configureEditorTheme\}/);
  assert.match(source, /onMount=\{\(editor, monaco\) => \{ editorRef\.current = editor; configureEditorTheme\(monaco\); \}\}/);
});
