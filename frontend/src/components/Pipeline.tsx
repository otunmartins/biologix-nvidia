"use client";

import { useState } from "react";
import { STATE_FILL, STATE_GLYPH, STATE_LABEL, liveResultFor, stateOf } from "@/lib/state";
import type { Availability, State, Verification } from "@/lib/types";
import { StateLegend } from "@/components/StateBadge";

/**
 * The engine, drawn as the computation it actually performs.
 *
 * Geometry is fixed in a viewBox and scaled as a whole, so no label can ever
 * collide or be clipped at any viewport width. Flow is animated only along an
 * edge whose two stages have *both* answered a real call — a dead stage never
 * appears to be receiving data.
 */

type Stage = {
  id: string;
  name: string;
  engine: string;
  adapter?: string;
  /** A stage that runs here, with no network call: always on. */
  local?: boolean;
  col: number;
  row: "up" | "mid" | "down";
  note: string;
};

const STAGES: Stage[] = [
  {
    id: "target",
    name: "Target biologic",
    engine: "registry · local",
    local: true,
    col: 0,
    row: "mid",
    note: "Chains, disulfides and modifications, kept separate rather than concatenated.",
  },
  {
    id: "esm2",
    name: "ESM-2 650M",
    engine: "BioNeMo NIM",
    adapter: "bionemo-esm2",
    col: 1,
    row: "mid",
    note: "Per-chain protein embeddings. Features for the surrogate model, not predictions.",
  },
  {
    id: "genmol",
    name: "GenMol 2.0.0",
    engine: "BioNeMo NIM",
    adapter: "bionemo-genmol",
    col: 2,
    row: "up",
    note: "Fragment-based molecule generation from a masked seed fragment.",
  },
  {
    id: "molmim",
    name: "MolMIM 1.0.0",
    engine: "BioNeMo NIM",
    adapter: "bionemo-molmim",
    col: 2,
    row: "down",
    note: "Latent-space sampling around known excipient chemistry, similarity-constrained.",
  },
  {
    id: "repeat",
    name: "Repeat units",
    engine: "RDKit · local",
    local: true,
    col: 3,
    row: "mid",
    note: "A molecule is not a repeat unit: each candidate must expose a polymerisable handle.",
  },
  {
    id: "screen",
    name: "Screening",
    engine: "RDKit / nvMolKit",
    adapter: "nvmolkit",
    col: 4,
    row: "mid",
    note: "Descriptors, fingerprints, diversity. Falls back to RDKit on CPU without a GPU.",
  },
  {
    id: "diffdock",
    name: "DiffDock 2.1.0",
    engine: "BioNeMo NIM",
    adapter: "bionemo-diffdock",
    col: 5,
    row: "up",
    note: "Blind docking poses, used as starting geometries for simulation.",
  },
  {
    id: "boltz2",
    name: "Boltz-2",
    engine: "BioNeMo NIM",
    adapter: "bionemo-boltz2",
    col: 5,
    row: "down",
    note: "Complex structure and a binding-affinity estimate. Oligomers are out of distribution.",
  },
  {
    id: "md",
    name: "MD + ranking",
    engine: "OpenMM · CPU",
    local: true,
    col: 6,
    row: "mid",
    note: "Simulation and ranking. Runs on CPU; no CUDA platform is present on this machine.",
  },
];

/**
 * Edges carry no inline labels on purpose. The horizontal gap between two
 * columns is 40px and the shortest useful label is wider than that, so an
 * in-diagram label would either overflow a node box or need clipping — and a
 * clipped label is worse than none. What flows along the edges is named in
 * the caption under the diagram instead, and each stage's own contribution is
 * in the hover and focus detail.
 */
const EDGES: Array<{ from: string; to: string }> = [
  { from: "target", to: "esm2" },
  { from: "esm2", to: "genmol" },
  { from: "esm2", to: "molmim" },
  { from: "genmol", to: "repeat" },
  { from: "molmim", to: "repeat" },
  { from: "repeat", to: "screen" },
  { from: "screen", to: "diffdock" },
  { from: "screen", to: "boltz2" },
  { from: "diffdock", to: "md" },
  { from: "boltz2", to: "md" },
];

const W = 150;
const H = 76;
const COL_X = [40, 230, 420, 610, 800, 990, 1180];
const ROW_Y = { up: 84, mid: 184, down: 284 } as const;

function box(stage: Stage) {
  return { x: COL_X[stage.col], y: ROW_Y[stage.row], w: W, h: H };
}

function curve(from: Stage, to: Stage): string {
  const a = box(from);
  const b = box(to);
  const x1 = a.x + a.w;
  const y1 = a.y + a.h / 2;
  const x2 = b.x;
  const y2 = b.y + b.h / 2;
  const dx = Math.max((x2 - x1) / 2, 18);
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

export function Pipeline({
  availability,
  verification,
}: {
  availability?: Availability;
  verification?: Verification;
}) {
  const [active, setActive] = useState<string | null>(null);

  const stateFor = (stage: Stage): State => {
    if (stage.local) return "verified";
    const adapter = availability?.adapters.find((a) => a.adapter === stage.adapter);
    const live = liveResultFor(verification, stage.adapter ?? "");
    return stateOf(adapter, live);
  };

  const states = new Map(STAGES.map((s) => [s.id, stateFor(s)]));
  const byId = new Map(STAGES.map((s) => [s.id, s]));
  const activeStage = active ? byId.get(active) : undefined;
  const activeLive = activeStage?.adapter ? liveResultFor(verification, activeStage.adapter) : undefined;
  const activeAdapter = activeStage?.adapter
    ? availability?.adapters.find((a) => a.adapter === activeStage.adapter)
    : undefined;

  return (
    <div>
      {/* Scaled as a whole, with a floor on its width: squeezed into a phone
          viewport the 12px stage labels would render around 3px. Below that
          floor the diagram scrolls sideways inside its panel instead. */}
      <div className="-mx-1 overflow-x-auto px-1 pb-1">
      <svg
        viewBox="0 0 1340 400"
        className="w-full min-w-[920px]"
        role="img"
        aria-label="Computational pipeline, stage by stage, with the live state of each"
      >
        <defs>
          <pattern id="pipe-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="var(--gridline)" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width="1340" height="400" fill="url(#pipe-grid)" opacity="0.35" />

        {EDGES.map((edge) => {
          const from = byId.get(edge.from);
          const to = byId.get(edge.to);
          if (!from || !to) return null;
          const path = curve(from, to);
          const flowing =
            states.get(edge.from) === "verified" && states.get(edge.to) === "verified";
          return (
            <g key={`${edge.from}-${edge.to}`}>
              <path d={path} fill="none" stroke="var(--baseline)" strokeWidth="1" />
              {flowing && (
                <path
                  d={path}
                  fill="none"
                  stroke="var(--accent)"
                  strokeWidth="1.5"
                  strokeDasharray="4 20"
                  strokeLinecap="round"
                  className="edge-flow"
                  opacity="0.9"
                />
              )}
            </g>
          );
        })}

        {STAGES.map((stage) => {
          const { x, y, w, h } = box(stage);
          const state = states.get(stage.id) ?? "inactive";
          const live = stage.adapter ? liveResultFor(verification, stage.adapter) : undefined;
          const isActive = active === stage.id;
          return (
            <g
              key={stage.id}
              tabIndex={0}
              role="button"
              aria-label={`${stage.name}: ${STATE_LABEL[state]}`}
              onMouseEnter={() => setActive(stage.id)}
              onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(stage.id)}
              onBlur={() => setActive(null)}
              style={{ cursor: "pointer" }}
            >
              <title>{`${stage.name} — ${STATE_LABEL[state]}. ${stage.note}`}</title>
              {state === "verified" && (
                <rect
                  x={x - 2}
                  y={y - 2}
                  width={w + 4}
                  height={h + 4}
                  rx="8"
                  fill="none"
                  stroke={STATE_FILL.verified}
                  strokeWidth="1"
                  opacity={isActive ? 0.5 : 0.22}
                  className={isActive ? undefined : "breathe"}
                />
              )}
              <rect
                x={x}
                y={y}
                width={w}
                height={h}
                rx="6"
                fill="var(--surface-2)"
                stroke={isActive ? "var(--accent)" : "var(--border)"}
                strokeWidth="1"
              />
              <circle cx={x + 13} cy={y + 17} r="3.5" fill={STATE_FILL[state]} />
              <text
                x={x + 23}
                y={y + 21}
                className="fill-ink-2"
                style={{ fontSize: 11, fontFamily: "ui-monospace, Consolas, monospace" }}
              >
                {STATE_GLYPH[state]}
              </text>
              <text
                x={x + w - 12}
                y={y + 21}
                textAnchor="end"
                className="fill-muted tabular"
                style={{ fontSize: 9.5, letterSpacing: "0.08em" }}
              >
                {stage.local ? "LOCAL" : "NIM"}
              </text>
              <text x={x + 13} y={y + 42} className="fill-ink" style={{ fontSize: 12.5, fontWeight: 600 }}>
                {stage.name}
              </text>
              <text
                x={x + 13}
                y={y + 60}
                className="fill-muted"
                style={{ fontSize: 9.5, fontFamily: "ui-monospace, Consolas, monospace" }}
              >
                {stage.engine}
              </text>
              {live?.ok && live.seconds !== undefined && (
                <text
                  x={x + w - 12}
                  y={y + 60}
                  textAnchor="end"
                  className="fill-ink-2 tabular"
                  style={{ fontSize: 10 }}
                >
                  {live.seconds.toFixed(2)}s
                </text>
              )}
            </g>
          );
        })}
      </svg>
      </div>

      <p className="mt-3 text-2xs leading-relaxed text-muted">
        Flow along the edges: target chains → per-chain embeddings → generated molecules →
        polymerisable repeat units → screened candidates → docked poses and affinity estimates →
        simulated and ranked.
      </p>

      <div className="mt-4 flex flex-col gap-3 border-t border-hairline pt-4 sm:flex-row sm:items-start sm:justify-between">
        <StateLegend />
        <p className="min-h-[2.5rem] max-w-xl text-xs leading-relaxed text-ink-2">
          {activeStage ? (
            <>
              <span className="font-semibold text-ink">{activeStage.name}</span>{" "}
              <span className="text-muted">· {STATE_LABEL[states.get(activeStage.id) ?? "inactive"]}</span>
              <br />
              {activeStage.note}
              {activeLive && !activeLive.ok && activeLive.message && (
                <>
                  <br />
                  <span className="font-mono text-2xs text-state-crit">
                    {activeLive.error}: {activeLive.message.slice(0, 160)}
                  </span>
                </>
              )}
              {!activeLive && activeAdapter?.reason && (
                <>
                  <br />
                  <span className="font-mono text-2xs text-muted">{activeAdapter.reason.slice(0, 180)}</span>
                </>
              )}
            </>
          ) : (
            <span className="text-muted">
              Hover or tab through a stage for what it contributes and what it last did.
            </span>
          )}
        </p>
      </div>
    </div>
  );
}
