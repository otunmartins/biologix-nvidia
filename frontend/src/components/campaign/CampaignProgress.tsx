"use client";

import { motion } from "framer-motion";
import type { RunSummary } from "@/lib/types";

/**
 * The live pipeline of a campaign in flight.
 *
 * The stages are the real ones the backend reports — `generating`,
 * `screening`, `diversity`, `complete` — not an invented animation. A stage
 * only lights up once the server has said it is there, and the connecting
 * edge only flows into a stage that is actually current, so nothing on this
 * diagram implies work that is not happening.
 */

const STAGES = [
  {
    key: "generating",
    label: "Generate",
    detail: "Propose molecules, keep the ones that are real repeat units",
  },
  {
    key: "screening",
    label: "Screen",
    detail: "Build a capped oligomer, compute descriptors, apply constraints",
  },
  {
    key: "diversity",
    label: "Diversity",
    detail: "Fingerprint the cohort and measure how far apart its members are",
  },
  { key: "complete", label: "Complete", detail: "Stored with its manifest" },
];

function stageIndex(stage: string | null, status: RunSummary["status"]): number {
  if (status === "succeeded") return STAGES.length - 1;
  if (!stage) return -1;
  const index = STAGES.findIndex((s) => s.key === stage);
  if (index >= 0) return index;
  return stage === "queued" ? -1 : 0;
}

export function CampaignProgress({ run }: { run: RunSummary }) {
  const current = stageIndex(run.stage, run.status);
  const failed = run.status === "failed";

  return (
    <div>
      <ol className="flex flex-col gap-3 sm:flex-row sm:items-stretch">
        {STAGES.map((stage, index) => {
          const done = index < current || run.status === "succeeded";
          const active = index === current && run.status === "running";
          const reached = done || active;
          return (
            <li key={stage.key} className="flex flex-1 items-stretch gap-3">
              <div
                className={`relative flex-1 rounded-xl border px-3.5 py-3 transition-colors ${
                  active
                    ? "border-accent/60 bg-accent-soft"
                    : done
                      ? "border-hairline bg-surface-2"
                      : "border-hairline/60 bg-surface-2/40"
                }`}
              >
                {active && (
                  <span
                    aria-hidden
                    className="shimmer pointer-events-none absolute inset-0 rounded-xl"
                  />
                )}
                <div className="relative flex items-center gap-2">
                  <span
                    aria-hidden
                    className="grid h-4 w-4 place-items-center rounded-full border text-[9px] font-mono leading-none"
                    style={{
                      borderColor: reached ? "var(--accent)" : "var(--border)",
                      color: reached ? "var(--accent)" : "var(--text-muted)",
                      background: done ? "var(--accent)" : "transparent",
                    }}
                  >
                    {done ? (
                      <span style={{ color: "var(--surface-1)" }}>✓</span>
                    ) : (
                      index + 1
                    )}
                  </span>
                  <span
                    className={`text-2xs uppercase tracking-wider ${
                      reached ? "text-ink" : "text-muted"
                    }`}
                  >
                    {stage.label}
                  </span>
                </div>
                <p className="relative mt-1.5 text-2xs leading-relaxed text-muted">
                  {stage.detail}
                </p>
              </div>

              {index < STAGES.length - 1 && (
                <svg
                  aria-hidden
                  className="hidden w-5 shrink-0 self-center sm:block"
                  height="10"
                  viewBox="0 0 20 10"
                >
                  <path
                    d="M0 5h20"
                    stroke={index < current ? "var(--accent)" : "var(--gridline)"}
                    strokeWidth="1.4"
                    strokeDasharray={index === current - 1 || index === current ? "4 4" : undefined}
                    className={index === current && run.status === "running" ? "edge-flow" : ""}
                  />
                </svg>
              )}
            </li>
          );
        })}
      </ol>

      <div className="mt-4 flex items-center gap-3">
        <div className="h-1 flex-1 overflow-hidden rounded-full bg-grid">
          <motion.div
            className="h-full rounded-full"
            style={{ background: failed ? "var(--state-crit)" : "var(--accent)" }}
            animate={{ width: `${Math.max(3, run.progress * 100)}%` }}
            transition={{ duration: 0.6, ease: "easeOut" }}
          />
        </div>
        <span className="tabular shrink-0 font-mono text-2xs text-muted">
          {Math.round(run.progress * 100)}%
        </span>
      </div>
    </div>
  );
}
