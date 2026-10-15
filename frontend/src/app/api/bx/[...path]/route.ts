import { cookies } from "next/headers";
import { API, SESSION_COOKIE } from "@/lib/api";

/**
 * One proxy for the whole Python API.
 *
 * Every browser request goes through here rather than straight to the
 * backend, which buys four things:
 *
 * * the backend's address never reaches client code, and needs no CORS entry
 *   for this app's port;
 * * the session cookie is HttpOnly in both directions — forwarded up on each
 *   request, and `Set-Cookie` from a sign-in handed back to the browser
 *   unchanged, so no JavaScript ever touches the token;
 * * a backend that is simply not running produces a readable 503 instead of
 *   an opaque network error in the console;
 * * only the methods this app actually uses are exposed.
 */
export const dynamic = "force-dynamic";

const PASS_THROUGH_REQUEST = ["content-type", "accept"];

async function proxy(request: Request, path: string[]): Promise<Response> {
  const target = new URL(request.url);
  const upstream = `${API}/${path.join("/")}${target.search}`;

  const headers = new Headers();
  for (const name of PASS_THROUGH_REQUEST) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  // The token travels as a cookie and only as a cookie.
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (token) headers.set("cookie", `${SESSION_COOKIE}=${token}`);
  // So the backend can record something useful about the session's client.
  const agent = request.headers.get("user-agent");
  if (agent) headers.set("user-agent", agent);

  const body =
    request.method === "GET" || request.method === "DELETE"
      ? undefined
      : await request.text();

  let response: Response;
  try {
    response = await fetch(upstream, {
      method: request.method,
      headers,
      body: body || undefined,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(120_000),
    });
  } catch (err) {
    return Response.json(
      {
        failed: true,
        error:
          err instanceof Error
            ? `${err.message}. Is the Biologix API running at ${API}?`
            : `Could not reach ${API}`,
      },
      { status: 503 },
    );
  }

  const out = new Headers({ "cache-control": "no-store" });
  const contentType = response.headers.get("content-type");
  if (contentType) out.set("content-type", contentType);
  // `redirect: "manual"` above means a 3xx arrives here intact rather than
  // being followed server-side. The browser can only act on it if Location
  // comes with it, which is what makes the OAuth callback work: Google sends
  // the browser to this proxy path, the API answers 303 + Set-Cookie, and
  // both have to reach the browser together.
  const location = response.headers.get("location");
  if (location) out.set("location", location);
  // Sign-in and sign-out set and clear the session; both must reach the
  // browser exactly as the API wrote them.
  for (const [name, value] of response.headers.entries()) {
    if (name.toLowerCase() === "set-cookie") out.append("set-cookie", value);
  }

  return new Response(response.body, { status: response.status, headers: out });
}

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: Request, { params }: Context) {
  return proxy(request, (await params).path);
}

export async function POST(request: Request, { params }: Context) {
  return proxy(request, (await params).path);
}

export async function PUT(request: Request, { params }: Context) {
  return proxy(request, (await params).path);
}

export async function DELETE(request: Request, { params }: Context) {
  return proxy(request, (await params).path);
}
