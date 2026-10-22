import assert from "node:assert/strict";
import test from "node:test";
import { buildSync } from "esbuild";
import { fileURLToPath } from "node:url";

function load(relative) {
  const result = buildSync({entryPoints: [fileURLToPath(new URL(relative, import.meta.url))],
    bundle: true, platform: "node", format: "esm", write: false});
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
}
const { MAX_MESSAGE_CHARACTERS, messageCharacterCount, messageValidationError } = await load("./message-validation.ts");
const { apiValidationMessage } = await load("./errors.ts");

test("instructions above the old limit fit the shared cap", () => {
  assert.equal(MAX_MESSAGE_CHARACTERS, 65536);
  assert.equal(messageValidationError("x".repeat(25000)), null);
  assert.equal(messageValidationError("x".repeat(MAX_MESSAGE_CHARACTERS)), null);
  assert.match(messageValidationError("x".repeat(MAX_MESSAGE_CHARACTERS + 1)), /limit by 1 characters.*draft is saved/);
});

test("Unicode counts match Python API character limits", () => {
  assert.equal(messageCharacterCount("  😀hi\n"), 3);
  assert.equal(messageValidationError("😀".repeat(MAX_MESSAGE_CHARACTERS)), null);
  assert.match(messageValidationError("😀".repeat(MAX_MESSAGE_CHARACTERS + 2)), /limit by 2 characters/);
});

test("API validation reports the specific field without copying the instruction", () => {
  assert.equal(apiValidationMessage({message: "Request validation failed", details: {errors: [
    {location: ["body", "content"], message: "String should have at most 65536 characters", input: "PRIVATE_INSTRUCTION"}
  ]}}), "content: String should have at most 65536 characters");
  assert.equal(apiValidationMessage({message: "Something else"}), null);
});
