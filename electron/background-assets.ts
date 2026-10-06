import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const BACKGROUND_ASSET_SCHEME = "forgex-asset";
export const MAX_BACKGROUND_IMAGE_BYTES = 20 * 1024 * 1024;

const BACKGROUND_ASSET_ID_PATTERN = /^background-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.(png|jpg|webp)$/;

export type BackgroundImageMediaType = "image/png" | "image/jpeg" | "image/webp";

export interface DesktopBackgroundAsset {
  assetId: string;
  url: string;
  filename: string;
  mediaType: BackgroundImageMediaType;
  size: number;
}

interface StoredBackgroundAssetMetadata {
  assetId: string;
  filename: string;
  mediaType: BackgroundImageMediaType;
  size: number;
}

export interface BackgroundAssetContent {
  bytes: Buffer;
  mediaType: BackgroundImageMediaType;
}

export class BackgroundAssetError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "BackgroundAssetError";
  }
}

export class BackgroundAssetStore {
  readonly directory: string;

  constructor(directory: string) {
    this.directory = path.resolve(directory);
  }

  async importImage(sourcePath: string): Promise<DesktopBackgroundAsset> {
    if (!path.isAbsolute(sourcePath)) {
      throw new BackgroundAssetError("Background image path must be absolute.");
    }

    const sourceStat = await fs.promises.lstat(sourcePath).catch(() => null);
    if (!sourceStat?.isFile() || sourceStat.isSymbolicLink()) {
      throw new BackgroundAssetError("The selected background image is not a regular file.");
    }
    if (sourceStat.size < 1 || sourceStat.size > MAX_BACKGROUND_IMAGE_BYTES) {
      throw new BackgroundAssetError(
        `Background images must be between 1 byte and ${MAX_BACKGROUND_IMAGE_BYTES / (1024 * 1024)} MB.`,
      );
    }

    const bytes = await fs.promises.readFile(sourcePath);
    if (bytes.length !== sourceStat.size || bytes.length > MAX_BACKGROUND_IMAGE_BYTES) {
      throw new BackgroundAssetError("The selected background image changed while it was being read.");
    }
    const detected = detectBackgroundImage(bytes);
    assertExtensionMatchesMediaType(path.extname(sourcePath), detected.mediaType);
    assertBackgroundImageDimensions(bytes, detected.mediaType);

    await fs.promises.mkdir(this.directory, { recursive: true });
    const assetId = `background-${crypto.randomUUID()}.${detected.extension}`;
    const assetPath = this.resolveAssetPath(assetId);
    const metadataPath = this.resolveMetadataPath(assetId);
    const metadata: StoredBackgroundAssetMetadata = {
      assetId,
      filename: path.basename(sourcePath),
      mediaType: detected.mediaType,
      size: bytes.length,
    };

    try {
      await fs.promises.writeFile(assetPath, bytes, { flag: "wx" });
      await fs.promises.writeFile(metadataPath, JSON.stringify(metadata), { encoding: "utf-8", flag: "wx" });
    } catch (error) {
      await Promise.allSettled([
        fs.promises.unlink(assetPath),
        fs.promises.unlink(metadataPath),
      ]);
      throw error;
    }

    return this.toPublicAsset(metadata);
  }

  async resolve(assetId: string): Promise<DesktopBackgroundAsset | null> {
    const assetPath = this.resolveAssetPath(assetId);
    const assetStat = await fs.promises.lstat(assetPath).catch(() => null);
    if (!assetStat?.isFile() || assetStat.isSymbolicLink() || assetStat.size < 1 || assetStat.size > MAX_BACKGROUND_IMAGE_BYTES) {
      return null;
    }

    const metadata = await this.readMetadata(assetId);
    const mediaType = mediaTypeFromAssetId(assetId);
    return this.toPublicAsset({
      assetId,
      filename: metadata?.filename ?? assetId,
      mediaType,
      size: assetStat.size,
    });
  }

  async read(assetId: string): Promise<BackgroundAssetContent | null> {
    const assetPath = this.resolveAssetPath(assetId);
    const assetStat = await fs.promises.lstat(assetPath).catch(() => null);
    if (!assetStat?.isFile() || assetStat.isSymbolicLink() || assetStat.size < 1 || assetStat.size > MAX_BACKGROUND_IMAGE_BYTES) {
      return null;
    }

    const bytes = await fs.promises.readFile(assetPath);
    if (bytes.length !== assetStat.size) return null;
    const detected = detectBackgroundImage(bytes);
    if (detected.mediaType !== mediaTypeFromAssetId(assetId)) return null;
    assertBackgroundImageDimensions(bytes, detected.mediaType);
    return { bytes, mediaType: detected.mediaType };
  }

  async remove(assetId: string): Promise<boolean> {
    const assetPath = this.resolveAssetPath(assetId);
    const metadataPath = this.resolveMetadataPath(assetId);
    let removed = false;
    try {
      const assetStat = await fs.promises.lstat(assetPath);
      if (!assetStat.isFile() || assetStat.isSymbolicLink()) {
        throw new BackgroundAssetError("Stored background asset is not a regular file.");
      }
      await fs.promises.unlink(assetPath);
      removed = true;
    } catch (error) {
      if (!isMissingFileError(error)) throw error;
    }

    try {
      const metadataStat = await fs.promises.lstat(metadataPath);
      if (metadataStat.isFile() && !metadataStat.isSymbolicLink()) {
        await fs.promises.unlink(metadataPath);
      }
    } catch (error) {
      if (!isMissingFileError(error)) throw error;
    }
    return removed;
  }

  private async readMetadata(assetId: string): Promise<StoredBackgroundAssetMetadata | null> {
    const metadataPath = this.resolveMetadataPath(assetId);
    try {
      const metadataStat = await fs.promises.lstat(metadataPath);
      if (!metadataStat.isFile() || metadataStat.isSymbolicLink() || metadataStat.size > 16 * 1024) return null;
      const parsed: unknown = JSON.parse(await fs.promises.readFile(metadataPath, "utf-8"));
      if (!isStoredMetadata(parsed) || parsed.assetId !== assetId) return null;
      return parsed;
    } catch (error) {
      if (isMissingFileError(error) || error instanceof SyntaxError) return null;
      throw error;
    }
  }

  private resolveAssetPath(assetId: string): string {
    if (!isBackgroundAssetId(assetId)) {
      throw new BackgroundAssetError("Invalid background asset identifier.");
    }
    return resolveChildPath(this.directory, assetId);
  }

  private resolveMetadataPath(assetId: string): string {
    if (!isBackgroundAssetId(assetId)) {
      throw new BackgroundAssetError("Invalid background asset identifier.");
    }
    return resolveChildPath(this.directory, `${assetId}.json`);
  }

  private toPublicAsset(metadata: StoredBackgroundAssetMetadata): DesktopBackgroundAsset {
    return {
      assetId: metadata.assetId,
      url: createBackgroundAssetUrl(metadata.assetId),
      filename: metadata.filename,
      mediaType: metadata.mediaType,
      size: metadata.size,
    };
  }
}

export function isBackgroundAssetId(value: unknown): value is string {
  return typeof value === "string" && BACKGROUND_ASSET_ID_PATTERN.test(value);
}

export function createBackgroundAssetUrl(assetId: string): string {
  if (!isBackgroundAssetId(assetId)) {
    throw new BackgroundAssetError("Invalid background asset identifier.");
  }
  return `${BACKGROUND_ASSET_SCHEME}://background/${assetId}`;
}

export function parseBackgroundAssetUrl(value: string): string | null {
  try {
    const url = new URL(value);
    if (
      url.protocol !== `${BACKGROUND_ASSET_SCHEME}:`
      || url.hostname !== "background"
      || url.username
      || url.password
      || url.port
      || url.search
      || url.hash
    ) {
      return null;
    }
    const assetId = decodeURIComponent(url.pathname.replace(/^\//, ""));
    return isBackgroundAssetId(assetId) ? assetId : null;
  } catch {
    return null;
  }
}

export function detectBackgroundImage(bytes: Uint8Array): {
  mediaType: BackgroundImageMediaType;
  extension: "png" | "jpg" | "webp";
} {
  if (
    bytes.length >= 8
    && bytes[0] === 0x89
    && bytes[1] === 0x50
    && bytes[2] === 0x4e
    && bytes[3] === 0x47
    && bytes[4] === 0x0d
    && bytes[5] === 0x0a
    && bytes[6] === 0x1a
    && bytes[7] === 0x0a
  ) {
    return { mediaType: "image/png", extension: "png" };
  }
  if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) {
    return { mediaType: "image/jpeg", extension: "jpg" };
  }
  if (
    bytes.length >= 12
    && ascii(bytes, 0, 4) === "RIFF"
    && ascii(bytes, 8, 12) === "WEBP"
  ) {
    return { mediaType: "image/webp", extension: "webp" };
  }
  throw new BackgroundAssetError("Only valid PNG, JPEG, and WebP background images are supported.");
}

/** Inspect bounded headers before a renderer allocates decoded image pixels. */
export function assertBackgroundImageDimensions(bytes: Buffer, mediaType: BackgroundImageMediaType): void {
  let dimensions: [number, number] | null = null;
  if (mediaType === "image/png" && bytes.length >= 33 && bytes.readUInt32BE(8) === 13 && ascii(bytes, 12, 16) === "IHDR") {
    dimensions = [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];
  } else if (mediaType === "image/jpeg") {
    let offset = 2;
    while (offset + 4 <= bytes.length) {
      if (bytes[offset++] !== 0xff) break;
      while (bytes[offset] === 0xff) offset++;
      const marker = bytes[offset++];
      if (marker === 0xda || marker === 0xd9 || marker === undefined) break;
      if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd8)) continue;
      if (offset + 2 > bytes.length) break;
      const length = bytes.readUInt16BE(offset);
      if (length < 2 || offset + length > bytes.length) break;
      if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker) && length >= 8) {
        dimensions = [bytes.readUInt16BE(offset + 5), bytes.readUInt16BE(offset + 3)];
        break;
      }
      offset += length;
    }
  } else if (mediaType === "image/webp" && bytes.length >= 20) {
    const end = bytes.readUInt32LE(4) + 8;
    if (end <= bytes.length && end >= 20) {
      for (let offset = 12; offset + 8 <= end;) {
        const kind = ascii(bytes, offset, offset + 4);
        const size = bytes.readUInt32LE(offset + 4);
        const start = offset + 8;
        if (start + size > end) break;
        if (kind === "VP8X" && size >= 10) {
          dimensions = [bytes.readUIntLE(start + 4, 3) + 1, bytes.readUIntLE(start + 7, 3) + 1];
          break;
        }
        if (kind === "VP8L" && size >= 5 && bytes[start] === 0x2f) {
          const bits = bytes.readUInt32LE(start + 1);
          dimensions = [(bits & 0x3fff) + 1, ((bits >>> 14) & 0x3fff) + 1];
          break;
        }
        if (kind === "VP8 " && size >= 10 && bytes[start] % 2 === 0 && bytes.subarray(start + 3, start + 6).equals(Buffer.from([0x9d, 0x01, 0x2a]))) {
          dimensions = [bytes.readUInt16LE(start + 6) & 0x3fff, bytes.readUInt16LE(start + 8) & 0x3fff];
          break;
        }
        offset = start + size + (size % 2);
      }
    }
  }
  if (!dimensions) throw new BackgroundAssetError("This image has an invalid or incomplete dimension header.");
  const [width, height] = dimensions;
  if (!width || !height || width > 8192 || height > 8192 || width * height > 32_000_000) {
    throw new BackgroundAssetError("Choose an image up to 8192 pixels per side and 32 megapixels.");
  }
}

function assertExtensionMatchesMediaType(extension: string, mediaType: BackgroundImageMediaType): void {
  const normalized = extension.toLowerCase();
  const matches = (
    (mediaType === "image/png" && normalized === ".png")
    || (mediaType === "image/jpeg" && (normalized === ".jpg" || normalized === ".jpeg"))
    || (mediaType === "image/webp" && normalized === ".webp")
  );
  if (!matches) {
    throw new BackgroundAssetError("The selected image extension does not match its file contents.");
  }
}

function mediaTypeFromAssetId(assetId: string): BackgroundImageMediaType {
  if (!isBackgroundAssetId(assetId)) {
    throw new BackgroundAssetError("Invalid background asset identifier.");
  }
  if (assetId.endsWith(".png")) return "image/png";
  if (assetId.endsWith(".jpg")) return "image/jpeg";
  return "image/webp";
}

function resolveChildPath(directory: string, filename: string): string {
  const resolvedDirectory = path.resolve(directory);
  const resolvedPath = path.resolve(resolvedDirectory, filename);
  if (path.dirname(resolvedPath) !== resolvedDirectory) {
    throw new BackgroundAssetError("Background asset path escaped its storage directory.");
  }
  return resolvedPath;
}

function ascii(bytes: Uint8Array, start: number, end: number): string {
  return String.fromCharCode(...bytes.slice(start, end));
}

function isMissingFileError(error: unknown): boolean {
  return error instanceof Error && "code" in error && error.code === "ENOENT";
}

function isStoredMetadata(value: unknown): value is StoredBackgroundAssetMetadata {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<StoredBackgroundAssetMetadata>;
  return (
    isBackgroundAssetId(candidate.assetId)
    && typeof candidate.filename === "string"
    && path.basename(candidate.filename) === candidate.filename
    && candidate.filename.length <= 255
    && (candidate.mediaType === "image/png" || candidate.mediaType === "image/jpeg" || candidate.mediaType === "image/webp")
    && typeof candidate.size === "number"
    && candidate.size > 0
    && candidate.size <= MAX_BACKGROUND_IMAGE_BYTES
  );
}
