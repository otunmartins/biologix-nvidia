import Link from "next/link";
import { notFound } from "next/navigation";
import { LaunchCampaign } from "@/components/campaign/LaunchCampaign";
import { Alert, Chip, Panel, Stat } from "@/components/ui/Primitives";
import { JobChip } from "@/components/ui/Status";
import { ActivityFeed } from "@/components/workspace/ActivityFeed";
import { CampaignTable } from "@/components/workspace/CampaignTable";
import { Shortlist } from "@/components/workspace/Shortlist";
import { isFailure, serverFetch } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import type { Availability, Options, ProgramDetail } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ProgramPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [program, options, availability] = await Promise.all([
    serverFetch<ProgramDetail>(`/projects/${id}`),
    serverFetch<Options>("/options"),
    serverFetch<Availability>("/availability"),
  ]);

  if (isFailure(program)) {
    if (program.status === 404) notFound();
    return <Alert title="could not load this program">{program.error}</Alert>;
  }

  // A generator whose adapter cannot reach anything right now is still
  // offered, with its reason, so the choice is informed rather than hidden.
  const unavailable: Record<string, string | null> = {};
  if (!isFailure(availability)) {
    for (const adapter of availability.adapters) {
      const name = adapter.adapter.replace(/^bionemo-/, "");
      if (!isFailure(options) && options.generators.includes(name) && !adapter.available) {
        unavailable[name] = adapter.reason;
      }
    }
  }

  const target = program.target;
  const liveJobs = program.jobs.filter(
    (job) => job.status === "queued" || job.status === "running",
  );

  return (
    <div className="space-y-6">
      <header className="border-b border-hairline pb-6">
        <nav className="flex items-center gap-2 text-2xs uppercase tracking-wider text-muted">
          <Link href="/programs" className="hover:text-accent">
            programs
          </Link>
          <span aria-hidden>/</span>
          <span className="text-ink-2">{program.target_name ?? program.target_key}</span>
        </nav>

        <div className="mt-3 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <h1 className="text-xl font-semibold text-ink">{program.name}</h1>
            {program.description && (
              <p className="mt-2 max-w-2xl text-xs leading-relaxed text-ink-2">
                {program.description}
              </p>
            )}
            <p className="mt-2 text-2xs text-muted">
              opened {formatRelative(program.created_at)}
              {program.is_owner ? " · you own this program" : ""}
            </p>
          </div>

          {target && (
            <div className="flex flex-wrap items-center gap-2">
              <Chip label="target" value={target.name} />
              <Chip label="class" value={target.class} />
              <Chip
                label="chains"
                value={`${target.n_chains} · ${target.total_residues} res`}
              />
              {target.pdb_id && <Chip label="pdb" value={target.pdb_id} />}
            </div>
          )}
        </div>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Campaigns" value={program.campaigns.length} />
        <Stat
          label="Shortlisted"
          value={program.shortlist.length}
          tone={program.shortlist.length > 0 ? "good" : "neutral"}
          context="Each with the reason its decider gave."
        />
        <Stat
          label="Analyses"
          value={program.jobs.length}
          context="DiffDock and Boltz-2 jobs run on this program's candidates."
        />
        <Stat
          label="In flight"
          value={liveJobs.length + program.campaigns.filter((c) => c.status === "running").length}
          tone="accent"
          context="Work the server is doing right now."
        />
      </div>

      {target && target.chains.length > 1 && (
        <Panel
          title="The target"
          subtitle="Chains are modelled separately and never concatenated: a two-chain biologic held by disulfides is a different problem from one long sequence."
        >
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {target.chains.map((chain) => (
              <li key={chain.chain_id} className="rounded-lg border border-hairline bg-surface-2 p-3">
                <p className="font-mono text-xs text-ink">chain {chain.chain_id}</p>
                <p className="tabular mt-1 text-2xs text-muted">{chain.length} residues</p>
                {chain.description && (
                  <p className="mt-1.5 text-2xs leading-relaxed text-muted">{chain.description}</p>
                )}
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {!isFailure(options) && (
        <Panel
          title="New campaign"
          subtitle={`Every campaign in this program screens against ${program.target_name ?? program.target_key}. The target cannot be changed here.`}
        >
          <LaunchCampaign
            options={options}
            projectId={program.id}
            targetName={program.target_name ?? program.target_key}
            unavailable={unavailable}
          />
        </Panel>
      )}

      <Panel
        title="Campaigns"
        subtitle="Kept counts candidates that passed every hard constraint. Discarded counts proposals that never became a candidate — each keeps its reason."
      >
        <CampaignTable initial={program.campaigns} projectId={program.id} />
      </Panel>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Panel
          title="Shortlist"
          subtitle="The program's output: candidates a person put their name to. Nothing here has been measured in a laboratory."
        >
          <Shortlist entries={program.shortlist} />
        </Panel>

        <div className="space-y-5">
          {program.jobs.length > 0 && (
            <Panel title="Analyses" subtitle="Newest first, across this program's candidates.">
              <ul className="space-y-2">
                {program.jobs.slice(0, 8).map((job) => (
                  <li key={job.id} className="flex items-center justify-between gap-3">
                    <JobChip job={job} />
                    {job.candidate_id && job.run_id ? (
                      <Link
                        href={`/campaigns/${job.run_id}/candidates/${job.candidate_id}`}
                        className="truncate font-mono text-2xs text-muted hover:text-accent"
                      >
                        {job.candidate_id.slice(0, 8)}
                      </Link>
                    ) : (
                      <span className="font-mono text-2xs text-muted">—</span>
                    )}
                    <span className="shrink-0 text-2xs text-muted">
                      {formatRelative(job.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          <Panel title="Program audit trail">
            <ActivityFeed
              activity={program.activity}
              emptyTitle="Nothing recorded in this program yet."
            />
          </Panel>
        </div>
      </div>
    </div>
  );
}
