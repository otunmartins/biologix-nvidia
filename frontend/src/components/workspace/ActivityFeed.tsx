import Link from "next/link";
import { Empty } from "@/components/ui/Primitives";
import { formatRelative } from "@/lib/format";
import type { ActivityEntry } from "@/lib/types";

/**
 * The audit trail.
 *
 * Append-only by design on the backend, and read here newest-first. Every
 * line names a person: an audit trail that says "a candidate was shortlisted"
 * without saying by whom is not one.
 */

const KIND: Record<string, { glyph: string; color: string; label: string }> = {
  "account.created": { glyph: "✦", color: "text-accent", label: "joined" },
  "program.created": { glyph: "◳", color: "text-accent", label: "program" },
  "program.archived": { glyph: "⊘", color: "text-muted", label: "archived" },
  "program.restored": { glyph: "⊚", color: "text-ink-2", label: "restored" },
  "campaign.started": { glyph: "»", color: "text-accent", label: "campaign" },
  "campaign.deleted": { glyph: "✕", color: "text-state-crit", label: "deleted" },
  "candidate.shortlisted": { glyph: "★", color: "text-state-good", label: "shortlisted" },
  "candidate.rejected": { glyph: "✕", color: "text-state-crit", label: "rejected" },
  "candidate.undecided": { glyph: "↺", color: "text-muted", label: "withdrawn" },
  "job.started": { glyph: "◬", color: "text-accent", label: "analysis" },
  "job.succeeded": { glyph: "✓", color: "text-state-good", label: "analysis" },
  "job.failed": { glyph: "✕", color: "text-state-crit", label: "analysis" },
};

const FALLBACK = { glyph: "·", color: "text-muted", label: "event" };

export function ActivityFeed({
  activity,
  showProgramLink = false,
  emptyTitle = "Nothing has happened yet.",
}: {
  activity: ActivityEntry[];
  showProgramLink?: boolean;
  emptyTitle?: string;
}) {
  if (!activity.length) {
    return (
      <Empty title={emptyTitle}>
        Every program opened, campaign started, decision recorded and analysis run appears here,
        with who did it and when.
      </Empty>
    );
  }

  return (
    <ol className="space-y-0">
      {activity.map((entry, index) => {
        const kind = KIND[entry.kind] ?? FALLBACK;
        const last = index === activity.length - 1;
        return (
          <li key={entry.id} className="relative flex gap-3 pb-4">
            {/* A spine, so a burst of related events reads as one sequence. */}
            {!last && (
              <span
                aria-hidden
                className="absolute left-[11px] top-6 bottom-0 w-px bg-grid"
              />
            )}
            <span
              aria-hidden
              className="relative grid h-[22px] w-[22px] shrink-0 place-items-center rounded-full border border-hairline bg-surface-2"
            >
              <span className={`font-mono text-2xs leading-none ${kind.color}`}>
                {kind.glyph}
              </span>
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-xs leading-relaxed text-ink-2">
                <span className="text-ink">{entry.actor_name ?? "someone"}</span>{" "}
                {entry.summary}
              </p>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-2xs text-muted">
                <span className="uppercase tracking-wider">{kind.label}</span>
                <span aria-hidden>·</span>
                <time dateTime={entry.created_at ?? undefined}>
                  {formatRelative(entry.created_at)}
                </time>
                {entry.run_id && (
                  <>
                    <span aria-hidden>·</span>
                    <Link
                      href={`/campaigns/${entry.run_id}`}
                      className="font-mono text-muted underline-offset-2 hover:text-accent hover:underline"
                    >
                      campaign {entry.run_id.slice(0, 8)}
                    </Link>
                  </>
                )}
                {showProgramLink && entry.project_id && (
                  <>
                    <span aria-hidden>·</span>
                    <Link
                      href={`/programs/${entry.project_id}`}
                      className="text-muted underline-offset-2 hover:text-accent hover:underline"
                    >
                      program
                    </Link>
                  </>
                )}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
