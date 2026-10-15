"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ComponentTable } from "@/components/ComponentTable";
import { Header } from "@/components/Header";
import { LatencyChart } from "@/components/LatencyChart";
import { Panel } from "@/components/Panel";
import { Pipeline } from "@/components/Pipeline";
import { Summary } from "@/components/Summary";
import type { StatusPayload } from "@/lib/types";

const POLL_MS = 20_000;

/**
 * The console's live half. It is handed a server-rendered first paint, so the
 * page is readable before any JavaScript runs, and then keeps it current.
 */
export function Console({ initial }: { initial: StatusPayload }) {
  const [data, setData] = useState<StatusPayload | null>(initial.ok ? initial : null);
  const [error, setError] = useState<string | null>(initial.ok ? null : (initial.error ?? null));
  const [refetching, setRefetching] = useState(false);
  const mounted = useRef(false);

  const load = useCallback(async () => {
    setRefetching(true);
    try {
      const res = await fetch("/api/status", { cache: "no-store" });
      const payload = (await res.json()) as StatusPayload;
      if (payload.ok) {
        setData(payload);
        setError(null);
      } else {
        // Hold the last good render rather than blanking the page.
        setError(payload.error ?? "The status API did not answer.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the console's API route.");
    } finally {
      setRefetching(false);
      mounted.current = true;
    }
  }, []);

  useEffect(() => {
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const verification = data?.verification;
  const results = verification?.ran ? verification.results : [];

  return (
    <div>
      <Header data={data} refetching={refetching} onRefresh={() => void load()} />

      {error && (
        <div
          role="alert"
          className="mt-5 rounded-lg border border-state-crit/40 bg-surface px-4 py-3 text-xs text-ink-2"
        >
          <span className="font-mono text-state-crit">backend unreachable</span> — {error}
          {data && <span className="text-muted"> Showing the last successful read.</span>}
        </div>
      )}

      {!data && !error && <p className="mt-10 text-xs text-muted">Reading component status…</p>}

      {data && (
        <div className={`mt-6 space-y-5 ${refetching && mounted.current ? "refetching" : "settled"}`}>
          <Summary availability={data.availability} verification={verification} />

          <Panel
            title="Pipeline"
            subtitle="Every stage of the computation, in order, with what each one last did. Animated flow appears only between two stages that have both answered a real call."
          >
            <Pipeline availability={data.availability} verification={verification} />
          </Panel>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
            <Panel
              title="Round trip"
              subtitle="Measured duration of the last successful call to each hosted NIM. A component that failed has no duration to plot and appears in the table instead."
            >
              <LatencyChart results={results} />
            </Panel>

            <Panel
              title="Components"
              subtitle="The table view: every component and every value as plain text, including the ones the diagram shows as blocked."
            >
              <ComponentTable availability={data.availability} verification={verification} />
            </Panel>
          </div>

          <Panel title="Provenance">
            <dl className="grid gap-x-8 gap-y-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <dt className="text-2xs uppercase tracking-wider text-muted">Live results from</dt>
                <dd className="mt-1 font-mono text-2xs text-ink-2">
                  {verification?.ran ? verification.source : "no live check on disk"}
                </dd>
              </div>
              <div>
                <dt className="text-2xs uppercase tracking-wider text-muted">Re-run the check</dt>
                <dd className="mt-1 font-mono text-2xs text-ink-2">
                  python scripts/live_check.py --json var/live-check.json
                </dd>
              </div>
              <div>
                <dt className="text-2xs uppercase tracking-wider text-muted">Status API</dt>
                <dd className="mt-1 font-mono text-2xs text-ink-2">127.0.0.1:8080</dd>
              </div>
              <div>
                <dt className="text-2xs uppercase tracking-wider text-muted">Written record</dt>
                <dd className="mt-1 font-mono text-2xs text-ink-2">docs/VERIFICATION.md</dd>
              </div>
            </dl>
            <p className="mt-4 max-w-4xl border-t border-hairline pt-4 text-2xs leading-relaxed text-muted">
              {data.availability?.summary.verification_note}
            </p>
          </Panel>

          <p className="pb-4 text-center text-2xs text-muted">
            Polling every {POLL_MS / 1000}s · last read{" "}
            <span className="tabular">{data.fetchedAt.slice(11, 19)}Z</span>
          </p>
        </div>
      )}
    </div>
  );
}
