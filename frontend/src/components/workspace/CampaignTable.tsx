"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Empty, Meter } from "@/components/ui/Primitives";
import { StatusBadge } from "@/components/ui/Status";
import { get } from "@/lib/client";
import { formatRelative, shortId } from "@/lib/format";
import type { RunSummary } from "@/lib/types";

const POLL_MS = 3_000;

/**
 * Campaigns in a program, polled only while something is in flight.
 *
 * Polling stops the moment every campaign is terminal, so a page left open on
 * finished work makes no further requests.
 */
export function CampaignTable({
  initial,
  projectId,
}: {
  initial: RunSummary[];
  projectId: string;
}) {
  const [runs, setRuns] = useState(initial);
  const active = runs.some((r) => r.status === "queued" || r.status === "running");

  useEffect(() => {
    if (!active) return;
    const id = setInterval(async () => {
      const result = await get<{ runs: RunSummary[] }>(`/runs?project_id=${projectId}&limit=100`);
      if (result.ok) setRuns(result.data.runs);
    }, POLL_MS);
    return () => clearInterval(id);
  }, [active, projectId]);

  if (!runs.length) {
    return (
      <Empty title="No campaigns in this program yet.">
        A campaign proposes candidates, builds a capped oligomer for each, computes descriptors
        and applies the constraint set you choose.
      </Empty>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[800px] border-collapse text-xs">
        <caption className="sr-only">Campaigns in this program, newest first</caption>
        <thead>
          <tr className="border-b border-baseline text-left">
            {["Campaign", "Generator", "State", "Kept", "Discarded", "By", "Started"].map((h) => (
              <th
                key={h}
                scope="col"
                className="py-2 pr-4 text-2xs font-medium uppercase tracking-wider text-muted"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id} className="border-b border-grid align-top last:border-0">
              <td className="py-3 pr-4">
                <Link
                  href={`/campaigns/${run.id}`}
                  className="font-mono text-2xs text-accent underline-offset-2 hover:underline"
                  title={run.id}
                >
                  {shortId(run.id)}
                </Link>
                {run.label && <p className="mt-0.5 text-2xs text-muted">{run.label}</p>}
              </td>
              <td className="py-3 pr-4 font-mono text-2xs text-ink-2">{run.generator}</td>
              <td className="py-3 pr-4">
                <StatusBadge status={run.status} stage={run.stage} />
                {(run.status === "running" || run.status === "queued") && (
                  <Meter value={run.progress} className="mt-1.5 w-24" />
                )}
                {run.status === "failed" && run.error && (
                  <p className="mt-1 max-w-xs text-2xs leading-relaxed text-state-crit">
                    {run.error}
                  </p>
                )}
              </td>
              {/* Kept is passed-of-screened, not a success rate: a candidate
                  that failed a constraint is a result, not a loss. */}
              <td className="py-3 pr-4 tabular text-ink-2">
                {run.n_passed}
                <span className="text-muted"> / {run.n_candidates}</span>
              </td>
              <td className="py-3 pr-4 tabular text-ink-2">{run.n_discarded}</td>
              <td className="py-3 pr-4 text-2xs text-muted">{run.created_by_name ?? "—"}</td>
              <td className="py-3 pr-4 text-2xs text-muted">{formatRelative(run.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
