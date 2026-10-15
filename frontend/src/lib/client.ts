"use client";

/**
 * Browser-side calls, all of them through this app's proxy at `/api/bx/...`.
 *
 * Every helper returns a discriminated result rather than throwing, and
 * judges success by HTTP status rather than by the presence of an `error`
 * key — payloads legitimately carry their own `error` field (a failed
 * campaign's reason), and conflating the two was a real bug once.
 */

export type Ok<T> = { ok: true; data: T };
export type Err = { ok: false; error: string; status: number };
export type Result<T> = Ok<T> | Err;

async function call<T>(
  path: string,
  init?: RequestInit & { body?: string },
): Promise<Result<T>> {
  try {
    const response = await fetch(`/api/bx${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        accept: "application/json",
        ...(init?.body ? { "content-type": "application/json" } : {}),
        ...init?.headers,
      },
    });
    const text = await response.text();
    const payload: unknown = text ? JSON.parse(text) : null;

    if (!response.ok) {
      const detail = (payload as { detail?: unknown; error?: unknown })?.detail;
      const proxied = (payload as { error?: unknown })?.error;
      return {
        ok: false,
        status: response.status,
        error:
          typeof detail === "string"
            ? detail
            : Array.isArray(detail) && detail.length
              ? String((detail[0] as { msg?: string })?.msg ?? `HTTP ${response.status}`)
              : typeof proxied === "string"
                ? proxied
                : `Request failed with HTTP ${response.status}`,
      };
    }
    return { ok: true, data: payload as T };
  } catch (err) {
    return {
      ok: false,
      status: 0,
      error: err instanceof Error ? err.message : "The request could not be sent.",
    };
  }
}

export function get<T>(path: string): Promise<Result<T>> {
  return call<T>(path);
}

export function post<T>(path: string, body?: unknown): Promise<Result<T>> {
  return call<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
}

export function put<T>(path: string, body?: unknown): Promise<Result<T>> {
  return call<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}) });
}

export function del<T>(path: string): Promise<Result<T>> {
  return call<T>(path, { method: "DELETE" });
}

/** Weights as the API's query format: `flexibility:0.6,polarity:0.4`. */
export function weightQuery(weights: Record<string, number>): string {
  return Object.entries(weights)
    .filter(([, value]) => value > 0)
    .map(([key, value]) => `${key}:${value}`)
    .join(",");
}
