import { notFound } from "next/navigation";
import { CandidateView } from "@/components/candidate/CandidateView";
import { Alert } from "@/components/ui/Primitives";
import { isFailure, serverFetch } from "@/lib/api";
import type { Availability, CandidateDetail } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function CandidatePage({
  params,
}: {
  params: Promise<{ id: string; cid: string }>;
}) {
  const { id, cid } = await params;
  const [candidate, availability] = await Promise.all([
    serverFetch<CandidateDetail>(`/runs/${id}/candidates/${cid}`),
    serverFetch<Availability>("/availability"),
  ]);

  if (isFailure(candidate)) {
    if (candidate.status === 404) notFound();
    return <Alert title="could not load this candidate">{candidate.error}</Alert>;
  }

  // Each analysis says up front whether its adapter can reach anything right
  // now, taken from the platform's own report rather than guessed at.
  const unavailable: Record<string, string | null> = {};
  if (!isFailure(availability)) {
    for (const adapter of availability.adapters) {
      if (!adapter.available) {
        unavailable[adapter.adapter.replace(/^bionemo-/, "")] = adapter.reason;
      }
    }
  }

  return <CandidateView candidate={candidate} runId={id} unavailable={unavailable} />;
}
