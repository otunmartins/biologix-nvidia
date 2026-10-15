import { isFailure, serverFetch } from "@/lib/api";
import type { StatusPayload } from "@/lib/types";

/**
 * The platform console's three reads, in one payload.
 *
 * Shared by the page's first paint and by the polling route handler, so both
 * see exactly the same shape. It goes through `serverFetch`, which forwards
 * the session cookie — these endpoints require a session, like everything
 * else except liveness and sign-in.
 *
 * `/availability` makes real network calls for self-hosted endpoints (the
 * container readiness probe), so this is not free and is never cached: a
 * developer refreshing it wants the current answer, not the answer from when
 * the process booted.
 */
export async function fetchStatus(): Promise<StatusPayload> {
  const fetchedAt = new Date().toISOString();
  const [availability, verification, config] = await Promise.all([
    serverFetch<NonNullable<StatusPayload["availability"]>>("/availability"),
    serverFetch<NonNullable<StatusPayload["verification"]>>("/verification"),
    serverFetch<NonNullable<StatusPayload["config"]>>("/config"),
  ]);

  const failed = [availability, verification, config].find(isFailure);
  if (failed) {
    return { ok: false, fetchedAt, error: failed.error };
  }

  return {
    ok: true,
    fetchedAt,
    availability: availability as NonNullable<StatusPayload["availability"]>,
    verification: verification as NonNullable<StatusPayload["verification"]>,
    config: config as NonNullable<StatusPayload["config"]>,
  };
}
