"use client";

import { motion } from "framer-motion";

/**
 * A schematic of the chain: end cap, repeat units, end cap.
 *
 * This is **not** a structure depiction, and it says so on the page. It draws
 * what the specification says — how many distinct repeat units, in what
 * architecture, at what chain length, capped how — because those are the four
 * things that make a polymer different from a molecule, and a reader coming
 * from a small-molecule tool needs them made obvious.
 *
 * Drawing the actual chemistry would mean rendering a molecule from SMILES in
 * the browser. The app does not do that: a wrong depiction of a real structure
 * is far worse than an honest diagram of the specification.
 */
export function ChainSchematic({
  repeatUnits,
  dp,
  endGroup,
  architecture,
}: {
  repeatUnits: string[];
  dp: number;
  endGroup: string;
  architecture: string;
}) {
  // Enough beads to read the pattern, never implying the true chain length —
  // the real DP is stated in words beside it.
  const beads = Math.min(Math.max(dp, 4), 9);
  const blocks = Math.max(repeatUnits.length, 1);

  return (
    <section className="overflow-hidden rounded-xl border border-hairline bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-hairline px-5 py-3">
        <h2 className="text-[13px] font-semibold uppercase tracking-[0.14em] text-ink-2">
          Chain specification
        </h2>
        <p className="text-2xs text-muted">
          Schematic of the specification — not a depiction of the structure
        </p>
      </div>

      <div className="px-5 py-6">
        <svg
          role="img"
          aria-label={`Schematic: ${architecture} of ${blocks} repeat unit type${blocks === 1 ? "" : "s"}, degree of polymerisation ${dp}, capped with ${endGroup}`}
          viewBox="0 0 560 72"
          className="h-auto w-full"
        >
          {/* Backbone */}
          <line
            x1="24"
            y1="36"
            x2="536"
            y2="36"
            stroke="var(--baseline)"
            strokeWidth="1.5"
          />

          {/* End caps, drawn differently from repeat units because they are
              chemically different and are what make the oligomer buildable. */}
          <EndCap x={24} label={endGroup} />
          <EndCap x={536} label={endGroup} anchor="end" />

          {Array.from({ length: beads }).map((_, index) => {
            const x = 24 + ((536 - 24) / (beads - 1)) * index;
            // In a block copolymer the units are laid down in runs; the
            // schematic shows which unit type sits where.
            const unit = blocks > 1 ? Math.floor((index / beads) * blocks) : 0;
            const colour = unit === 0 ? "var(--seq-400)" : unit === 1 ? "var(--seq-600)" : "var(--accent)";
            return (
              <motion.circle
                key={index}
                cx={x}
                cy={36}
                r={9}
                fill="var(--surface-2)"
                stroke={colour}
                strokeWidth="1.6"
                initial={{ scale: 0, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ delay: 0.04 * index, duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
                style={{ originX: `${x}px`, originY: "36px" }}
              />
            );
          })}

          {/* An ellipsis in the middle when the drawing is shorter than the
              real chain, so the diagram never claims to be to scale. */}
          {dp > beads && (
            <g>
              <rect x="258" y="22" width="44" height="28" fill="var(--surface-1)" />
              <text
                x="280"
                y="41"
                textAnchor="middle"
                className="font-mono"
                fontSize="13"
                fill="var(--text-muted)"
              >
                ⋯
              </text>
            </g>
          )}
        </svg>

        <dl className="mt-5 grid gap-x-8 gap-y-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <Item
            term="Architecture"
            value={architecture}
            note={blocks > 1 ? `${blocks} distinct repeat units` : "single repeat unit"}
          />
          <Item
            term="Degree of polymerisation"
            value={String(dp)}
            note="As specified — the drawing above is not to scale"
          />
          <Item term="End groups" value={endGroup} note="Both termini, explicitly capped" />
          <Item
            term="Chain-length distribution"
            value="not modelled"
            note="A real polymer is polydisperse; this specification names one DP"
          />
        </dl>
      </div>
    </section>
  );
}

function EndCap({
  x,
  label,
  anchor = "start",
}: {
  x: number;
  label: string;
  anchor?: "start" | "end";
}) {
  return (
    <g>
      <rect
        x={x - 7}
        y={29}
        width={14}
        height={14}
        rx={3}
        fill="var(--surface-3)"
        stroke="var(--text-muted)"
        strokeWidth="1.2"
      />
      <text
        x={x}
        y={20}
        textAnchor={anchor === "end" ? "end" : "start"}
        fontSize="9"
        fill="var(--text-muted)"
        className="font-mono uppercase"
      >
        {label}
      </text>
    </g>
  );
}

function Item({ term, value, note }: { term: string; value: string; note: string }) {
  return (
    <div>
      <dt className="text-2xs uppercase tracking-wider text-muted">{term}</dt>
      <dd className="mt-1 font-mono text-2xs text-ink">{value}</dd>
      <dd className="mt-1 text-2xs leading-relaxed text-muted">{note}</dd>
    </div>
  );
}
