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
  assertBackgroundImageDimensions,
} from "../dist/electron/background-assets.js";

const PNG_BYTES = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64");
// A complete SOF dimension header; pixel decoding is validated by the renderer.
const JPEG_BYTES = Buffer.from([0xff,0xd8,0xff,0xc0,0,11,8,0,1,0,1,1,1,0x11,0,0xff,0xd9]);

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

test("native imports reject incomplete headers and excessive dimensions before copying", async () => {
  await withTempDirectory(async root => {
    const storage = path.join(root, "backgrounds");
    const source = path.join(root, "large.png");
    const oversized = Buffer.from(PNG_BYTES);
    oversized.writeUInt32BE(100000, 16);
    fs.writeFileSync(source, oversized);
    await assert.rejects(new BackgroundAssetStore(storage).importImage(source), /8192/);
    assert.equal(fs.existsSync(storage), false);
    assert.throws(() => assertBackgroundImageDimensions(PNG_BYTES.subarray(0, 24), "image/png"), /incomplete/);
    const area = Buffer.from(PNG_BYTES);
    area.writeUInt32BE(8000, 16); area.writeUInt32BE(8000, 20);
    assert.throws(() => assertBackgroundImageDimensions(area, "image/png"), /32 megapixels/);
  });
});

test("dimension guards handle JPEG markers and every WebP header variant", () => {
  assert.doesNotThrow(() => assertBackgroundImageDimensions(JPEG_BYTES, "image/jpeg"));
  const webp = (kind, data) => {
    const header = Buffer.alloc(20);
    header.write("RIFF"); header.writeUInt32LE(12 + data.length + data.length % 2, 4);
    header.write("WEBP", 8); header.write(kind, 12); header.writeUInt32LE(data.length, 16);
    return Buffer.concat([header, data, Buffer.alloc(data.length % 2)]);
  };
  const extended = Buffer.alloc(10); extended.writeUIntLE(639, 4, 3); extended.writeUIntLE(359, 7, 3);
  const lossless = Buffer.alloc(5); lossless[0] = 0x2f; lossless.writeUInt32LE(639 | (359 << 14), 1);
  const lossy = Buffer.from([0,0,0,0x9d,1,0x2a,0x80,2,0x68,1]);
  for (const [kind, data] of [["VP8X", extended], ["VP8L", lossless], ["VP8 ", lossy]]) {
    const bytes = webp(kind, data);
    assert.doesNotThrow(() => assertBackgroundImageDimensions(bytes, "image/webp"));
    assert.throws(() => assertBackgroundImageDimensions(bytes.subarray(0, 24), "image/webp"), /incomplete/);
  }
  extended.writeUIntLE(100000, 4, 3);
  assert.throws(() => assertBackgroundImageDimensions(webp("VP8X", extended), "image/webp"), /8192/);
});
