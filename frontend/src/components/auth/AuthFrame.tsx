import type { ReactNode } from "react";

/**
 * The sign-in surround.
 *
 * The right half states what the product is and, immediately, what it is not.
 * That is not modesty: a tool that proposes drug-formulation candidates has to
 * say on its front door that nothing it outputs is a measurement, because
 * that is the claim a reader will otherwise assume.
 */
export function AuthFrame({
  title,
  lede,
  children,
}: {
  title: string;
  lede: string;
  children: ReactNode;
}) {
  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <div className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8">
            <div className="flex items-center gap-2.5">
              <ChainMark />
              <span className="text-[13px] font-semibold uppercase tracking-[0.2em] text-ink">
                Biologix
              </span>
            </div>
            <h1 className="mt-6 text-xl font-semibold text-ink">{title}</h1>
            <p className="mt-2 text-xs leading-relaxed text-muted">{lede}</p>
          </div>
          {children}
        </div>
      </div>

      <aside className="relative hidden overflow-hidden border-l border-hairline bg-surface-2/40 lg:block">
        <ChainField />
        <div className="relative flex h-full flex-col justify-between p-10">
          <div>
            <p className="text-2xs uppercase tracking-[0.18em] text-muted">
              Polymer excipient discovery
            </p>
            <h2 className="mt-4 max-w-md text-2xl font-semibold leading-snug text-ink">
              A polymer is not a molecule. It is a specification.
            </h2>
            <p className="mt-4 max-w-md text-xs leading-relaxed text-ink-2">
              Repeat units, a composition, an architecture, a chain-length distribution. Anything
              that needs an ordinary molecule — fingerprints, docking, simulation — builds an
              explicit capped oligomer first and records how it was built, so every number can be
              read as what it is.
            </p>
          </div>

          <dl className="grid max-w-md grid-cols-2 gap-x-6 gap-y-5">
            <Fact
              term="Generation"
              detail="BioNeMo GenMol and MolMIM, against a deterministic baseline that needs no GPU."
            />
            <Fact
              term="Structure"
              detail="DiffDock poses and Boltz-2 complexes, per candidate, as tracked jobs."
            />
            <Fact
              term="Screening"
              detail="RDKit descriptors on a capped oligomer; nvMolKit for fingerprints when a GPU is present."
            />
            <Fact
              term="Every figure"
              detail="Carries an evidence tier and a run manifest. None of them is a measurement."
            />
          </dl>

          <p className="max-w-md border-t border-hairline pt-5 text-2xs leading-relaxed text-muted">
            Research prototype. Nothing here is a validated prediction of drug stability, shelf
            life or clinical performance, and no laboratory assay is connected — so the platform
            never returns a measured value.
          </p>
        </div>
      </aside>
    </div>
  );
}

function Fact({ term, detail }: { term: string; detail: string }) {
  return (
    <div>
      <dt className="text-2xs uppercase tracking-wider text-accent">{term}</dt>
      <dd className="mt-1.5 text-2xs leading-relaxed text-muted">{detail}</dd>
    </div>
  );
}

/**
 * A drifting backbone, purely decorative.
 *
 * It depicts no particular chemistry — repeat units as beads on a chain — and
 * it is marked `aria-hidden` so it is never read as data. The app does not
 * draw structures it has not computed.
 */
function ChainField() {
  const rows = [0, 1, 2, 3, 4, 5];
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 opacity-[0.5]">
      {rows.map((row) => (
        <svg
          key={row}
          className="drift absolute left-0 w-[160%]"
          style={{
            top: `${8 + row * 17}%`,
            animationDuration: `${11 + row * 2.5}s`,
            animationDirection: row % 2 ? "reverse" : "normal",
          }}
          height="28"
          viewBox="0 0 480 28"
          fill="none"
        >
          <path
            d="M0 14h480"
            stroke="var(--gridline)"
            strokeWidth="1"
            strokeDasharray="3 7"
          />
          {Array.from({ length: 10 }).map((_, index) => (
            <circle
              key={index}
              cx={24 + index * 48}
              cy={14}
              r={row % 3 === 0 ? 3.4 : 2.4}
              fill="var(--surface-1)"
              stroke={row % 2 ? "var(--seq-500)" : "var(--seq-400)"}
              strokeWidth="1.1"
            />
          ))}
        </svg>
      ))}
    </div>
  );
}

function ChainMark() {
  return (
    <svg aria-hidden width="20" height="20" viewBox="0 0 20 20">
      <path d="M2 10h4M8 10h4M14 10h4" stroke="var(--accent)" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="7" cy="10" r="1.9" fill="var(--surface-1)" stroke="var(--accent)" strokeWidth="1.2" />
      <circle cx="13" cy="10" r="1.9" fill="var(--surface-1)" stroke="var(--accent)" strokeWidth="1.2" />
      <circle cx="2" cy="10" r="1" fill="var(--text-muted)" />
      <circle cx="18" cy="10" r="1" fill="var(--text-muted)" />
    </svg>
  );
}
