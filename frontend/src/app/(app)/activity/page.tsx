import { Alert, Panel } from "@/components/ui/Primitives";
import { ActivityFeed } from "@/components/workspace/ActivityFeed";
import { isFailure, serverFetch } from "@/lib/api";
import type { ActivityEntry } from "@/lib/types";

export const dynamic = "force-dynamic";
export const metadata = { title: "Audit trail · Biologix" };

export default async function ActivityPage() {
  const activity = await serverFetch<{ activity: ActivityEntry[] }>("/activity?limit=200");
  if (isFailure(activity)) {
    return <Alert title="could not load the audit trail">{activity.error}</Alert>;
  }

  return (
    <div className="space-y-6">
      <header className="border-b border-hairline pb-6">
        <h1 className="text-xl font-semibold text-ink">Audit trail</h1>
        <p className="mt-2 max-w-2xl text-xs leading-relaxed text-muted">
          Every program opened, campaign started, decision recorded and analysis run, across all
          programs. Append-only: nothing in the application edits or deletes a line, because an
          audit trail that can be rewritten is not one.
        </p>
      </header>

      <Panel title="All activity" bodyClassName="p-5">
        <ActivityFeed activity={activity.activity} showProgramLink />
      </Panel>
    </div>
  );
}
