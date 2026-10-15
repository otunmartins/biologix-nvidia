import { NewProgram } from "@/components/workspace/NewProgram";
import { ProgramCards } from "@/components/workspace/ProgramCards";
import { Alert } from "@/components/ui/Primitives";
import { isFailure, serverFetch } from "@/lib/api";
import type { Program, Target } from "@/lib/types";

export const dynamic = "force-dynamic";
export const metadata = { title: "Programs · Biologix" };

export default async function ProgramsPage() {
  const [programs, targets] = await Promise.all([
    serverFetch<{ projects: Program[] }>("/projects?include_archived=true"),
    serverFetch<{ targets: Target[] }>("/targets"),
  ]);

  if (isFailure(programs)) {
    return <Alert title="could not load programs">{programs.error}</Alert>;
  }

  const active = programs.projects.filter((p) => !p.archived);
  const archived = programs.projects.filter((p) => p.archived);

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-4 border-b border-hairline pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-ink">Programs</h1>
          <p className="mt-2 max-w-2xl text-xs leading-relaxed text-muted">
            A program fixes one biologic target and collects every campaign, decision and
            analysis against it. Anyone signed in can read a program; only its owner can archive
            it.
          </p>
        </div>
        {!isFailure(targets) && <NewProgram targets={targets.targets} />}
      </header>

      <ProgramCards programs={active} />

      {archived.length > 0 && (
        <section>
          <h2 className="mb-3 text-[13px] font-semibold uppercase tracking-[0.14em] text-muted">
            Archived
          </h2>
          <div className="opacity-60">
            <ProgramCards programs={archived} />
          </div>
        </section>
      )}
    </div>
  );
}
