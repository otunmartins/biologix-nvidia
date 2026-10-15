"""An in-process work queue.

A thread pool, not a broker. A single-user prototype does not need Celery or
Redis to run a screen in the background, and adding either would make "start
the server" a three-process affair. The cost is that work does not survive a
restart; :func:`biologix.store.abandon_orphaned_work` makes that visible at the
next startup instead of leaving a spinner that never finishes.

Threads rather than processes because the slow parts — NIM calls, nvMolKit,
RDKit's C++ — release the GIL, and because a thread can share the settings
object and database engine without pickling.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

from biologix.jobs.analysis import JOB_KINDS, KIND_LABEL, execute_analysis_job
from biologix.jobs.pipeline import GENERATORS, execute_run
from biologix.screening.filters import DEFAULT_CONSTRAINTS, get_constraint_set
from biologix.settings import Settings, get_settings
from biologix.store import abandon_orphaned_work, create_job, create_run
from biologix.store.workspace import log
from biologix.targets.registry import get_target

MAX_CANDIDATES = 200
MAX_POSES = 20


class JobQueue:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, self.settings.job_workers), thread_name_prefix="biologix-job"
        )
        self._futures: dict[str, Future[None]] = {}

    def recover(self) -> int:
        """Mark whatever the previous process left running as abandoned."""
        return abandon_orphaned_work(url=self.settings.database_url)

    def submit_run(
        self,
        *,
        target_key: str,
        generator: str = "baseline",
        n: int = 12,
        constraint_set: str | None = None,
        seed: int | None = None,
        label: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        actor_name: str | None = None,
    ) -> str:
        """Validate a run request, record it, and start it.

        Validation happens here, before anything is stored, so a bad request
        is a 4xx with a reason rather than a run that fails a second later.
        """
        target = get_target(target_key)  # KeyError names the known targets
        choice = generator.strip().lower()
        if choice not in GENERATORS:
            raise ValueError(f"unknown generator {generator!r}; use {', '.join(GENERATORS)}")
        if not 1 <= n <= MAX_CANDIDATES:
            raise ValueError(f"n must be between 1 and {MAX_CANDIDATES}")
        resolved_constraints = (
            get_constraint_set(constraint_set) if constraint_set else DEFAULT_CONSTRAINTS
        ).name
        resolved_seed = seed if seed is not None else self.settings.seed

        run_id = create_run(
            target_key=target_key,
            target_name=target.name,
            generator=choice,
            n_requested=n,
            constraint_set=resolved_constraints,
            seed=resolved_seed,
            label=label,
            params={"n": n, "generator": choice, "constraint_set": constraint_set},
            project_id=project_id,
            created_by=user_id,
            created_by_name=actor_name,
            url=self.settings.database_url,
        )
        log(
            kind="campaign.started",
            summary=(
                f"Campaign started: {n} candidates for {target.name} with the {choice} generator"
            ),
            project_id=project_id,
            user_id=user_id,
            actor_name=actor_name,
            run_id=run_id,
            detail={"generator": choice, "n": n, "constraint_set": resolved_constraints},
            url=self.settings.database_url,
        )
        self._futures[run_id] = self._pool.submit(
            execute_run,
            run_id,
            generator=choice,
            n=n,
            constraint_set=constraint_set,
            seed=resolved_seed,
            settings=self.settings,
        )
        return run_id

    def submit_analysis(
        self,
        *,
        kind: str,
        target_key: str,
        repeat_unit_smiles: str,
        degree_of_polymerisation: int = 10,
        num_poses: int = 5,
        production_steps: int = 6_000,
        run_id: str | None = None,
        candidate_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        actor_name: str | None = None,
    ) -> str:
        """Enqueue DiffDock or Boltz-2 for one candidate.

        The adapter's availability is deliberately *not* checked here. A job
        that is created and then fails with the adapter's own reason leaves a
        record a user can read and retry; refusing at submission time would
        leave nothing behind but an error in a toast.
        """
        if kind not in JOB_KINDS:
            raise ValueError(f"unknown analysis {kind!r}; use {' or '.join(JOB_KINDS)}")
        target = get_target(target_key)
        if not 1 <= degree_of_polymerisation <= 200:
            raise ValueError("degree of polymerisation must be between 1 and 200")
        if kind == "dock" and not 1 <= num_poses <= MAX_POSES:
            raise ValueError(f"num_poses must be between 1 and {MAX_POSES}")

        payload: dict[str, object] = {
            "target_key": target_key,
            "repeat_unit_smiles": repeat_unit_smiles,
            "degree_of_polymerisation": degree_of_polymerisation,
        }
        if kind == "dock":
            payload["num_poses"] = num_poses
        if kind == "simulate":
            if not 500 <= production_steps <= 200_000:
                raise ValueError("production_steps must be between 500 and 200000")
            payload["production_steps"] = production_steps

        job_id = create_job(
            kind=kind,
            payload=payload,
            run_id=run_id,
            candidate_id=candidate_id,
            project_id=project_id,
            created_by=user_id,
            created_by_name=actor_name,
            evidence="computed",
            url=self.settings.database_url,
        )
        log(
            kind="job.started",
            summary=f"{KIND_LABEL[kind]} requested against {target.name}",
            project_id=project_id,
            user_id=user_id,
            actor_name=actor_name,
            run_id=run_id,
            candidate_id=candidate_id,
            job_id=job_id,
            detail={"kind": kind, "dp": degree_of_polymerisation},
            url=self.settings.database_url,
        )
        self._futures[job_id] = self._pool.submit(
            execute_analysis_job,
            job_id,
            kind,
            payload,
            settings=self.settings,
            project_id=project_id,
            actor_name=actor_name,
            user_id=user_id,
        )
        return job_id

    def wait(self, run_id: str, timeout: float | None = None) -> None:
        """Block until work submitted by this queue finishes; for tests and scripts."""
        future = self._futures.get(run_id)
        if future is not None:
            future.result(timeout=timeout)

    def shutdown(self, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=not wait)


__all__ = ["MAX_CANDIDATES", "JobQueue"]
