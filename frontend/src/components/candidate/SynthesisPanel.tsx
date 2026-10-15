"use client";

import { EvidenceTag, Panel } from "@/components/ui/Primitives";
import type { SynthesisRoute } from "@/lib/types";

/**
 * How this polymer would actually be made.
 *
 * The backend has computed this all along — the monomer recovered from the
 * repeat unit, the mechanism claimed for it, Ertl's synthetic accessibility
 * score on the monomer, a named precedent where one exists, and the specific
 * objections against the route. None of it was ever rendered, so a candidate
 * arrived at triage with no answer to the first question a chemist asks.
 *
 * The retrosynthesis for a polymer is the monomer and the reaction: a chain is
 * made from its monomer, so naming both *is* the route. That is why this panel
 * leads with the monomer structure rather than a tree of generic steps.
 *
 * Confidence is shown as the backend assigned it and is a claim about
 * chemistry, not about whether the polymer is any good. A refused route is
 * displayed as prominently as an established one: the reason a route cannot
 * work is the most useful thing on this panel when it applies.
 */

const CONFIDENCE_STYLES: Record<string, { className: string; label: string }> = {
  established: {
    className: "border-state-good/40 text-state-good",
    label: "established chemistry",
  },
  plausible: {
    className: "border-accent/50 text-accent",
    label: "plausible",
  },
  speculative: {
    className: "border-state-warn/40 text-state-warn",
    label: "speculative",
  },
  refused: {
    className: "border-state-crit/40 text-state-crit",
    label: "refused",
  },
};

export function SynthesisPanel({ route }: { route: SynthesisRoute }) {
  const confidence = CONFIDENCE_STYLES[route.confidence] ?? {
    className: "border-hairline text-muted",
    label: route.confidence,
  };

  return (
    <Panel
      title="How it would be made"
      subtitle="The monomer, the reaction that polymerises it, and the objections against the route. Nothing here has been run in a flask."
    >
      <div className="flex flex-wrap items-center gap-2.5">
        <span
          className={`rounded border px-2 py-0.5 text-2xs uppercase tracking-wider ${confidence.className}`}
        >
          {confidence.label}
        </span>
        <span className="rounded border border-hairline px-2 py-0.5 font-mono text-2xs text-ink-2">
          {route.mechanism}
        </span>
        {route.derivation === "graft" && (
          <span className="rounded border border-hairline px-2 py-0.5 text-2xs uppercase tracking-wider text-muted">
            grafted onto a backbone
          </span>
        )}
        <EvidenceTag tier="computed" />
      </div>

      <dl className="mt-5 space-y-4">
        <div>
          <dt className="text-2xs uppercase tracking-wider text-muted">
            Monomer it is made from
          </dt>
          <dd className="mt-1 break-all font-mono text-xs text-ink">
            {route.monomer_smiles ?? "—"}
          </dd>
          {route.monomer_class && (
            <dd className="mt-1 text-2xs text-muted">{route.monomer_class}</dd>
          )}
        </div>

        {route.retro_steps.length > 0 && (
          <div>
            <dt className="text-2xs uppercase tracking-wider text-muted">Retrosynthetic steps</dt>
            <dd className="mt-2">
              <ol className="space-y-2">
                {route.retro_steps.map((step, index) => (
                  <li key={step} className="flex gap-3 text-2xs leading-relaxed text-ink-2">
                    <span
                      aria-hidden
                      className="tabular mt-px shrink-0 font-mono text-muted"
                    >
                      {index + 1}.
                    </span>
                    <span>{step}</span>
                  </li>
                ))}
              </ol>
            </dd>
          </div>
        )}

        {route.reagents.length > 0 && (
          <div>
            <dt className="text-2xs uppercase tracking-wider text-muted">Reagents</dt>
            <dd className="mt-1.5 flex flex-wrap gap-1.5">
              {route.reagents.map((reagent) => (
                <span
                  key={reagent}
                  className="rounded border border-hairline bg-surface-2 px-2 py-0.5 font-mono text-2xs text-ink-2"
                >
                  {reagent}
                </span>
              ))}
            </dd>
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <dt className="text-2xs uppercase tracking-wider text-muted">
              Synthetic accessibility
            </dt>
            <dd className="tabular mt-1 font-mono text-xs text-ink">
              {route.sa_score === null || route.sa_score === undefined
                ? "—"
                : route.sa_score.toFixed(2)}
              <span className="ml-1.5 text-2xs text-muted">/ 10</span>
            </dd>
            <dd className="mt-1 text-2xs leading-relaxed text-muted">
              {route.sa_note || "Ertl's score, computed on the monomer rather than the polymer."}
            </dd>
          </div>
          <div>
            <dt className="text-2xs uppercase tracking-wider text-muted">Precedent</dt>
            <dd className="mt-1 text-2xs leading-relaxed text-ink-2">
              {route.precedent ?? "No specific precedent polymer is claimed for this monomer."}
            </dd>
          </div>
        </div>

        <div>
          <dt className="text-2xs uppercase tracking-wider text-muted">Reasoning</dt>
          <dd className="mt-1 text-2xs leading-relaxed text-ink-2">{route.reasoning}</dd>
        </div>
      </dl>

      {route.concerns.length > 0 && (
        <div className="mt-5 border-t border-hairline pt-4">
          <h4 className="text-2xs uppercase tracking-wider text-muted">
            What argues against this route
          </h4>
          <ul className="mt-2 space-y-1.5">
            {route.concerns.map((concern) => (
              <li key={concern} className="text-2xs leading-relaxed text-state-warn">
                ! {concern}
              </li>
            ))}
          </ul>
        </div>
      )}

      {route.selection && (
        <div className="mt-5 border-t border-hairline pt-4">
          <h4 className="text-2xs uppercase tracking-wider text-muted">
            Why this candidate was surfaced
          </h4>
          <p className="mt-2 text-2xs leading-relaxed text-muted">
            Ranked {route.selection.fit_score} on target fit, from logP/repeat{" "}
            {route.selection.logp_per_repeat ?? "—"} and aromatic fraction{" "}
            {route.selection.aromatic_fraction ?? "—"}. Ranking decides what is shown, never
            whether it passed: the constraints are applied independently.
          </p>
          {route.selection.reasons.length > 0 && (
            <ul className="mt-2 space-y-1">
              {route.selection.reasons.map((reason) => (
                <li key={reason} className="text-2xs leading-relaxed text-ink-2">
                  · {reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Panel>
  );
}
