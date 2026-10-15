"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";
import { Button, inputClass } from "@/components/ui/Primitives";
import { VerdictBadge } from "@/components/ui/Status";
import { del, put } from "@/lib/client";
import type { DecisionRecord, Verdict } from "@/lib/types";

/**
 * Shortlist or reject one candidate.
 *
 * The reason box is not optional and not skippable, because the reason is the
 * only part of a triage decision that is not already recoverable from the
 * numbers. The weights and order score the decider was looking at are sent
 * along with it, so the call can be read later in the ordering that produced
 * it rather than in whatever ordering a reader has today.
 */
export function DecisionControl({
  runId,
  candidateId,
  decision,
  weights,
  orderScore,
  onChange,
  compact = false,
}: {
  runId: string;
  candidateId: string;
  decision: DecisionRecord | null;
  weights: Record<string, number>;
  orderScore: number | null;
  onChange: (decision: DecisionRecord | null) => void;
  compact?: boolean;
}) {
  const [open, setOpen] = useState<Verdict | null>(null);
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(verdict: Verdict) {
    setBusy(true);
    setError(null);
    const result = await put<DecisionRecord>(
      `/runs/${runId}/candidates/${candidateId}/decision`,
      {
        verdict,
        rationale,
        weights: Object.fromEntries(Object.entries(weights).filter(([, v]) => v > 0)),
        order_score: orderScore,
      },
    );
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    onChange(result.data);
    setOpen(null);
    setRationale("");
  }

  async function withdraw() {
    setBusy(true);
    const result = await del(`/runs/${runId}/candidates/${candidateId}/decision`);
    setBusy(false);
    if (result.ok) onChange(null);
  }

  if (decision && !open) {
    return (
      <div className={compact ? "" : "space-y-2"}>
        <div className="flex flex-wrap items-center gap-2">
          <VerdictBadge verdict={decision.verdict} />
          <button
            type="button"
            onClick={() => {
              setOpen(decision.verdict);
              setRationale(decision.rationale);
            }}
            className="text-2xs uppercase tracking-wider text-muted hover:text-accent"
          >
            revise
          </button>
          <button
            type="button"
            onClick={withdraw}
            disabled={busy}
            className="text-2xs uppercase tracking-wider text-muted hover:text-state-crit disabled:opacity-50"
          >
            withdraw
          </button>
        </div>
        {!compact && (
          <p className="max-w-prose text-2xs leading-relaxed text-muted">
            “{decision.rationale}”
          </p>
        )}
      </div>
    );
  }

  return (
    <div>
      <AnimatePresence initial={false} mode="wait">
        {open ? (
          <motion.div
            key="form"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden"
          >
            <label
              htmlFor={`rationale-${candidateId}`}
              className="block text-2xs uppercase tracking-wider text-muted"
            >
              Why {open === "shortlisted" ? "shortlist" : "reject"} this one?
            </label>
            <textarea
              id={`rationale-${candidateId}`}
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              rows={compact ? 2 : 3}
              maxLength={2000}
              autoFocus
              placeholder={
                open === "shortlisted"
                  ? "Most flexible in the cohort, no residual-monomer flags, TPSA mid-band."
                  : "Rotatable-bond count too low to act as a flexible spacer."
              }
              className={`${inputClass} mt-1.5 resize-y`}
            />
            {error && <p className="mt-1.5 text-2xs text-state-crit">{error}</p>}
            <div className="mt-2 flex items-center gap-2">
              <Button
                onClick={() => submit(open)}
                disabled={busy || !rationale.trim()}
                variant={open === "shortlisted" ? "primary" : "danger"}
              >
                {busy ? "saving…" : open === "shortlisted" ? "shortlist" : "reject"}
              </Button>
              <button
                type="button"
                onClick={() => {
                  setOpen(null);
                  setError(null);
                }}
                className="text-2xs uppercase tracking-wider text-muted hover:text-ink"
              >
                cancel
              </button>
              <span className="text-2xs text-muted">
                A reason is required — it is the part the numbers cannot give back.
              </span>
            </div>
          </motion.div>
        ) : (
          <motion.div
            key="buttons"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="flex items-center gap-2"
          >
            <button
              type="button"
              onClick={() => setOpen("shortlisted")}
              className="rounded border border-hairline px-2 py-0.5 text-2xs uppercase tracking-wider text-muted transition-colors hover:border-state-good/50 hover:text-state-good"
            >
              ★ shortlist
            </button>
            <button
              type="button"
              onClick={() => setOpen("rejected")}
              className="rounded border border-hairline px-2 py-0.5 text-2xs uppercase tracking-wider text-muted transition-colors hover:border-state-crit/50 hover:text-state-crit"
            >
              ✕ reject
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
