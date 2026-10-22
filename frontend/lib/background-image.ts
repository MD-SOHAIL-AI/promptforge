import type { DesktopBackgroundAsset } from "@/types/electron";

const resolved = new Map<string, Promise<DesktopBackgroundAsset | null>>();

export async function validateBackgroundImage(url: string): Promise<void> {
  const image = new Image();
  image.src = url;
  try { await image.decode(); }
  catch { throw new Error("This image could not be decoded. Choose a valid PNG, JPEG, or WebP image."); }
  if (!image.naturalWidth || !image.naturalHeight || image.naturalWidth > 8192 || image.naturalHeight > 8192 || image.naturalWidth * image.naturalHeight > 32_000_000) {
    throw new Error("Choose an image up to 8192 pixels per side and 32 megapixels.");
  }
}

export function resolveBackgroundImage(id: string) {
  if (!resolved.has(id)) {
    const request = (window.forgexDesktop?.resolveBackgroundImage?.(id) ?? Promise.resolve(null)).then(async asset => {
      if (asset) await validateBackgroundImage(asset.url);
      return asset;
    }).catch(error => { resolved.delete(id); throw error; });
    resolved.set(id, request);
    if (resolved.size > 8) resolved.delete(resolved.keys().next().value!);
  }
  return resolved.get(id)!;
}

export function invalidateBackgroundImage(id: string) { resolved.delete(id); }
