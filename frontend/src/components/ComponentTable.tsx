"use client";

import { STATE_LABEL, formatSeconds, liveResultFor, shortName, stateOf } from "@/lib/state";
import { StateBadge } from "@/components/StateBadge";
import type { Availability, Verification } from "@/lib/types";

/**
 * The table view: every component, every value, as plain text.
 *
 * This is the accessible twin of the diagram and the chart — nothing on this
 * page is reachable only by hovering something.
 */
export function ComponentTable({
  availability,
  verification,
}: {
  availability?: Availability;
  verification?: Verification;
}) {
  const adapters = availability?.adapters ?? [];
  if (adapters.length === 0) {
    return <p className="text-xs text-muted">No component report available.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-xs">
        <thead>
          <tr className="border-b border-hairline text-2xs uppercase tracking-wider text-muted">
            <th scope="col" className="py-2 pr-4 font-medium">Component</th>
            <th scope="col" className="py-2 pr-4 font-medium">State</th>
            <th scope="col" className="py-2 pr-4 font-medium">Configured</th>
            <th scope="col" className="py-2 pr-4 font-medium">Last live call</th>
            <th scope="col" className="py-2 pr-4 font-medium">Evidence</th>
          </tr>
        </thead>
        <tbody>
          {adapters.map((a) => {
            const live = liveResultFor(verification, a.adapter);
            const state = stateOf(a, live);
            const evidence = live
              ? live.ok
                ? `HTTP 200 · keys: ${(live.response_keys ?? []).join(", ") || "—"}`
                : `${live.error}: ${(live.message ?? "").slice(0, 120)}`
              : (a.reason ?? "never called");
            return (
              <tr
                key={a.adapter}
                className="border-b border-hairline/60 align-top transition-colors hover:bg-surface-2"
              >
                <th scope="row" className="py-2.5 pr-4 font-normal">
                  <span className="font-mono text-ink">{shortName(a.adapter)}</span>
                  {a.model && <span className="ml-2 text-2xs text-muted">{a.model}</span>}
                  {a.endpoint && (
                    <div className="mt-0.5 max-w-[22rem] truncate font-mono text-2xs text-muted">
                      {a.endpoint}
                    </div>
                  )}
                </th>
                <td className="py-2.5 pr-4">
                  <StateBadge state={state} />
                </td>
                <td className="py-2.5 pr-4 text-ink-2">
                  {a.available ? "yes" : "no"}
                  {a.requires_api_key !== undefined && (
                    <div className="text-2xs text-muted">
                      {a.requires_api_key ? "needs key" : "no key needed"}
                      {a.api_key_present ? " · key set" : ""}
                    </div>
                  )}
                </td>
                <td className="py-2.5 pr-4 tabular text-ink-2">
                  {live ? (live.ok ? formatSeconds(live.seconds) : "failed") : "—"}
                </td>
                <td className="py-2.5 pr-4 text-muted">
                  <span className="block max-w-[28rem] font-mono text-2xs leading-relaxed">
                    {evidence}
                  </span>
                  {live?.hint && (
                    <span className="mt-1 block max-w-[28rem] text-2xs leading-relaxed text-state-warn">
                      {live.hint}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-3 text-2xs leading-relaxed text-muted">
        “Configured” is the API’s own <span className="font-mono">available</span> flag: credentials
        present and an endpoint set. It is not a claim that the component works — only a state of{" "}
        <span className="text-state-good">{STATE_LABEL.verified}</span> is.
      </p>
    </div>
  );
}
