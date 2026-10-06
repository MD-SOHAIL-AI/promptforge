import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
const sharp = createRequire(new URL("../frontend/package.json", import.meta.url))("sharp");

const svg = await readFile(new URL("../assets/forgex.svg", import.meta.url));
const sizes = [32, 48, 64, 128, 256];
const images = await Promise.all(sizes.map(size => sharp(svg).resize(size, size).png().toBuffer()));
const header = Buffer.alloc(6 + 16 * images.length);
header.writeUInt16LE(1, 2);
header.writeUInt16LE(images.length, 4);
let offset = header.length;
for (let index = 0; index < images.length; index++) {
  const entry = 6 + 16 * index;
  header[entry] = sizes[index] % 256;
  header[entry + 1] = sizes[index] % 256;
  header.writeUInt16LE(1, entry + 4);
  header.writeUInt16LE(32, entry + 6);
  header.writeUInt32LE(images[index].length, entry + 8);
  header.writeUInt32LE(offset, entry + 12);
  offset += images[index].length;
}
await writeFile(new URL("../assets/forgex.ico", import.meta.url), Buffer.concat([header, ...images]));
