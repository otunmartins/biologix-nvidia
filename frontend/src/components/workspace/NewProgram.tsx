"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Field, inputClass } from "@/components/ui/Primitives";
import { post } from "@/lib/client";
import type { Program, Target } from "@/lib/types";

/**
 * Open a program.
 *
 * The target is chosen once, here, and cannot be changed afterwards — a
 * program whose target moved halfway through would make its own history
 * unreadable, since candidates before and after were screened against
 * different proteins. The form says so rather than letting a user discover it
 * from a 400 later.
 */
export function NewProgram({ targets }: { targets: Target[] }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [targetKey, setTargetKey] = useState(targets[0]?.key ?? "");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const chosen = targets.find((t) => t.key === targetKey);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const result = await post<Program>("/projects", {
      name,
      target_key: targetKey,
      description: description.trim() || null,
    });
    if (!result.ok) {
      setError(result.error);
      setBusy(false);
      return;
    }
    router.push(`/programs/${result.data.id}`);
  }

  return (
    <div>
      <Button variant={open ? "ghost" : "primary"} onClick={() => setOpen((v) => !v)}>
        {open ? "cancel" : "new program"}
      </Button>

      <AnimatePresence>
        {open && (
          <motion.form
            onSubmit={submit}
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            <div className="mt-4 space-y-4 rounded-xl border border-hairline bg-surface p-5">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Program name" htmlFor="program-name">
                  <input
                    id="program-name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    required
                    maxLength={200}
                    placeholder="Insulin aspart · surfactant-free stabiliser"
                    className={inputClass}
                  />
                </Field>

                <Field
                  label="Biologic target"
                  htmlFor="program-target"
                  hint="Fixed for the life of the program: every campaign in it screens against this protein."
                >
                  <select
                    id="program-target"
                    value={targetKey}
                    onChange={(e) => setTargetKey(e.target.value)}
                    className={inputClass}
                  >
                    {targets.map((target) => (
                      <option key={target.key} value={target.key}>
                        {target.name}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>

              {chosen && (
                <p className="text-2xs leading-relaxed text-muted">
                  <span className="text-ink-2">{chosen.name}</span> · {chosen.class} ·{" "}
                  {chosen.n_chains} chain{chosen.n_chains === 1 ? "" : "s"},{" "}
                  {chosen.total_residues} residues
                  {chosen.n_modifications > 0 && (
                    <>
                      , {chosen.n_modifications} modification
                      {chosen.n_modifications === 1 ? "" : "s"}
                    </>
                  )}
                  {chosen.pdb_id && <> · PDB {chosen.pdb_id}</>}. Chains are never concatenated
                  into one sequence.
                </p>
              )}

              <Field
                label="What you are looking for"
                htmlFor="program-description"
                hint="Optional. Context for whoever reads the shortlist later, including you."
              >
                <textarea
                  id="program-description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={2}
                  maxLength={2000}
                  placeholder="Replace polysorbate 80 without raising viscosity at 100 mg/mL."
                  className={`${inputClass} resize-y`}
                />
              </Field>

              {error && <Alert title="could not open the program">{error}</Alert>}

              <Button type="submit" disabled={busy || !name.trim()}>
                {busy ? "opening…" : "open program"}
              </Button>
            </div>
          </motion.form>
        )}
      </AnimatePresence>
    </div>
  );
}
