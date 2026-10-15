"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { EvidenceTag } from "@/components/ui/Primitives";
import type { Job, StructureArtifact } from "@/lib/types";

/**
 * The protein–polymer complex, in three dimensions, from real coordinates.
 *
 * Every atom on screen came out of a job: a DiffDock pose, a Boltz-2 predicted
 * complex, or a frame of an OpenMM trajectory. Nothing here is drawn from a
 * guess, and nothing is idealised for the picture — which is why the panel
 * states what it is showing and repeats the caveat attached to the job that
 * produced it. A beautiful render of a structure is persuasive out of
 * proportion to its evidential weight, and that is exactly the failure mode
 * worth guarding against.
 *
 * 3Dmol.js is loaded from a CDN on demand rather than bundled: the viewer is
 * one panel on one page, and 800 KB of WebGL has no business in the main
 * bundle.
 */

const THREEDMOL_SRC = "https://cdnjs.cloudflare.com/ajax/libs/3Dmol/2.4.2/3Dmol-min.js";

type Viewer = {
  addModel: (data: string, format: string) => unknown;
  setStyle: (selection: object, style: object) => void;
  addSurface: (type: unknown, style: object, selection?: object) => void;
  removeAllSurfaces: () => void;
  removeAllModels: () => void;
  setBackgroundColor: (color: string, alpha?: number) => void;
  zoomTo: (selection?: object) => void;
  zoom: (factor: number) => void;
  render: () => void;
  spin: (axis: string | boolean, speed?: number) => void;
  setViewStyle: (style: object) => void;
  getModel: (index?: number) => { setFrame?: (n: number) => void } | undefined;
  setFrame: (frame: number) => Promise<void> | void;
  getNumFrames?: () => number;
  resize: () => void;
  clear: () => void;
};

declare global {
  interface Window {
    $3Dmol?: {
      createViewer: (element: HTMLElement, config: object) => Viewer;
      SurfaceType: { VDW: unknown; SAS: unknown };
    };
  }
}

let scriptPromise: Promise<void> | null = null;

function load3Dmol(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.$3Dmol) return Promise.resolve();
  if (scriptPromise) return scriptPromise;
  scriptPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = THREEDMOL_SRC;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () =>
      reject(new Error("the 3D viewer library could not be loaded from the CDN"));
    document.head.appendChild(script);
  });
  return scriptPromise;
}

type Style = "cartoon" | "surface" | "ribbon";

/** Protein in cool grey-blue, polymer in warm accent: the two must never be
 *  confusable, and colour is the only channel a 3D scene really has. */
const PROTEIN_COLOR = "#7d93ad";
const POLYMER_COLOR = "#f0a830";

export function ComplexViewer({
  job,
  artifacts,
}: {
  job: Job;
  artifacts: StructureArtifact[];
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const viewer = useRef<Viewer | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [style, setStyle] = useState<Style>("cartoon");
  const [spinning, setSpinning] = useState(false);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);

  const trajectory = artifacts.find((a) => a.kind === "trajectory");
  const complex = artifacts.find((a) => a.kind === "complex");
  const target = artifacts.find((a) => a.kind === "target");
  const poses = artifacts.filter((a) => a.kind === "pose");
  const [poseIndex, setPoseIndex] = useState(0);

  const frames = trajectory?.n_frames ?? 0;
  const primary = trajectory ?? complex ?? target;

  const fetchStructure = useCallback(
    async (name: string) => {
      const response = await fetch(
        `/api/bx/jobs/${job.id}/structures/${encodeURIComponent(name)}`,
        { cache: "force-cache" },
      );
      if (!response.ok) throw new Error(`could not load ${name} (HTTP ${response.status})`);
      return response.text();
    },
    [job.id],
  );

  // Build the scene once, then only restyle on interaction.
  useEffect(() => {
    let cancelled = false;
    if (!primary) return;

    (async () => {
      try {
        await load3Dmol();
        if (cancelled || !host.current || !window.$3Dmol) return;

        const instance = window.$3Dmol.createViewer(host.current, {
          backgroundColor: "#121211",
          antialias: true,
        });
        viewer.current = instance;

        if (trajectory) {
          const text = await fetchStructure(trajectory.name);
          if (cancelled) return;
          instance.addModel(text, "pdb");
        } else if (complex) {
          const text = await fetchStructure(complex.name);
          if (cancelled) return;
          // Boltz-2 returns mmCIF; 3Dmol calls that format "cif".
          const format = ["cif", "mmcif"].includes(complex.format) ? "cif" : "pdb";
          instance.addModel(text, format);
        } else {
          // A docking job: the prepared target plus one pose, as two models,
          // so the polymer can be styled separately from the protein.
          if (target) {
            const text = await fetchStructure(target.name);
            if (cancelled) return;
            instance.addModel(text, "pdb");
          }
          if (poses[poseIndex]) {
            const text = await fetchStructure(poses[poseIndex].name);
            if (cancelled) return;
            instance.addModel(text, poses[poseIndex].format);
          }
        }

        if (cancelled) return;
        instance.setViewStyle({ style: "outline", color: "black", width: 0.03 });
        instance.zoomTo();
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
        // The viewer may already be gone; nothing to do.
      }
      viewer.current = null;
    };
  }, [primary, trajectory, complex, target, poses, poseIndex, fetchStructure]);

  // Styling, applied on every change of style so the two selections stay
  // distinct: protein as cartoon, everything non-protein as sticks.
  useEffect(() => {
    const instance = viewer.current;
    if (!instance || !ready) return;

    instance.removeAllSurfaces();
    instance.setStyle({}, {});
    const proteinSelection = { resn: PROTEIN_RESIDUES };
    const ligandSelection = { not: { resn: PROTEIN_RESIDUES } };

    if (style === "ribbon") {
      instance.setStyle(proteinSelection, {
        cartoon: { style: "trace", color: PROTEIN_COLOR, thickness: 0.4 },
      });
    } else {
      instance.setStyle(proteinSelection, {
        cartoon: { color: PROTEIN_COLOR, arrows: true, thickness: 0.8 },
      });
    }

    instance.setStyle(ligandSelection, {
      stick: { radius: 0.18, color: POLYMER_COLOR },
      sphere: { radius: 0.34, color: POLYMER_COLOR },
    });

    if (style === "surface") {
      // Faint, so the chain underneath still reads. A solid surface looks
      // impressive and hides the thing being examined.
      instance.addSurface(
        window.$3Dmol?.SurfaceType.VDW,
        { opacity: 0.45, color: PROTEIN_COLOR },
        proteinSelection,
      );
    }
    instance.render();
  }, [style, ready]);

  useEffect(() => {
    const instance = viewer.current;
    if (!instance || !ready) return;
    instance.spin(spinning ? "y" : false, 0.5);
  }, [spinning, ready]);

  // Frame stepping for a trajectory.
  useEffect(() => {
    const instance = viewer.current;
    if (!instance || !ready || !frames) return;
    void instance.setFrame(frame);
    instance.render();
  }, [frame, frames, ready]);

  useEffect(() => {
    if (!playing || !frames) return;
    const id = setInterval(() => {
      setFrame((current) => (current + 1) % frames);
    }, 90);
    return () => clearInterval(id);
  }, [playing, frames]);

  if (!primary) return null;

  const caveat = typeof job.result?.caveat === "string" ? job.result.caveat : null;

  return (
    <div className="overflow-hidden rounded-xl border border-hairline bg-surface">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-hairline px-4 py-3">
        <div>
          <h3 className="text-[13px] font-semibold uppercase tracking-[0.14em] text-ink-2">
            {trajectory
              ? "Molecular dynamics"
              : complex
                ? "Predicted complex"
                : "Docked pose"}
          </h3>
          <p className="mt-1 max-w-2xl text-2xs leading-relaxed text-muted">
            {primary.description}
          </p>
        </div>
        <EvidenceTag tier="computed" />
      </div>

      <div className="relative">
        <div
          ref={host}
          className="h-[420px] w-full"
          style={{ position: "relative" }}
          role="img"
          aria-label={`Three-dimensional view: ${primary.description}`}
        />
        {!ready && !error && (
          <div className="absolute inset-0 grid place-items-center bg-surface-2/60">
            <p className="shimmer rounded px-3 py-1 text-2xs uppercase tracking-wider text-muted">
              loading coordinates…
            </p>
          </div>
        )}
        {error && (
          <div className="absolute inset-0 grid place-items-center bg-surface-2/80 px-6">
            <p className="max-w-sm text-center text-2xs leading-relaxed text-ink-2">
              <span className="font-mono text-state-crit">viewer unavailable</span> — {error}.
              The coordinates are still downloadable from the job above.
            </p>
          </div>
        )}

        {/* The legend is not decoration: the two colours are the only thing
            telling protein from polymer. */}
        <div className="pointer-events-none absolute left-4 top-4 space-y-1.5 rounded-lg border border-hairline bg-plane/70 px-3 py-2 backdrop-blur-sm">
          <Key color={PROTEIN_COLOR} label={`${job.result?.target ?? "target"} · cartoon`} />
          <Key color={POLYMER_COLOR} label="oligomer · sticks" />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-hairline px-4 py-3">
        <div className="flex items-center gap-1">
          {(["cartoon", "ribbon", "surface"] as Style[]).map((option) => (
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
              {option}
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

        {frames > 1 && (
          <div className="flex min-w-[220px] flex-1 items-center gap-3">
            <button
              type="button"
              onClick={() => setPlaying((p) => !p)}
              className="rounded border border-accent/50 bg-accent-soft px-2 py-0.5 text-2xs uppercase tracking-wider text-ink"
            >
              {playing ? "pause" : "play"}
            </button>
            <input
              type="range"
              min={0}
              max={frames - 1}
              value={frame}
              onChange={(e) => {
                setPlaying(false);
                setFrame(Number(e.target.value));
              }}
              aria-label="trajectory frame"
              className="flex-1 accent-[var(--accent)]"
            />
            <span className="tabular shrink-0 font-mono text-2xs text-muted">
              frame {frame + 1}/{frames}
              {trajectory?.picoseconds
                ? ` · ${((trajectory.picoseconds * (frame + 1)) / frames).toFixed(1)} ps`
                : ""}
            </span>
          </div>
        )}

        {poses.length > 1 && (
          <label className="flex items-center gap-2 text-2xs text-muted">
            pose
            <select
              value={poseIndex}
              onChange={(e) => {
                setReady(false);
                setPoseIndex(Number(e.target.value));
              }}
              className="rounded border border-hairline bg-surface-2 px-1.5 py-0.5 text-2xs text-ink"
            >
              {poses.map((pose, index) => (
                <option key={pose.name} value={index}>
                  {pose.rank ?? index + 1}
                  {pose.confidence !== undefined && pose.confidence !== null
                    ? ` · conf ${pose.confidence.toFixed(2)}`
                    : ""}
                </option>
              ))}
            </select>
          </label>
        )}

        <a
          href={`/api/bx/jobs/${job.id}/structures/${encodeURIComponent(primary.name)}`}
          download
          className="ml-auto text-2xs uppercase tracking-wider text-muted hover:text-accent"
        >
          download {primary.format}
        </a>
      </div>

      {caveat && (
        <p className="border-t border-hairline px-4 py-3 text-2xs leading-relaxed text-muted">
          {caveat}
        </p>
      )}
    </div>
  );
}

function Key({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-2">
      <span
        aria-hidden
        className="inline-block h-2 w-2 rounded-sm"
        style={{ background: color }}
      />
      <span className="text-2xs text-ink-2">{label}</span>
    </span>
  );
}

/** The standard residues, so "protein" and "everything else" can be selected
 *  apart in a file that contains both. */
const PROTEIN_RESIDUES = [
  "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
  "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
  "HID", "HIE", "HIP", "CYX", "ASH", "GLH", "LYN", "MSE",
];
