"use client";

import { useState } from "react";
import { shortName } from "@/lib/state";
import type { LiveResult } from "@/lib/types";

/**
 * Measured round-trip of the last live call, per component.
 *
 * One series, so no legend box: the title says what is plotted. Magnitude is
 * the job, so the colour is a single sequential hue rather than one hue per
 * bar — a per-bar palette would double-encode bar length as colour and spend
 * the only free channel on information the length already carries.
 *
 * Only components that answered appear here. A failed call has no duration
 * worth plotting, and drawing it as a zero-length bar would read as "fast".
 * The table below carries every component, including those.
 */
export function LatencyChart({ results }: { results: LiveResult[] }) {
  const [hover, setHover] = useState<string | null>(null);

  const rows = results
    .filter((r) => r.ok && typeof r.seconds === "number")
    .sort((a, b) => (b.seconds ?? 0) - (a.seconds ?? 0));

  if (rows.length === 0) {
    return (
      <p className="text-xs text-muted">
        No component has returned a timed call yet. Run the live check to populate this.
      </p>
    );
  }

  const peak = Math.max(...rows.map((r) => r.seconds ?? 0));
  const max = Math.max(0.5, Math.ceil(peak * 1.25 * 2) / 2);
  const ticks = [0, max / 2, max];

  return (
    <div className="relative">
      <div className="space-y-2.5">
        {rows.map((r) => {
          const seconds = r.seconds ?? 0;
          const pct = (seconds / max) * 100;
          const isHover = hover === r.adapter;
          return (
            <div
              key={r.adapter}
              tabIndex={0}
              onMouseEnter={() => setHover(r.adapter)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(r.adapter)}
              onBlur={() => setHover(null)}
              className="group flex items-center gap-3 rounded py-1"
              style={{ minHeight: 26 }}
            >
              <span className="w-[76px] shrink-0 text-right font-mono text-2xs text-ink-2">
                {shortName(r.adapter)}
              </span>
              <div className="relative h-3.5 flex-1">
                {/* Gridlines: solid hairlines, one step off the surface. */}
                {ticks.map((t) => (
                  <span
                    key={t}
                    aria-hidden
                    className="absolute top-0 h-full w-px"
                    style={{ left: `${(t / max) * 100}%`, background: "var(--gridline)" }}
                  />
                ))}
                <span
                  aria-hidden
                  className="absolute left-0 top-0 h-full w-px"
                  style={{ background: "var(--baseline)" }}
                />
                {/* 4px rounded data-end, square at the baseline. */}
                <span
                  className="absolute left-0 top-0 h-full"
                  style={{
                    width: `${pct}%`,
                    background: isHover ? "var(--seq-400)" : "var(--seq-500)",
                    borderTopRightRadius: 4,
                    borderBottomRightRadius: 4,
                    transition: "background 120ms ease-out",
                  }}
                />
              </div>
              <span className="w-[58px] shrink-0 tabular text-right text-xs text-ink">
                {seconds.toFixed(2)}s
              </span>
            </div>
          );
        })}
      </div>

      <div className="mt-3 flex items-center gap-3 border-t border-hairline pt-2">
        <span className="w-[76px] shrink-0" />
        <div className="relative h-4 flex-1">
          {ticks.map((t) => (
            <span
              key={t}
              className="absolute top-0 tabular text-2xs text-muted"
              style={{ left: `${(t / max) * 100}%`, transform: "translateX(-50%)" }}
            >
              {t === 0 ? "0" : `${t.toFixed(1)}s`}
            </span>
          ))}
        </div>
        <span className="w-[58px] shrink-0" />
      </div>

      {hover && (
        <HoverCard result={rows.find((r) => r.adapter === hover)!} />
      )}
    </div>
  );
}

function HoverCard({ result }: { result: LiveResult }) {
  return (
    <div className="mt-3 rounded border border-hairline bg-surface-2 p-3">
      <p className="font-mono text-2xs text-ink">{result.endpoint}</p>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-2xs sm:grid-cols-4">
        <div>
          <dt className="text-muted">model</dt>
          <dd className="font-mono text-ink-2">{result.model}</dd>
        </div>
        <div>
          <dt className="text-muted">round trip</dt>
          <dd className="tabular text-ink-2">{result.seconds?.toFixed(2)} s</dd>
        </div>
        {result.response_keys && (
          <div className="col-span-2">
            <dt className="text-muted">response keys</dt>
            <dd className="font-mono text-ink-2">{result.response_keys.join(", ")}</dd>
          </div>
        )}
        {result.unexpected_keys && result.unexpected_keys.length > 0 && (
          <div className="col-span-2 sm:col-span-4">
            <dt className="text-state-warn">keys the parser did not expect</dt>
            <dd className="font-mono text-ink-2">{result.unexpected_keys.join(", ")}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}
