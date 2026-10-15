"use client";

import type { Criterion } from "@/lib/types";

/**
 * The weight controls.
 *
 * These exist because the ordering is the reader's, not the author's. Each
 * slider carries its criterion's direction and its rationale, so moving one is
 * an informed choice rather than a guess, and a criterion the cohort cannot
 * discriminate on is marked rather than left to look meaningful.
 */
export function Weights({
  criteria,
  weights,
  degenerate,
  busy = false,
  onChange,
  onReset,
}: {
  criteria: Criterion[];
  weights: Record<string, number>;
  degenerate: string[];
  busy?: boolean;
  onChange: (key: string, value: number) => void;
  onReset: () => void;
}) {
  return (
    <div className="space-y-4">
      {criteria.map((criterion) => {
        const weight = weights[criterion.key] ?? 0;
        const isDegenerate = degenerate.includes(criterion.key);
        return (
          <div key={criterion.key}>
            <div className="flex items-baseline justify-between gap-3">
              <label
                htmlFor={`weight-${criterion.key}`}
                className="text-2xs uppercase tracking-wider text-ink-2"
              >
                {criterion.label}
                <span className="ml-1.5 normal-case tracking-normal text-muted">
                  {criterion.higher_is_better ? "higher is better" : "lower is better"}
                </span>
              </label>
              <span className="tabular font-mono text-2xs text-ink">{weight.toFixed(2)}</span>
            </div>
            <input
              id={`weight-${criterion.key}`}
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={weight}
              onChange={(e) => onChange(criterion.key, Number(e.target.value))}
              className="mt-2 w-full accent-[var(--accent)]"
            />
            <p className="mt-1 text-2xs leading-relaxed text-muted">
              {criterion.rationale}
              {isDegenerate && (
                <span className="text-state-warn">
                  {" "}
                  No information in this cohort: every candidate scores the same, so this
                  criterion cannot separate them.
                </span>
              )}
            </p>
          </div>
        );
      })}

      <div className="flex items-center justify-between gap-3 border-t border-hairline pt-3">
        <p className="text-2xs leading-relaxed text-muted">
          {busy
            ? "Reordering…"
            : "Weights are scaled to sum to 1 before they are applied."}
        </p>
        <button
          type="button"
          onClick={onReset}
          className="rounded border border-hairline bg-surface-2 px-2.5 py-1 text-2xs uppercase tracking-wider text-ink-2 transition-colors hover:border-accent hover:text-ink"
        >
          defaults
        </button>
      </div>
    </div>
  );
}
