"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Field, inputClass } from "@/components/ui/Primitives";
import { post } from "@/lib/client";
import type { Options } from "@/lib/types";

/**
 * Launch a design campaign.
 *
 * The generator cards are the honest part. Each one says what it is, what it
 * costs, and — if its adapter cannot reach anything right now — exactly why,
 * taken from the platform's own availability report. A generator that is
 * unavailable is still selectable: the campaign then fails with the adapter's
 * reason, which leaves a record, rather than being silently swapped for the
 * baseline.
 */

const GENERATOR_COPY: Record<string, { title: string; engine: string; blurb: string }> = {
  baseline: {
    title: "Baseline",
    engine: "Deterministic · CPU",
    blurb:
      "Hand-curated seeds plus grafted pendant groups, reproducible from the seed. The comparison standard: a learned generator that cannot beat it on validity, uniqueness and diversity has not earned its dependency.",
  },
  genmol: {
    title: "GenMol",
    engine: "BioNeMo GenMol 2.0.0 · hosted NIM",
    blurb:
      "Fragment-based generation from polymer-shaped seed templates, then filtered down to molecules that carry a polymerisable handle.",
  },
  molmim: {
    title: "MolMIM",
    engine: "BioNeMo MolMIM 1.0.0 · hosted NIM",
    blurb:
      "Latent sampling around known excipient chemistry. Its built-in objectives (QED, plogP) score oral drug-likeness, which selects against what an excipient needs, so similarity-constrained sampling is the default.",
  },
};

export function LaunchCampaign({
  options,
  projectId,
  targetName,
  unavailable,
}: {
  options: Options;
  projectId: string;
  targetName: string;
  /** Generator name → why its adapter cannot run, from /availability. */
  unavailable: Record<string, string | null>;
}) {
  const router = useRouter();
  const [generator, setGenerator] = useState(options.generators[0] ?? "baseline");
  const [n, setN] = useState(16);
  const [constraintSet, setConstraintSet] = useState(
    options.constraint_sets[0]?.name ?? "",
  );
  const [label, setLabel] = useState("");
  const [seed, setSeed] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const chosenSet = options.constraint_sets.find((c) => c.name === constraintSet);
  const blocked = unavailable[generator];

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const result = await post<{ run_id: string }>("/runs", {
      project_id: projectId,
      generator,
      n,
      constraint_set: constraintSet || null,
      seed: seed.trim() === "" ? null : Number(seed),
      label: label.trim() || null,
    });
    if (!result.ok) {
      setError(result.error);
      setBusy(false);
      return;
    }
    router.push(`/campaigns/${result.data.run_id}`);
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      <fieldset>
        <legend className="text-2xs uppercase tracking-wider text-muted">Generator</legend>
        <div className="mt-2.5 grid gap-3 lg:grid-cols-3">
          {options.generators.map((name) => {
            const copy = GENERATOR_COPY[name] ?? {
              title: name,
              engine: "",
              blurb: "",
            };
            const selected = generator === name;
            const reason = unavailable[name];
            return (
              <motion.label
                key={name}
                whileHover={{ y: -2 }}
                transition={{ duration: 0.16 }}
                className={`relative flex cursor-pointer flex-col rounded-xl border p-4 transition-colors ${
                  selected
                    ? "border-accent/60 bg-accent-soft"
                    : "border-hairline bg-surface-2 hover:border-hairline-strong"
                }`}
              >
                <input
                  type="radio"
                  name="generator"
                  value={name}
                  checked={selected}
                  onChange={() => setGenerator(name)}
                  className="absolute right-4 top-4 accent-[var(--accent)]"
                />
                <span className="pr-6 text-sm text-ink">{copy.title}</span>
                <span className="mt-0.5 font-mono text-2xs text-accent">{copy.engine}</span>
                <span className="mt-2.5 text-2xs leading-relaxed text-muted">{copy.blurb}</span>
                {reason ? (
                  <span className="mt-3 block border-t border-hairline pt-2.5 text-2xs leading-relaxed text-state-warn">
                    Cannot run now: {reason}
                  </span>
                ) : (
                  <span className="mt-3 block border-t border-hairline pt-2.5 text-2xs text-state-good">
                    Reachable from this machine
                  </span>
                )}
              </motion.label>
            );
          })}
        </div>
      </fieldset>

      <AnimatePresence>
        {blocked && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <Alert kind="warn" title="this campaign will fail">
              {blocked} The campaign is still recorded, with that reason attached — it is never
              quietly run with the baseline instead.
            </Alert>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Field
          label="Candidates"
          htmlFor="campaign-n"
          hint={`Proposals to accept, 1–${options.max_candidates}.`}
        >
          <input
            id="campaign-n"
            type="number"
            min={1}
            max={options.max_candidates}
            value={n}
            onChange={(e) => setN(Number(e.target.value))}
            className={`${inputClass} tabular`}
          />
        </Field>

        <Field
          label="Constraints"
          htmlFor="campaign-constraints"
          hint={chosenSet?.description ?? "Editable heuristics with stated rationales."}
        >
          <select
            id="campaign-constraints"
            value={constraintSet}
            onChange={(e) => setConstraintSet(e.target.value)}
            className={inputClass}
          >
            {options.constraint_sets.map((set) => (
              <option key={set.name} value={set.name}>
                {set.name} · {set.constraints.length} threshold
                {set.constraints.length === 1 ? "" : "s"}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Seed"
          htmlFor="campaign-seed"
          hint="Blank uses the configured seed. The same seed and generator reproduce the run."
        >
          <input
            id="campaign-seed"
            type="number"
            value={seed}
            onChange={(e) => setSeed(e.target.value)}
            placeholder="default"
            className={`${inputClass} tabular`}
          />
        </Field>

        <Field label="Label" htmlFor="campaign-label" hint="Optional. How you will recognise it.">
          <input
            id="campaign-label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            maxLength={200}
            placeholder="wider TPSA band"
            className={inputClass}
          />
        </Field>
      </div>

      {error && <Alert title="refused">{error}</Alert>}

      <div className="flex flex-wrap items-center gap-4">
        <Button type="submit" disabled={busy}>
          {busy ? "starting…" : `run campaign against ${targetName}`}
        </Button>
        <p className="text-2xs text-muted">
          Runs on the server. You can close this page and come back to it.
        </p>
      </div>
    </form>
  );
}
