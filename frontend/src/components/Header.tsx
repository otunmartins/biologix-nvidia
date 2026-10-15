"use client";

import { useEffect, useState } from "react";
import type { Config, StatusPayload } from "@/lib/types";

function Chip({ label, value, tone = "normal" }: { label: string; value: string; tone?: "normal" | "warn" }) {
  return (
    <span className="inline-flex items-baseline gap-1.5 rounded border border-hairline bg-surface-2 px-2 py-1">
      <span className="text-2xs uppercase tracking-wider text-muted">{label}</span>
      <span className={`font-mono text-2xs ${tone === "warn" ? "text-state-warn" : "text-ink-2"}`}>
        {value}
      </span>
    </span>
  );
}

export function Header({
  data,
  refetching,
  onRefresh,
}: {
  data: StatusPayload | null;
  refetching: boolean;
  onRefresh: () => void;
}) {
  const [clock, setClock] = useState<string>("");

  useEffect(() => {
    const tick = () => setClock(new Date().toISOString().slice(11, 19) + "Z");
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  const config = data?.config as Config | undefined;
  const sim = data?.availability?.simulation;
  const keyState = config?.nvidia_api_key === "<set>" ? "set" : "unset";

  return (
    <header className="flex flex-col gap-4 border-b border-hairline pb-5 md:flex-row md:items-end md:justify-between">
      <div>
        <div className="flex items-center gap-2.5">
          <span
            aria-hidden
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: data?.ok ? "var(--state-good)" : "var(--state-crit)" }}
          />
          <h1 className="text-xl font-semibold text-ink">Platform</h1>
        </div>
        <p className="mt-2 max-w-2xl text-xs leading-relaxed text-muted">
          What can actually run from this machine. &ldquo;Configured&rdquo; means credentialled
          and pointed somewhere; &ldquo;verified live&rdquo; means it answered a real call. The
          gap between those two claims is what this page exists to show.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Chip label="api key" value={keyState} tone={keyState === "set" ? "normal" : "warn"} />
        <Chip
          label="gpu"
          value={sim?.cuda_available ? "cuda" : "none"}
          tone={sim?.cuda_available ? "normal" : "warn"}
        />
        <Chip label="sim" value={String(sim?.platform ?? sim?.backend ?? "—")} />
        <Chip label="utc" value={clock || "—:—:—"} />
        <button
          type="button"
          onClick={onRefresh}
          disabled={refetching}
          className="rounded border border-hairline bg-surface-2 px-2.5 py-1 text-2xs uppercase tracking-wider text-ink-2 transition-colors hover:border-accent hover:text-ink disabled:opacity-50"
        >
          {refetching ? "polling…" : "refresh"}
        </button>
      </div>
    </header>
  );
}
