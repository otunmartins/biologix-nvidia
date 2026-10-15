import { redirect } from "next/navigation";
import { Shell } from "@/components/shell/Shell";
import { isFailure, serverFetch } from "@/lib/api";
import type { Availability, User } from "@/lib/types";

/**
 * Everything behind sign-in.
 *
 * The session is validated here, by asking the API who the caller is, rather
 * than trusted from the cookie's presence — middleware only keeps obvious
 * strangers out. A rejected token sends the user to sign in instead of
 * rendering a shell full of empty panels.
 */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const me = await serverFetch<{ user: User }>("/auth/me");
  if (isFailure(me)) {
    if (me.status === 401) redirect("/login");
    // The API is unreachable, which is not the user's fault and not a reason
    // to bounce them to a sign-in screen that will also fail.
    return (
      <div className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6">
        <div className="rounded-xl border border-state-crit/40 bg-surface px-5 py-4">
          <p className="font-mono text-xs text-state-crit">backend unreachable</p>
          <p className="mt-2 text-xs leading-relaxed text-ink-2">{me.error}</p>
          <p className="mt-3 text-2xs leading-relaxed text-muted">
            Start it with <span className="font-mono text-ink-2">biologix-api</span> from the
            repository root, then reload this page.
          </p>
        </div>
      </div>
    );
  }

  // Best-effort: the shell shows the platform pill when it can, and simply
  // reports a degraded platform when it cannot.
  const availability = await serverFetch<Availability>("/availability");

  return (
    <Shell user={me.user} availability={isFailure(availability) ? undefined : availability}>
      {children}
    </Shell>
  );
}
