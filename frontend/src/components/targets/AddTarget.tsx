"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Field, inputClass } from "@/components/ui/Primitives";
import { post } from "@/lib/client";
import type { ImportPreview, TargetSummary } from "@/lib/types";

/**
 * Add any biologic.
 *
 * Deliberately two steps. The import is previewed before anything is stored,
 * because the ways it can be subtly wrong are the ways only a person can
 * catch: chains split where they should not be, a crystal structure missing
 * its termini, a chain over the limit a sequence model will accept. Storing
 * first and showing afterwards would turn those into cleanup work.
 */

const SOURCES = [
  {
    key: "pasted" as const,
    label: "Paste sequences",
    blurb:
      "FASTA or a bare sequence. Each record becomes its own chain — a multi-chain biologic is never concatenated into one.",
  },
  {
    key: "pdb" as const,
    label: "PDB entry",
    blurb:
      "Chains are read from the deposited coordinates. Choose this when you need docking or simulation: what is in the file is what gets docked.",
  },
  {
    key: "uniprot" as const,
    label: "UniProt accession",
    blurb:
      "The canonical sequence, one chain. UniProt describes a polypeptide, not an assembly.",
  },
];

const CLASSES = [
  { value: "mab", label: "Monoclonal antibody" },
  { value: "protein", label: "Protein" },
  { value: "peptide", label: "Peptide" },
  { value: "fusion", label: "Fusion protein" },
  { value: "enzyme", label: "Enzyme" },
  { value: "other", label: "Other" },
];

export function AddTarget({ onAdded }: { onAdded?: (target: TargetSummary) => void }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState<"pasted" | "pdb" | "uniprot">("pasted");
  const [name, setName] = useState("");
  const [sequences, setSequences] = useState("");
  const [pdbId, setPdbId] = useState("");
  const [uniprotId, setUniprotId] = useState("");
  const [biologicClass, setBiologicClass] = useState("protein");
  const [notes, setNotes] = useState("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [remedy, setRemedy] = useState<string | null>(null);

  function reset() {
    setPreview(null);
    setError(null);
    setRemedy(null);
  }

  async function runPreview(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    reset();
    const result = await post<ImportPreview>("/targets/preview", {
      source,
      name: name.trim() || null,
      sequences: source === "pasted" ? sequences : null,
      pdb_id: source === "pdb" ? pdbId : null,
      uniprot_id: source === "uniprot" ? uniprotId : null,
    });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setPreview(result.data);
    if (!name.trim()) setName(result.data.name);
  }

  async function commit() {
    if (!preview) return;
    setBusy(true);
    setError(null);
    const result = await post<TargetSummary>("/targets", {
      name: name.trim() || preview.name,
      biologic_class: biologicClass,
      chains: preview.chains.map((chain) => ({
        chain_id: chain.chain_id,
        sequence: chain.sequence,
        description: chain.description,
      })),
      source: preview.source,
      source_detail: preview.source_detail,
      pdb_id: preview.pdb_id,
      uniprot_id: preview.uniprot_id,
      notes: notes.trim() || preview.notes,
    });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setOpen(false);
    setPreview(null);
    setSequences("");
    onAdded?.(result.data);
    router.refresh();
  }

  return (
    <div>
      <Button variant={open ? "ghost" : "primary"} onClick={() => setOpen((v) => !v)}>
        {open ? "cancel" : "add a biologic"}
      </Button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            <form
              onSubmit={runPreview}
              className="mt-4 space-y-5 rounded-xl border border-hairline bg-surface p-5"
            >
              <fieldset>
                <legend className="text-2xs uppercase tracking-wider text-muted">
                  Where the sequence comes from
                </legend>
                <div className="mt-2.5 grid gap-3 lg:grid-cols-3">
                  {SOURCES.map((option) => {
                    const selected = source === option.key;
                    return (
                      <label
                        key={option.key}
                        className={`relative cursor-pointer rounded-xl border p-4 transition-colors ${
                          selected
                            ? "border-accent/60 bg-accent-soft"
                            : "border-hairline bg-surface-2 hover:border-hairline-strong"
                        }`}
                      >
                        <input
                          type="radio"
                          name="source"
                          value={option.key}
                          checked={selected}
                          onChange={() => {
                            setSource(option.key);
                            reset();
                          }}
                          className="absolute right-4 top-4 accent-[var(--accent)]"
                        />
                        <span className="block pr-6 text-xs text-ink">{option.label}</span>
                        <span className="mt-2 block text-2xs leading-relaxed text-muted">
                          {option.blurb}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </fieldset>

              {source === "pasted" && (
                <Field
                  label="Sequences"
                  htmlFor="target-sequences"
                  hint="One FASTA record per chain. Unknown residue letters are refused rather than guessed at."
                >
                  <textarea
                    id="target-sequences"
                    value={sequences}
                    onChange={(e) => setSequences(e.target.value)}
                    rows={7}
                    spellCheck={false}
                    placeholder={">Heavy chain\nEVQLVESGGGLVQPGG...\n>Light chain\nDIQMTQSPSSLSASVG..."}
                    className={`${inputClass} resize-y font-mono`}
                  />
                </Field>
              )}

              {source === "pdb" && (
                <Field
                  label="PDB id"
                  htmlFor="target-pdb"
                  hint="Four characters beginning with a digit. Fetched from RCSB and hashed."
                >
                  <input
                    id="target-pdb"
                    value={pdbId}
                    onChange={(e) => setPdbId(e.target.value)}
                    maxLength={8}
                    placeholder="3I40"
                    className={`${inputClass} font-mono uppercase`}
                  />
                </Field>
              )}

              {source === "uniprot" && (
                <Field
                  label="UniProt accession"
                  htmlFor="target-uniprot"
                  hint="For example P01308. The canonical sequence is retrieved and the URL recorded."
                >
                  <input
                    id="target-uniprot"
                    value={uniprotId}
                    onChange={(e) => setUniprotId(e.target.value)}
                    maxLength={16}
                    placeholder="P01308"
                    className={`${inputClass} font-mono uppercase`}
                  />
                </Field>
              )}

              {error && (
                <Alert title="import refused">
                  {error}
                  {remedy && <span className="mt-1.5 block text-2xs text-muted">{remedy}</span>}
                </Alert>
              )}

              <Button type="submit" disabled={busy}>
                {busy && !preview ? "reading…" : "preview"}
              </Button>

              <AnimatePresence>
                {preview && (
                  <motion.div
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    className="space-y-4 border-t border-hairline pt-5"
                  >
                    <div>
                      <h3 className="text-2xs uppercase tracking-wider text-muted">
                        What was read — check it before saving
                      </h3>
                      <ul className="mt-2.5 space-y-2">
                        {preview.chains.map((chain) => (
                          <li
                            key={chain.chain_id}
                            className="rounded-lg border border-hairline bg-surface-2 p-3"
                          >
                            <div className="flex items-baseline justify-between gap-3">
                              <span className="font-mono text-xs text-ink">
                                chain {chain.chain_id}
                              </span>
                              <span className="tabular text-2xs text-muted">
                                {chain.length} residues
                              </span>
                            </div>
                            {chain.description && (
                              <p className="mt-1 truncate text-2xs text-muted">
                                {chain.description}
                              </p>
                            )}
                            <p className="mt-1.5 break-all font-mono text-2xs leading-relaxed text-ink-2">
                              {chain.sequence.slice(0, 120)}
                              {chain.sequence.length > 120 && (
                                <span className="text-muted">
                                  … +{chain.sequence.length - 120}
                                </span>
                              )}
                            </p>
                          </li>
                        ))}
                      </ul>
                      <p className="mt-2 text-2xs text-muted">
                        {preview.n_chains} chain{preview.n_chains === 1 ? "" : "s"},{" "}
                        {preview.total_residues} residues · {preview.source_detail}
                      </p>
                    </div>

                    {preview.warnings.length > 0 && (
                      <ul className="space-y-1.5">
                        {preview.warnings.map((warning) => (
                          <li key={warning} className="text-2xs leading-relaxed text-state-warn">
                            ! {warning}
                          </li>
                        ))}
                      </ul>
                    )}

                    <p
                      className={`rounded-lg border px-3 py-2 text-2xs leading-relaxed ${
                        preview.can_dock
                          ? "border-state-good/30 bg-state-good/5 text-ink-2"
                          : "border-state-warn/30 bg-state-warn/5 text-ink-2"
                      }`}
                    >
                      {preview.capability_note}
                    </p>

                    <div className="grid gap-4 sm:grid-cols-2">
                      <Field label="Name" htmlFor="target-name">
                        <input
                          id="target-name"
                          value={name}
                          onChange={(e) => setName(e.target.value)}
                          maxLength={200}
                          className={inputClass}
                        />
                      </Field>
                      <Field
                        label="Class"
                        htmlFor="target-class"
                        hint="Recorded, and used to describe the target everywhere it appears."
                      >
                        <select
                          id="target-class"
                          value={biologicClass}
                          onChange={(e) => setBiologicClass(e.target.value)}
                          className={inputClass}
                        >
                          {CLASSES.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </Field>
                    </div>

                    <Field
                      label="Notes"
                      htmlFor="target-notes"
                      hint="Optional. Anything a reader needs in order to interpret results against this target."
                    >
                      <textarea
                        id="target-notes"
                        value={notes}
                        onChange={(e) => setNotes(e.target.value)}
                        rows={2}
                        maxLength={4000}
                        className={`${inputClass} resize-y`}
                      />
                    </Field>

                    <p className="text-2xs leading-relaxed text-muted">
                      {preview.modifications_note}
                    </p>

                    <div className="flex items-center gap-3">
                      <Button onClick={commit} disabled={busy}>
                        {busy ? "saving…" : "save this target"}
                      </Button>
                      <button
                        type="button"
                        onClick={reset}
                        className="text-2xs uppercase tracking-wider text-muted hover:text-ink"
                      >
                        start over
                      </button>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </form>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
