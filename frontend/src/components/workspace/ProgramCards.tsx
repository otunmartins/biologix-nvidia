"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import { Empty, Meter } from "@/components/ui/Primitives";
import { formatRelative } from "@/lib/format";
import type { Program } from "@/lib/types";

/**
 * Program cards.
 *
 * A card leads with the shortlist count, because that is the program's actual
 * output — the candidates a person put their name to. Candidate totals come
 * second: a campaign that generated 200 molecules nobody chose is not
 * progress.
 */
export function ProgramCards({ programs }: { programs: Program[] }) {
  if (!programs.length) {
    return (
      <Empty title="No programs yet.">
        A program fixes one biologic target and collects every campaign against it. Open one to
        start — the baseline generator needs no GPU and no credentials.
      </Empty>
    );
  }

  return (
    <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {programs.map((program, index) => (
        <motion.li
          key={program.id}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: Math.min(index * 0.04, 0.28), duration: 0.28 }}
        >
          <Link
            href={`/programs/${program.id}`}
            className="group flex h-full flex-col rounded-xl border border-hairline bg-surface p-5 transition-colors hover:border-accent/40"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="truncate text-sm font-medium text-ink group-hover:text-ink">
                  {program.name}
                </h3>
                <p className="mt-1 truncate text-2xs text-muted">
                  {program.target_name ?? program.target_key}
                </p>
              </div>
              {(program.n_running ?? 0) > 0 && (
                <span className="inline-flex shrink-0 items-center gap-1.5 rounded border border-accent/40 bg-accent-soft px-2 py-0.5">
                  <span
                    aria-hidden
                    className="breathe inline-block h-1.5 w-1.5 rounded-full"
                    style={{ background: "var(--accent)" }}
                  />
                  <span className="text-2xs uppercase tracking-wider text-ink-2">
                    {program.n_running} running
                  </span>
                </span>
              )}
            </div>

            {program.description && (
              <p className="mt-3 line-clamp-2 text-2xs leading-relaxed text-muted">
                {program.description}
              </p>
            )}

            <div className="mt-auto pt-5">
              <div className="flex items-end justify-between gap-4">
                <div>
                  <p className="tabular text-2xl font-semibold leading-none text-ink">
                    {program.n_shortlisted ?? 0}
                  </p>
                  <p className="mt-1 text-2xs uppercase tracking-wider text-muted">shortlisted</p>
                </div>
                <div className="text-right">
                  <p className="tabular text-xs text-ink-2">
                    {program.n_campaigns ?? 0} campaign
                    {(program.n_campaigns ?? 0) === 1 ? "" : "s"}
                  </p>
                  <p className="tabular mt-0.5 text-2xs text-muted">
                    {program.n_passed ?? 0} of {program.n_candidates ?? 0} kept
                  </p>
                </div>
              </div>
              {/* Share of screened candidates that cleared every hard
                  constraint. Not a success rate: a failure is a result. */}
              <Meter
                value={program.n_passed ?? 0}
                max={Math.max(1, program.n_candidates ?? 0)}
                className="mt-3"
                color="var(--seq-500)"
                label={`${program.n_passed ?? 0} of ${program.n_candidates ?? 0} candidates passed every hard constraint`}
              />
              <p className="mt-3 text-2xs text-muted">
                {program.last_activity ? `active ${formatRelative(program.last_activity)}` : "no activity yet"}
              </p>
            </div>
          </Link>
        </motion.li>
      ))}
    </ul>
  );
}
