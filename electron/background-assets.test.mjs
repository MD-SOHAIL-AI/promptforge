import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  BackgroundAssetStore,
  MAX_BACKGROUND_IMAGE_BYTES,
  createBackgroundAssetUrl,
  parseBackgroundAssetUrl,
} from "../dist/electron/background-assets.js";

const PNG_BYTES = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00]);
const JPEG_BYTES = Buffer.from([0xff, 0xd8, 0xff, 0xdb, 0x00]);

function withTempDirectory(run) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-background-assets-"));
  return Promise.resolve(run(root)).finally(() => {
    fs.rmSync(root, { recursive: true, force: true });
  });
}

test("background images are copied with generated safe IDs and resolvable metadata", async () => {
  await withTempDirectory(async (root) => {
    const source = path.join(root, "night sky.png");
    const storage = path.join(root, "user-data", "desktop", "backgrounds");
    fs.writeFileSync(source, PNG_BYTES);
    const store = new BackgroundAssetStore(storage);

    const imported = await store.importImage(source);
    const resolved = await store.resolve(imported.assetId);
    const content = await store.read(imported.assetId);

    assert.match(imported.assetId, /^background-[0-9a-f-]+\.png$/);
    assert.equal(imported.filename, "night sky.png");
    assert.equal(imported.mediaType, "image/png");
    assert.equal(imported.url, createBackgroundAssetUrl(imported.assetId));
    assert.deepEqual(resolved, imported);
    assert.deepEqual(content?.bytes, PNG_BYTES);
    assert.equal(content?.mediaType, "image/png");
    assert.equal(fs.existsSync(path.join(storage, imported.assetId)), true);
    assert.equal(fs.existsSync(path.join(storage, `${imported.assetId}.json`)), true);
  });
});

test("background import rejects spoofed extensions and oversized files", async () => {
  await withTempDirectory(async (root) => {
    const store = new BackgroundAssetStore(path.join(root, "backgrounds"));
    const spoofed = path.join(root, "spoofed.jpg");
    fs.writeFileSync(spoofed, PNG_BYTES);
    await assert.rejects(store.importImage(spoofed), /extension does not match/i);

    const oversized = path.join(root, "oversized.png");
    fs.writeFileSync(oversized, PNG_BYTES);
    fs.truncateSync(oversized, MAX_BACKGROUND_IMAGE_BYTES + 1);
    await assert.rejects(store.importImage(oversized), /20 MB/i);
  });
});

test("only allowlisted background asset IDs can be resolved or removed", async () => {
  await withTempDirectory(async (root) => {
    const storage = path.join(root, "backgrounds");
    const outside = path.join(root, "outside.txt");
    fs.writeFileSync(outside, "keep");
    const store = new BackgroundAssetStore(storage);

    await assert.rejects(store.resolve("../outside.txt"), /invalid background asset identifier/i);
    await assert.rejects(store.remove("../outside.txt"), /invalid background asset identifier/i);
    assert.equal(fs.readFileSync(outside, "utf-8"), "keep");
    assert.equal(parseBackgroundAssetUrl("forgex-asset://background/..%2Foutside.png"), null);
    assert.equal(parseBackgroundAssetUrl("https://background/background-12345678-1234-4123-8123-123456789abc.png"), null);
  });
});

test("removing a background deletes only its copied asset and metadata", async () => {
  await withTempDirectory(async (root) => {
    const storage = path.join(root, "backgrounds");
    const source = path.join(root, "photo.jpeg");
    fs.writeFileSync(source, JPEG_BYTES);
    const store = new BackgroundAssetStore(storage);
    const imported = await store.importImage(source);
    const unrelated = path.join(storage, "keep.txt");
    fs.writeFileSync(unrelated, "keep");

    assert.equal(await store.remove(imported.assetId), true);
    assert.equal(await store.resolve(imported.assetId), null);
    assert.equal(fs.readFileSync(unrelated, "utf-8"), "keep");
    assert.equal(await store.remove(imported.assetId), false);
  });
});
