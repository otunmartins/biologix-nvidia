"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { EvidenceTag } from "@/components/ui/Primitives";
import { load3Dmol, type Viewer } from "@/lib/threedmol";
import type { CandidateStructure } from "@/lib/types";

/**
 * The candidate polymer itself, in three dimensions.
 *
 * Before this panel, a candidate was a SMILES string and a flat schematic
 * until someone spent a GPU job on docking it. That is the wrong order for
 * triage: whether a chain looks like a plausible excipient or like a knot of
 * rings is visible from its own geometry, needs no target, and is the first
 * thing a chemist wants to see.
 *
 * The coordinates come from the server's RDKit embedding, not from anything
 * drawn here, and the panel states what they are: one relaxed conformer of a
 * short capped chain in vacuum. The caveat is rendered as prominently as the
 * picture on purpose. A polished 3D render is persuasive out of proportion to
 * its evidential weight, and a conformer is the easiest thing in this product
 * to mistake for a simulation.
 *
 * Styling favours legibility over spectacle: carbon is held back so the
 * heteroatoms doing the chemistry read first, and the backbone is drawn
 * heavier than the hydrogens so the chain's path is followable at a glance.
 */

type Style = "ball-and-stick" | "sticks" | "spacefill";

/** Element colours. Carbon is deliberately muted — on a polymer it is most of
 *  the molecule, and letting it dominate hides the groups that matter. */
const ELEMENT_COLORS: Record<string, string> = {
  C: "#8d8f93",
  H: "#53555a",
  N: "#5b8dd9",
  O: "#e05c5c",
  S: "#d9b63f",
  P: "#e08a3c",
  F: "#6fc27b",
  Cl: "#6fc27b",
  Br: "#b5713f",
  I: "#9b6fc2",
};

const STYLE_LABELS: Record<Style, string> = {
  "ball-and-stick": "ball & stick",
  sticks: "sticks",
  spacefill: "spacefill",
};

export function MoleculeViewer({
  structure,
  structureUrl,
  title = "The candidate in three dimensions",
}: {
  structure: CandidateStructure;
  structureUrl: string;
  title?: string;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const viewer = useRef<Viewer | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [style, setStyle] = useState<Style>("ball-and-stick");
  const [spinning, setSpinning] = useState(false);
  const [showHydrogens, setShowHydrogens] = useState(false);

  const applyStyle = useCallback(
    (instance: Viewer, which: Style, hydrogens: boolean) => {
      instance.removeAllSurfaces();
      instance.setStyle({}, {});

      const colorscheme = { prop: "elem", map: ELEMENT_COLORS };

      if (which === "spacefill") {
        instance.setStyle({}, { sphere: { colorscheme, scale: 1.0 } });
      } else if (which === "sticks") {
        instance.setStyle({}, { stick: { radius: 0.14, colorscheme } });
      } else {
        instance.setStyle(
          {},
          {
            stick: { radius: 0.11, colorscheme },
            sphere: { colorscheme, scale: 0.26 },
          },
        );
      }

      // Hydrogens are most of the atom count and almost never the point, so
      // they are off by default and drawn thin when asked for.
      if (!hydrogens && which !== "spacefill") {
        instance.setStyle({ elem: "H" }, {});
      } else if (hydrogens && which === "ball-and-stick") {
        instance.setStyle(
          { elem: "H" },
          { stick: { radius: 0.05, colorscheme }, sphere: { colorscheme, scale: 0.13 } },
        );
      }
      instance.render();
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        await load3Dmol();
        if (cancelled || !host.current || !window.$3Dmol) return;

        const response = await fetch(structureUrl, { cache: "force-cache" });
        if (!response.ok) {
          throw new Error(`the coordinates could not be loaded (HTTP ${response.status})`);
        }
        const pdb = await response.text();
        if (cancelled || !host.current) return;

        const instance = window.$3Dmol.createViewer(host.current, {
          backgroundColor: "#121211",
          antialias: true,
        });
        viewer.current = instance;
        instance.addModel(pdb, "pdb");
        // A thin outline separates overlapping atoms where a long chain folds
        // back on itself, which is most of the time.
        instance.setViewStyle({ style: "outline", color: "black", width: 0.04 });
        applyStyle(instance, style, showHydrogens);
        instance.zoomTo();
        instance.zoom(0.9);
        instance.render();
        setReady(true);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "the viewer failed to start");
        }
      }
    })();

    return () => {
      cancelled = true;
      try {
        viewer.current?.clear();
      } catch {
        // Already torn down; nothing to do.
      }
      viewer.current = null;
    };
    // Style and hydrogen changes are applied by the effect below, so they are
    // deliberately not rebuild triggers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structureUrl, applyStyle]);

  useEffect(() => {
    const instance = viewer.current;
    if (!instance || !ready) return;
    applyStyle(instance, style, showHydrogens);
  }, [style, showHydrogens, ready, applyStyle]);

  useEffect(() => {
    const instance = viewer.current;
    if (!instance || !ready) return;
    instance.spin(spinning ? "y" : false, 0.4);
  }, [spinning, ready]);

  return (
    <div className="overflow-hidden rounded-xl border border-hairline bg-surface">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-hairline px-4 py-3">
        <div className="min-w-0">
          <h3 className="text-[13px] font-semibold uppercase tracking-[0.14em] text-ink-2">
            {title}
          </h3>
          <p className="mt-1 max-w-2xl text-2xs leading-relaxed text-muted">
            A DP-{structure.degree_of_polymerisation} capped chain, embedded with ETKDGv3 and
            relaxed with {structure.force_field}
            {structure.energy_kcal_per_mol !== null && structure.energy_kcal_per_mol !== undefined
              ? ` to ${structure.energy_kcal_per_mol.toFixed(1)} kcal/mol`
              : ""}
            {structure.converged ? "" : " (minimisation did not converge)"}.
          </p>
        </div>
        <EvidenceTag tier="computed" />
      </div>

      <div className="relative">
        <div
          ref={host}
          className="h-[380px] w-full"
          style={{ position: "relative" }}
          role="img"
          aria-label={`Three-dimensional conformer of a DP-${structure.degree_of_polymerisation} oligomer of this candidate`}
        />
        {!ready && !error && (
          <div className="absolute inset-0 grid place-items-center bg-surface-2/60">
            <p className="shimmer rounded px-3 py-1 text-2xs uppercase tracking-wider text-muted">
              embedding the chain…
            </p>
          </div>
        )}
        {error && (
          <div className="absolute inset-0 grid place-items-center bg-surface-2/80 px-6">
            <p className="max-w-sm text-center text-2xs leading-relaxed text-ink-2">
              <span className="font-mono text-state-crit">viewer unavailable</span> — {error}. The
              coordinates are still downloadable below.
            </p>
          </div>
        )}

        <div className="pointer-events-none absolute left-4 top-4 flex flex-wrap gap-x-3 gap-y-1.5 rounded-lg border border-hairline bg-plane/70 px-3 py-2 backdrop-blur-sm">
          {(["C", "N", "O", "S"] as const).map((element) => (
            <span key={element} className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: ELEMENT_COLORS[element] }}
              />
              <span className="font-mono text-2xs text-ink-2">{element}</span>
            </span>
          ))}
        </div>

        <div className="pointer-events-none absolute right-4 top-4 rounded-lg border border-hairline bg-plane/70 px-3 py-2 text-right backdrop-blur-sm">
          <p className="tabular font-mono text-2xs text-ink-2">
            {structure.n_heavy_atoms} heavy · {structure.n_atoms} atoms
          </p>
          <p className="tabular mt-0.5 font-mono text-2xs text-muted">
            {structure.molecular_weight.toFixed(0)} Da drawn
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-hairline px-4 py-3">
        <div className="flex items-center gap-1">
          {(Object.keys(STYLE_LABELS) as Style[]).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setStyle(option)}
              className={`rounded border px-2 py-0.5 text-2xs uppercase tracking-wider transition-colors ${
                style === option
                  ? "border-accent/50 bg-accent-soft text-ink"
                  : "border-hairline text-muted hover:text-ink"
              }`}
            >
              {STYLE_LABELS[option]}
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={() => setSpinning((s) => !s)}
          className="rounded border border-hairline px-2 py-0.5 text-2xs uppercase tracking-wider text-muted transition-colors hover:text-ink"
        >
          {spinning ? "stop spin" : "spin"}
        </button>

        <label className="flex items-center gap-1.5 text-2xs uppercase tracking-wider text-muted">
          <input
            type="checkbox"
            checked={showHydrogens}
            onChange={(e) => setShowHydrogens(e.target.checked)}
            className="accent-[var(--accent)]"
          />
          hydrogens
        </label>

        <a
          href={`${structureUrl}?fmt=sdf`}
          download
          className="ml-auto text-2xs uppercase tracking-wider text-muted hover:text-accent"
        >
          download sdf
        </a>
        <a
          href={structureUrl}
          download
          className="text-2xs uppercase tracking-wider text-muted hover:text-accent"
        >
          pdb
        </a>
      </div>

      <p className="border-t border-hairline px-4 py-3 text-2xs leading-relaxed text-muted">
        {structure.caveat}
      </p>

      {structure.warnings.length > 0 && (
        <ul className="border-t border-hairline px-4 py-3">
          {structure.warnings.map((warning) => (
            <li key={warning} className="text-2xs leading-relaxed text-state-warn">
              ! {warning}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
