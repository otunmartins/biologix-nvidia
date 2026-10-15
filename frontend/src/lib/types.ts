/** Shapes served by the Python API. Only the fields this console reads. */

export type Adapter = {
  adapter: string;
  available: boolean;
  reason: string | null;
  endpoint?: string;
  model?: string;
  health?: { probed: boolean; reachable: boolean | null; detail?: string };
  requires_api_key?: boolean;
  api_key_present?: boolean;
  code_path_executed_in_this_build?: boolean;
  verification_note?: string;
  requires_gpu?: boolean;
};

export type Simulation = {
  backend?: string;
  platform?: string;
  cuda_available?: boolean;
  available?: boolean;
  reason?: string;
  [k: string]: unknown;
};

export type Availability = {
  adapters: Adapter[];
  simulation: Simulation;
  summary: {
    n_available: number;
    n_total: number;
    degraded_mode: boolean;
    unverified_adapters: string[];
    note: string;
    verification_note: string;
  };
};

/** One row of scripts/live_check.py's report: a real call that was made. */
export type LiveResult = {
  adapter: string;
  model: string;
  endpoint: string;
  ok: boolean;
  seconds?: number;
  error?: string;
  message?: string;
  hint?: string;
  response_keys?: string[];
  unexpected_keys?: string[];
  n_generated?: number;
  n_poses_parsed?: number;
  sample?: string[];
};

export type Verification =
  | { ran: false; note: string; results: [] }
  | {
      ran: true;
      checked_at: string;
      age_hours: number | null;
      stale: boolean;
      n_ok: number;
      n_total: number;
      source: string;
      results: LiveResult[];
      note?: string;
    };

export type Config = Record<string, unknown> & {
  nvidia_api_key?: string;
  openmm_platform?: string;
};

export type StatusPayload = {
  ok: boolean;
  fetchedAt: string;
  error?: string;
  availability?: Availability;
  verification?: Verification;
  config?: Config;
};

/**
 * The three states this console reports. The distinction between `configured`
 * and `verified` is the whole point of the page: the API's `available` flag
 * means "credentialled and pointed somewhere", which is a far weaker claim
 * than "answered a real call".
 */
export type State = "verified" | "configured" | "failed" | "inactive";

/* -- runs ----------------------------------------------------------------
 *
 * Only the fields this console reads, as with the status types above. The
 * tiering matters here too: everything below is `computed` — software output
 * reproducible from the run's manifest — and never a measurement.
 */

export type Target = {
  key: string;
  name: string;
  class: string;
  n_chains: number;
  total_residues: number;
  n_modifications: number;
  pdb_id?: string | null;
};

export type Criterion = {
  key: string;
  label: string;
  descriptor: string;
  higher_is_better: boolean;
  rationale: string;
  default_weight: number;
};

export type ConstraintSet = {
  name: string;
  description: string;
  constraints: { descriptor: string; minimum?: number | null; maximum?: number | null; rationale?: string; hard?: boolean }[];
};

export type Options = {
  generators: string[];
  analyses: JobKind[];
  max_candidates: number;
  max_poses: number;
  constraint_sets: ConstraintSet[];
  ranking_criteria: Criterion[];
  default_weights: Record<string, number>;
  note: string;
};

export type RunStatus = "queued" | "running" | "succeeded" | "failed";

export type RunSummary = {
  id: string;
  project_id: string | null;
  created_by: string | null;
  created_by_name: string | null;
  created_at: string | null;
  finished_at: string | null;
  status: RunStatus;
  stage: string | null;
  progress: number;
  error: string | null;
  label: string | null;
  target_key: string;
  target_name: string | null;
  generator: string;
  n_requested: number;
  constraint_set: string;
  seed: number | null;
  n_candidates: number;
  n_passed: number;
  n_discarded: number;
};

export type CandidateRow = {
  candidate_id: string;
  display_name: string;
  repeat_units: string[];
  degree_of_polymerisation: number;
  end_group: string;
  architecture: string;
  families: string[];
  generator: string | null;
  descriptors: Record<string, number>;
  descriptor_error: string | null;
  oligomer_smiles: string | null;
  screening_dp: number | null;
  passed: boolean;
  failures: string[];
  warnings: string[];
  monomer_flags: { monomer_class: string; concern: string; scope: string; does_not_imply: string }[];
  cohort_distance: number | null;
};

export type Discard = {
  proposed_smiles: string;
  source: string | null;
  stage: string;
  codes: string[];
  detail: string | null;
  duplicate_of: string | null;
};

export type RunDetail = RunSummary & {
  params: Record<string, unknown>;
  statistics: Record<string, number | string>;
  rejections: Record<string, number>;
  diversity: Record<string, unknown> | null;
  constraints: ConstraintSet["constraints"];
  warnings: string[];
  errors: string[];
  manifest: Record<string, unknown> | null;
  candidates: CandidateRow[];
  discards: Discard[];
  evidence: string;
  note: string;
};

/** One criterion's contribution to where a candidate sits. */
export type Contribution = {
  criterion: string;
  value: number | null;
  normalised: number | null;
  weight: number;
  contribution: number;
  degenerate?: boolean;
};

export type RankedRow = CandidateRow & {
  order_score: number;
  contributions: Contribution[];
  missing_descriptors: string[];
};

export type Ranking = {
  weights: Record<string, number>;
  criteria: Criterion[];
  degenerate_criteria: string[];
  basis: string;
  run: Pick<RunSummary, "id" | "status" | "label" | "target_key" | "target_name" | "generator">;
  n_excluded: number;
  candidates: RankedRow[];
};

/* -- people, programs, decisions -----------------------------------------
 *
 * The app's own domain, as the API serves it. Same rule as above: only the
 * fields this console reads.
 */

export type Role = "scientist" | "computational" | "reviewer";

export type User = {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  created_at: string | null;
  last_seen_at: string | null;
};

export type Program = {
  id: string;
  owner_id: string;
  name: string;
  description: string | null;
  target_key: string;
  target_name: string | null;
  created_at: string | null;
  archived: boolean;
  /** Present in listings, which carry the counts a card needs. */
  n_campaigns?: number;
  n_running?: number;
  n_candidates?: number;
  n_passed?: number;
  n_shortlisted?: number;
  last_activity?: string | null;
};

export type TargetDetail = Target & {
  chains: { chain_id: string; length: number; description?: string | null }[];
  modifications: unknown;
};

export type Verdict = "shortlisted" | "rejected";

export type DecisionRecord = {
  id: number;
  project_id: string;
  run_id: string;
  candidate_id: string;
  user_id: string;
  verdict: Verdict;
  rationale: string;
  weights_at_decision: Record<string, number> | null;
  order_score_at_decision: number | null;
  created_at: string | null;
  updated_at: string | null;
};

export type JobKind = "dock" | "affinity" | "simulate";

/** A coordinate file a job produced, described well enough to render. */
export type StructureArtifact = {
  name: string;
  kind: "target" | "pose" | "complex" | "trajectory" | "frame" | string;
  label: string;
  format: string;
  description: string;
  n_bytes: number;
  rank?: number | null;
  confidence?: number | null;
  n_frames?: number;
  picoseconds?: number;
  pdb_id?: string;
};
export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export type Job = {
  id: string;
  kind: JobKind | string;
  status: JobStatus;
  stage: string | null;
  progress: number;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  run_id: string | null;
  candidate_id: string | null;
  project_id: string | null;
  created_by: string | null;
  created_by_name: string | null;
  evidence: string;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  error_kind: string | null;
  remedy: string | null;
};

export type ActivityEntry = {
  id: number;
  project_id: string | null;
  user_id: string | null;
  actor_name: string | null;
  kind: string;
  summary: string;
  run_id: string | null;
  candidate_id: string | null;
  job_id: string | null;
  detail: Record<string, unknown>;
  created_at: string | null;
};

export type ProgramDetail = Program & {
  is_owner: boolean;
  target: TargetDetail | null;
  campaigns: RunSummary[];
  shortlist: { decision: DecisionRecord; candidate: CandidateRow }[];
  jobs: Job[];
  activity: ActivityEntry[];
};

/** A campaign row as the triage view receives it: the candidate, its place in
 *  the reader's ordering, that reader's own verdict, and its analysis jobs. */
export type TriageRow = RankedRow & {
  my_decision: DecisionRecord | null;
  jobs: Job[];
};

export type Triage = Omit<Ranking, "candidates"> & {
  candidates: TriageRow[];
  run: Ranking["run"] & { project_id: string | null };
};

export type CampaignDetail = RunDetail & {
  decisions: DecisionRecord[];
  jobs: Job[];
};

export type CandidateDetail = CandidateRow & {
  run: RunSummary | null;
  constraints: ConstraintSet["constraints"];
  jobs: Job[];
  decisions: DecisionRecord[];
  my_decision: DecisionRecord | null;
  evidence: string;
};

/* -- the target catalogue ------------------------------------------------- */

export type TargetSummary = {
  key: string;
  name: string;
  class?: string;
  biologic_class?: string;
  n_chains?: number;
  total_residues?: number;
  n_modifications?: number;
  modifications?: string[];
  pdb_id?: string | null;
  uniprot_id?: string | null;
  notes?: string | null;
  builtin?: boolean;
  source: string;
  source_detail: string;
  added_by?: string | null;
  created_at?: string | null;
  editable?: boolean;
  supports_docking?: boolean;
  capability_note?: string;
  chains?: { chain_id: string; length: number; description?: string | null }[];
};

export type TargetCatalogue = {
  targets: TargetSummary[];
  classes: string[];
  modification_kinds: string[];
  esm2_max_residues: number;
  note: string;
};

/** A parsed import, before anything is stored. */
export type ImportPreview = {
  name: string;
  chains: {
    chain_id: string;
    sequence: string;
    length: number;
    description: string | null;
  }[];
  n_chains: number;
  total_residues: number;
  source: string;
  source_detail: string;
  pdb_id: string | null;
  uniprot_id: string | null;
  notes: string | null;
  warnings: string[];
  can_dock: boolean;
  capability_note: string;
  modifications_note: string;
};
