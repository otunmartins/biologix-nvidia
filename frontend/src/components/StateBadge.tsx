import { STATE_COLOR, STATE_FILL, STATE_GLYPH, STATE_LABEL } from "@/lib/state";
import type { State } from "@/lib/types";

/**
 * A state, carried by three channels at once: a dot, a glyph and a word.
 * Never by colour alone — two of the status hues are close enough under
 * simulated colour-vision deficiency that the glyph and the label are the
 * channels doing the real work.
 */
export function StateBadge({ state, label }: { state: State; label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span
        aria-hidden
        className="inline-block h-1.5 w-1.5 rounded-full"
        style={{ background: STATE_FILL[state] }}
      />
      <span aria-hidden className={`font-mono text-2xs leading-none ${STATE_COLOR[state]}`}>
        {STATE_GLYPH[state]}
      </span>
      <span className="text-2xs uppercase tracking-wider text-ink-2">
        {label ?? STATE_LABEL[state]}
      </span>
    </span>
  );
}

export function StateLegend() {
  const states: State[] = ["verified", "configured", "failed", "inactive"];
  return (
    <ul className="flex flex-wrap items-center gap-x-5 gap-y-2">
      {states.map((s) => (
        <li key={s}>
          <StateBadge state={s} />
        </li>
      ))}
    </ul>
  );
}
