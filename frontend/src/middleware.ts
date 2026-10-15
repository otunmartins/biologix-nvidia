import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Keeps signed-out visitors off the app, and signed-in ones off the sign-in
 * screen.
 *
 * This checks only whether a session cookie is *present*. It is a routing
 * convenience, never a security boundary: the API validates every token on
 * every request, so a forged cookie gets a 401 from the backend and the shell
 * turns that into a redirect. Doing real validation here would mean a network
 * call on every navigation.
 */
const PUBLIC_PATHS = ["/login", "/register"];

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const signedIn = Boolean(request.cookies.get("biologix_session")?.value);
  const isPublic = PUBLIC_PATHS.some((path) => pathname.startsWith(path));

  if (!signedIn && !isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    // So the user lands where they were going, not on a generic home page.
    url.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(url);
  }

  if (signedIn && isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = "/";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // Everything except the proxy, Next's own assets and the favicon.
  matcher: ["/((?!api/|_next/|favicon).*)"],
};
