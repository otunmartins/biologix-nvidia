import type { ReactNode } from "react";

/**
 * The pieces every screen is built from.
 *
 * Two rules run through all of them. A state is never carried by colour
 * alone — it always ships a glyph and a word as well, because two of the
 * status hues are close under simulated colour-vision deficiency. And an
 * evidence tier gets its own fixed encoding, used for nothing else, so
 * "computed" can never be mistaken for "good".
 */

export function Panel({
  title,
  subtitle,
  aside,
  children,
  className = "",
  bodyClassName = "p-5",
}: {
  title?: string;
  subtitle?: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section
      className={`overflow-hidden rounded-xl border border-hairline bg-surface/90 backdrop-blur-sm ${className}`}
    >
      {(title || aside) && (
        <header className="flex items-start justify-between gap-4 border-b border-hairline px-5 py-3.5">
          <div>
            {title && (
              <h2 className="text-[13px] font-semibold uppercase tracking-[0.14em] text-ink-2">
                {title}
              </h2>
            )}
            {subtitle && (
              <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">{subtitle}</p>
            )}
          </div>
          {aside && <div className="shrink-0">{aside}</div>}
        </header>
      )}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}

/** A number that leads, with one line of context and never an invented trend. */
export function Stat({
  label,
  value,
  context,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  context?: string;
  tone?: "neutral" | "good" | "warn" | "crit" | "accent";
}) {
  const color = {
    neutral: "text-ink",
    good: "text-state-good",
    warn: "text-state-warn",
    crit: "text-state-crit",
    accent: "text-accent",
  }[tone];
  return (
    <div className="rounded-xl border border-hairline bg-surface px-4 py-3.5">
      <p className="text-2xs uppercase tracking-wider text-muted">{label}</p>
      <p className={`mt-1.5 tabular text-2xl font-semibold leading-none ${color}`}>{value}</p>
      {context && <p className="mt-2 text-2xs leading-relaxed text-muted">{context}</p>}
    </div>
  );
}

const TIER = {
  hypothesis: {
    fill: "var(--tier-hypothesis)",
    glyph: "◇",
    label: "hypothesis",
    note: "Proposed, not computed.",
  },
  computed: {
    fill: "var(--tier-computed)",
    glyph: "ƒ",
    label: "computed",
    note: "Software output, reproducible from the manifest. Not a measurement.",
  },
  measured: {
    fill: "var(--tier-measured)",
    glyph: "◆",
    label: "measured",
    note: "From a laboratory assay, with provenance.",
  },
} as const;

export type Tier = keyof typeof TIER;

/**
 * The evidence tier of whatever it sits next to.
 *
 * This is the single most load-bearing label in the product: it is what keeps
 * a docking score from being read as a measurement. It carries its own
 * explanation in the tooltip rather than relying on the reader knowing the
 * vocabulary.
 */
export function EvidenceTag({ tier, className = "" }: { tier: Tier | string; className?: string }) {
  const spec = TIER[tier as Tier] ?? TIER.computed;
  return (
    <span
      title={spec.note}
      className={`inline-flex items-center gap-1.5 rounded border border-hairline bg-surface-2 px-2 py-0.5 ${className}`}
    >
      <span aria-hidden className="font-mono text-2xs leading-none" style={{ color: spec.fill }}>
        {spec.glyph}
      </span>
      <span className="text-2xs uppercase tracking-wider text-ink-2">{spec.label}</span>
    </span>
  );
}

export function Chip({
  label,
  value,
  tone = "normal",
}: {
  label?: string;
  value: ReactNode;
  tone?: "normal" | "warn" | "crit" | "good";
}) {
  const color = {
    normal: "text-ink-2",
    good: "text-state-good",
    warn: "text-state-warn",
    crit: "text-state-crit",
  }[tone];
  return (
    <span className="inline-flex items-baseline gap-1.5 rounded border border-hairline bg-surface-2 px-2 py-1">
      {label && <span className="text-2xs uppercase tracking-wider text-muted">{label}</span>}
      <span className={`font-mono text-2xs ${color}`}>{value}</span>
    </span>
  );
}

export function Field({
  label,
  hint,
  tone = "normal",
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  tone?: "normal" | "warn";
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label
        htmlFor={htmlFor}
        className="block text-2xs uppercase tracking-wider text-muted"
      >
        {label}
      </label>
      <div className="mt-1.5">{children}</div>
      {hint && (
        <p
          className={`mt-1.5 text-2xs leading-relaxed ${
            tone === "warn" ? "text-state-warn" : "text-muted"
          }`}
        >
          {hint}
        </p>
      )}
    </div>
  );
}

export const inputClass =
  "w-full rounded-lg border border-hairline bg-surface-2 px-3 py-2 text-xs text-ink outline-none transition-colors placeholder:text-muted focus:border-accent";

export function Button({
  children,
  variant = "primary",
  className = "",
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger";
}) {
  const styles = {
    primary:
      "border-accent/60 bg-accent-soft text-ink hover:bg-accent/25 hover:border-accent",
    ghost: "border-hairline bg-surface-2 text-ink-2 hover:border-accent hover:text-ink",
    danger:
      "border-state-crit/50 bg-state-crit/10 text-ink-2 hover:border-state-crit hover:text-ink",
  }[variant];
  return (
    <button
      {...rest}
      className={`rounded-lg border px-3 py-2 text-2xs uppercase tracking-wider transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

export function Alert({
  kind = "crit",
  title,
  children,
}: {
  kind?: "crit" | "warn" | "info";
  title: string;
  children?: ReactNode;
}) {
  const border = {
    crit: "border-state-crit/40",
    warn: "border-state-warn/40",
    info: "border-accent/40",
  }[kind];
  const color = {
    crit: "text-state-crit",
    warn: "text-state-warn",
    info: "text-accent",
  }[kind];
  return (
    <div role="alert" className={`rounded-xl border ${border} bg-surface px-4 py-3 text-xs text-ink-2`}>
      <span className={`font-mono ${color}`}>{title}</span>
      {children ? <> — {children}</> : null}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-hairline bg-surface-2/40 px-5 py-8 text-center">
      <p className="text-xs text-ink-2">{title}</p>
      {children && <p className="mx-auto mt-2 max-w-md text-2xs leading-relaxed text-muted">{children}</p>}
    </div>
  );
}

/** A bar for one bounded quantity. Width is the value; nothing else encodes it. */
export function Meter({
  value,
  max = 1,
  className = "",
  color = "var(--accent)",
  label,
}: {
  value: number;
  max?: number;
  className?: string;
  color?: string;
  label?: string;
}) {
  const fraction = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  return (
    <div
      className={`h-1.5 overflow-hidden rounded-full bg-grid ${className}`}
      role="img"
      aria-label={label ?? `${Math.round(fraction * 100)}%`}
    >
      <div
        className="h-full rounded-full transition-[width] duration-500 ease-out"
        style={{ width: `${fraction * 100}%`, background: color }}
      />
    </div>
  );
}
