import { notFound } from "next/navigation";
import { CampaignView } from "@/components/campaign/CampaignView";
import { Alert } from "@/components/ui/Primitives";
import { isFailure, serverFetch } from "@/lib/api";
import type { CampaignDetail, Triage } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function CampaignPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const run = await serverFetch<CampaignDetail>(`/runs/${id}`);

  if (isFailure(run)) {
    if (run.status === 404) notFound();
    return <Alert title="could not load this campaign">{run.error}</Alert>;
  }

  // A campaign that has not finished has nothing to rank yet; the client asks
  // for the ordering itself once the run reports success.
  const triage =
    run.status === "succeeded" ? await serverFetch<Triage>(`/runs/${id}/ranking`) : null;

  return (
    <CampaignView
      initialRun={run}
      initialTriage={triage && !isFailure(triage) ? triage : null}
      triageError={triage && isFailure(triage) ? triage.error : null}
    />
  );
}
