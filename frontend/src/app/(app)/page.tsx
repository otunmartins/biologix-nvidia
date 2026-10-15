import Link from "next/link";
import { ActivityFeed } from "@/components/workspace/ActivityFeed";
import { NewProgram } from "@/components/workspace/NewProgram";
import { ProgramCards } from "@/components/workspace/ProgramCards";
import { Alert, Panel, Stat } from "@/components/ui/Primitives";
import { StatusBadge } from "@/components/ui/Status";
import { isFailure, serverFetch } from "@/lib/api";
import { formatRelative, shortId } from "@/lib/format";
import type {
  ActivityEntry,
  Program,
  RunSummary,
  Target,
  User,
  Verification,
} from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * The overview.
 *
 * Ordered by what a returning user needs first: anything still running, then
 * their programs, then what has happened since they left. The platform's own
 * state is a strip at the bottom rather than a headline — it matters, but not
 * more than the work.
 */
export default async function OverviewPage() {
  const [me, programs, runs, activity, targets, verification] = await Promise.all([
    serverFetch<{ user: User }>("/auth/me"),
    serverFetch<{ projects: Program[] }>("/projects"),
    serverFetch<{ runs: RunSummary[] }>("/runs?limit=60"),
    serverFetch<{ activity: ActivityEntry[] }>("/activity?limit=14"),
    serverFetch<{ targets: Target[] }>("/targets"),
    serverFetch<Verification>("/verification"),
  ]);

  if (isFailure(programs)) {
    return <Alert title="could not load your programs">{programs.error}</Alert>;
  }

  const user = isFailure(me) ? null : me.user;
  const allRuns = isFailure(runs) ? [] : runs.runs;
  const inFlight = allRuns.filter((r) => r.status === "queued" || r.status === "running");
  const shortlisted = programs.projects.reduce((sum, p) => sum + (p.n_shortlisted ?? 0), 0);
  const kept = programs.projects.reduce((sum, p) => sum + (p.n_passed ?? 0), 0);
  const screened = programs.projects.reduce((sum, p) => sum + (p.n_candidates ?? 0), 0);
  const liveNims = !isFailure(verification) && verification.ran ? verification.n_ok : null;

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-4 border-b border-hairline pb-6 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-2xs uppercase tracking-[0.18em] text-muted">
            {greeting()}
            {user ? `, ${user.display_name.split(" ")[0]}` : ""}
          </p>
          <h1 className="mt-2 text-xl font-semibold text-ink">Excipient design</h1>
          <p className="mt-2 max-w-2xl text-xs leading-relaxed text-muted">
            Propose polymer excipients for a biologic, screen them against editable constraints,
            and decide which ones are worth laboratory time. Every figure carries its evidence
            tier; none of them is a measurement.
          </p>
        </div>
        {!isFailure(targets) && <NewProgram targets={targets.targets} />}
      </header>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Programs"
          value={programs.projects.length}
          context="Each one fixed to a single biologic target."
        />
        <Stat
          label="Shortlisted"
          value={shortlisted}
          tone={shortlisted > 0 ? "good" : "neutral"}
          context="Candidates a person put their name to, with a reason."
        />
        <Stat
          label="Screened"
          value={screened ? `${kept} / ${screened}` : "0"}
          context="Passed every hard constraint, of all candidates screened."
        />
        <Stat
          label="NIMs verified live"
          value={liveNims === null ? "—" : liveNims}
          tone={liveNims ? "good" : "warn"}
          context={
            liveNims === null
              ? "No live check on disk: nothing has been proven to work here."
              : "Answered a real call, not just configured."
          }
        />
      </div>

      {inFlight.length > 0 && (
        <Panel
          title="In flight"
          subtitle="Campaigns running right now. They continue if you close this page; the server owns the work, not the browser."
        >
          <ul className="space-y-2">
            {inFlight.map((run) => (
              <li key={run.id}>
                <Link
                  href={`/campaigns/${run.id}`}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-hairline bg-surface-2 px-4 py-3 transition-colors hover:border-accent/40"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-xs text-ink">
                      {run.label ?? `${run.n_requested} candidates`}{" "}
                      <span className="text-muted">· {run.target_name ?? run.target_key}</span>
                    </span>
                    <span className="mt-0.5 block font-mono text-2xs text-muted">
                      {run.generator} · {shortId(run.id)}
                    </span>
                  </span>
                  <span className="flex items-center gap-4">
                    <StatusBadge status={run.status} stage={run.stage} />
                    <span className="tabular text-2xs text-muted">
                      {Math.round(run.progress * 100)}%
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <section>
        <div className="mb-3 flex items-end justify-between gap-4">
          <h2 className="text-[13px] font-semibold uppercase tracking-[0.14em] text-ink-2">
            Programs
          </h2>
          <Link href="/programs" className="text-2xs uppercase tracking-wider text-muted hover:text-accent">
            all programs
          </Link>
        </div>
        <ProgramCards programs={programs.projects.slice(0, 6)} />
      </section>

      <Panel
        title="Recent activity"
        subtitle="Who did what, and when. Append-only: nothing in the app edits or deletes a line."
        aside={
          <Link href="/activity" className="text-2xs uppercase tracking-wider text-muted hover:text-accent">
            full trail
          </Link>
        }
      >
        <ActivityFeed
          activity={isFailure(activity) ? [] : activity.activity}
          showProgramLink
          emptyTitle="Nothing has happened yet."
        />
      </Panel>

      {!isFailure(verification) && (
        <p className="pb-2 text-2xs leading-relaxed text-muted">
          {verification.ran ? (
            <>
              Live check {formatRelative(verification.checked_at)} —{" "}
              <span className="text-ink-2">
                {verification.n_ok} of {verification.n_total}
              </span>{" "}
              hosted NIMs answered a real call.{" "}
              {verification.stale && (
                <span className="text-state-warn">
                  That result is over a day old and describes the service as it was then.
                </span>
              )}
            </>
          ) : (
            <>
              No live check has been run on this machine, so no component here has been proven to
              work. <Link href="/platform" className="text-accent hover:underline">See what that means</Link>.
            </>
          )}
        </p>
      )}
    </div>
  );
}

function greeting(): string {
  const hour = new Date().getUTCHours();
  if (hour < 5) return "Late shift";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}
