import { createHmac } from "node:crypto";

export const runtime = "nodejs";

export async function GET(request: Request): Promise<Response> {
  const key = process.env.FORGEX_FRONTEND_IDENTITY_KEY;
  const challenge = request.headers.get("x-forgex-challenge");
  if (!key || !challenge || !/^[A-Za-z0-9_-]{32}$/.test(challenge)) {
    return new Response(null, { status: 404, headers: { "Cache-Control": "no-store" } });
  }
  const proof = createHmac("sha256", key).update(challenge).digest("hex");
  return new Response(null, {
    status: 204,
    headers: { "Cache-Control": "no-store", "X-ForgeX-Proof": proof },
  });
}
