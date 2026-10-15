import { fetchStatus } from "@/lib/server";

/** The polling endpoint the console's client half calls. */
export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  const payload = await fetchStatus();
  return Response.json(payload, {
    status: payload.ok ? 200 : 503,
    headers: { "cache-control": "no-store" },
  });
}
