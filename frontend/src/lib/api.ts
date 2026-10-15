import { cookies } from "next/headers";

/**
 * Server-side reads of the Python API.
 *
 * Used by server components so a page's first paint already carries real
 * data — which also means `curl` of any page shows whether the backend is
 * answering. The session cookie is read from the incoming request and
 * forwarded; it is never placed in a URL or a body.
 */
export const API = process.env.BIOLOGIX_API_URL ?? "http://127.0.0.1:8080";

export const SESSION_COOKIE = "biologix_session";

/**
 * A reachability or refusal failure, tagged explicitly.
 *
 * The tag matters: a campaign payload carries its own `error` field (the
 * reason *that campaign* failed), so testing for the presence of `error`
 * would read every successfully fetched failed campaign as a dead backend.
 */
export type Failure = { failed: true; error: string; status: number };

export function isFailure<T>(value: T | Failure): value is Failure {
  return typeof value === "object" && value !== null && (value as Failure).failed === true;
}

export async function serverFetch<T>(
  path: string,
  init?: RequestInit & { body?: string },
): Promise<T | Failure> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  try {
    const response = await fetch(`${API}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        accept: "application/json",
        ...(init?.body ? { "content-type": "application/json" } : {}),
        ...(token ? { cookie: `${SESSION_COOKIE}=${token}` } : {}),
        ...init?.headers,
      },
      signal: AbortSignal.timeout(60_000),
    });

    const text = await response.text();
    const payload: unknown = text ? JSON.parse(text) : null;

    if (!response.ok) {
      const detail = (payload as { detail?: unknown })?.detail;
      return {
        failed: true,
        status: response.status,
        error:
          typeof detail === "string"
            ? detail
            : Array.isArray(detail) && detail.length
              ? String((detail[0] as { msg?: string })?.msg ?? `HTTP ${response.status}`)
              : `${path} returned HTTP ${response.status}`,
      };
    }
    return payload as T;
  } catch (err) {
    return {
      failed: true,
      status: 503,
      error:
        err instanceof Error
          ? `${err.message}. Is the Biologix API running at ${API}?`
          : `Could not reach ${API}`,
    };
  }
}

/** True when the caller has no valid session, which the shell turns into a
 *  redirect to the sign-in screen rather than an error panel. */
export function isUnauthenticated(value: unknown): boolean {
  return isFailure(value) && value.status === 401;
}
