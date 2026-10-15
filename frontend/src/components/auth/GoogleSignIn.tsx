import { serverFetch, isFailure } from "@/lib/api";

/**
 * "Continue with Google", shown only when pressing it would work.
 *
 * The API reports whether the credentials are configured, so an unconfigured
 * deployment renders nothing at all rather than a button that bounces the
 * person back with an error. This is a plain `<a>`, not a `fetch`: the browser
 * has to *navigate* to Google, and the callback has to arrive as a navigation
 * carrying the session cookie the API sets on the way back.
 */
type Providers = {
  google: {
    configured: boolean;
    start_path: string | null;
    allowed_domains: string[];
  };
};

export async function GoogleSignIn({ next }: { next?: string }) {
  const providers = await serverFetch<Providers>("/auth/providers");
  if (isFailure(providers) || !providers.google.configured || !providers.google.start_path) {
    return null;
  }

  const href =
    providers.google.start_path + (next ? `?next=${encodeURIComponent(next)}` : "");
  const domains = providers.google.allowed_domains;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3" aria-hidden="true">
        <span className="h-px flex-1 bg-white/10" />
        <span className="text-2xs uppercase tracking-wider text-muted">or</span>
        <span className="h-px flex-1 bg-white/10" />
      </div>

      <a
        href={href}
        className="flex w-full items-center justify-center gap-2.5 rounded-lg border border-white/15 bg-white/5 px-4 py-2.5 text-sm font-medium transition hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <svg viewBox="0 0 18 18" className="h-4 w-4" aria-hidden="true">
          <path
            fill="#4285F4"
            d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92a8.78 8.78 0 0 0 2.68-6.62Z"
          />
          <path
            fill="#34A853"
            d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86a5.36 5.36 0 0 1-5.03-3.71H1.05v2.34A9 9 0 0 0 9 18Z"
          />
          <path
            fill="#FBBC05"
            d="M3.97 10.71a5.41 5.41 0 0 1 0-3.42V4.96H1.05a9 9 0 0 0 0 8.09l2.92-2.34Z"
          />
          <path
            fill="#EA4335"
            d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.59A9 9 0 0 0 1.05 4.96l2.92 2.33A5.36 5.36 0 0 1 9 3.58Z"
          />
        </svg>
        Continue with Google
      </a>

      {domains.length > 0 && (
        <p className="text-center text-2xs text-muted">
          {domains.length === 1 ? (
            <>Only {domains[0]} accounts can sign in this way.</>
          ) : (
            <>Allowed domains: {domains.join(", ")}.</>
          )}
        </p>
      )}
    </div>
  );
}
