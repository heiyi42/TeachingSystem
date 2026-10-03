export type LearningSubject =
  "C_program" | "operating_systems" | "cybersec_lab";

export interface LearningCourse {
  id: LearningSubject;
  name: string;
  capabilities: string[];
  planned: string;
  exercise_count: number;
  chapters: Array<{
    id: string;
    number: number;
    title: string;
    exercise_count: number;
  }>;
}

export interface LearningExercise {
  subject_id: LearningSubject;
  subject_name: string;
  chapter_id: string;
  chapter_title: string;
  id: string;
  title: string;
  algorithm: string;
  kind:
    | "page_replacement"
    | "cpu_scheduling"
    | "c_trace"
    | "c_program"
    | "security_lab"
    | "process_states"
    | "thread_resources"
    | "file_allocation"
    | "readers_writers"
    | "pv_trace"
    | "pv_design"
    | "resource_request"
    | "schedule_metrics"
    | "disk_schedule"
    | "clock_trace"
    | "address_translation"
    | "auth_flow"
    | "signature_check"
    | "c_repair"
    | "banker"
    | "dh"
    | "access_control"
    | "log_evidence";
  difficulty: string;
  rules: string;
  training_tags: string[];
  content_version?: number;
  publication_status?: string;
  source?: string;
  parameters: {
    source_files?: string[];
    code?: string;
    checkpoints?: string[];
    description?: string;
    p?: number;
    g?: number;
    a?: number;
    b?: number;
    security_checks?: Array<{
      label: string;
      format:
        | "integer"
        | "choice"
        | "evidence"
        | "text"
        | "process_sequence"
        | "pv_queue"
        | "pv_operations"
        | "rw_active"
        | "rw_queue";
      options?: string[];
    }>;
    users?: Array<{ name: string; roles: string[] }>;
    grants?: Array<{ role: string; resource: string; action: string }>;
    logs?: Array<{
      id: string;
      time: string;
      user: string;
      source: string;
      outcome: string;
    }>;
    sequence?: number[];
    frames?: number;
    processes?: Array<{ name: string; arrival: number; service: number }>;
    quantum?: number | null;
    available?: number[];
    resources?: string[];
    banker_processes?: Array<{
      name: string;
      allocation: number[];
      maximum: number[];
    }>;
  };
}

export type LearningRow = Record<string, string>;

export interface LearningError {
  step: number;
  field: string;
  error_code: string;
  label: string;
  message: string;
  possible_cause: string | null;
}

export interface LearningSolution {
  security_trace?: Array<{
    checkpoint: string;
    value: string;
    explanation: string;
  }>;
  algorithm?: string;
  need?: Record<string, number[]>;
  safe?: boolean;
  safe_sequence?: string[];
  rounds?: Array<{
    process: string;
    work_before: number[];
    work_after: number[];
    allocation_released: number[];
  }>;
  c_trace?: Array<{ checkpoint: string; value: number }>;
  files?: Array<{ name: string; code: string }>;
  code?: string;
  explanation?: string;
  faults?: number;
  hits?: number;
  trace?: Array<{
    step: number;
    page: number;
    frames_after: number[];
    event: string;
    evicted: number | null;
    next_uses?: Record<string, number | null>;
    optimal_victims?: number[];
  }>;
  timeline?: Array<{ process: string; start: number; end: number }>;
  metrics?: Record<
    string,
    { completion: number; turnaround: number; waiting: number }
  >;
  average_turnaround?: number;
  average_waiting?: number;
}

export interface GradingReview {
  id: string;
  submission_number: number;
  reason: string;
  status: "pending" | "resolved";
  decision?: string;
  note?: string;
  reviewer_name?: string;
  created_at: number;
  resolved_at?: number;
}

export interface LearningAttempt {
  walkthrough?: {
    revision: number;
    submission_count: number;
    total: number;
    steps: Array<{ title: string; prediction: string; details: Record<string, unknown> }>;
  } | null;
  dialogue?: {
    revision: number;
    status: "talking" | "verifying" | "closed";
    baseline_submissions: number;
    turns: { question: string; answer?: string }[];
    hypothesis?: string;
    next_step?: string;
  } | null;
  grading_reviews?: GradingReview[];
  diagnostic?: boolean;
  review_id?: string | null;
  tutoring_viewed?: boolean;
  assignment?: {
    id: string;
    title: string;
    kind: "homework" | "quiz" | "experiment";
    due_at: number;
    late_until: number | null;
    max_submissions: number;
    feedback_hidden: boolean;
    can_edit: boolean;
    can_hint: boolean;
    can_solution: boolean;
  };
  content_version: number | string;
  class_id: string | null;
  legacy_record: boolean;
  id: string;
  parent_id: string | null;
  exercise: LearningExercise;
  created_at: number;
  updated_at: number;
  status: "in_progress" | "needs_correction" | "passed";
  draft: LearningRow[];
  draft_dirty: boolean;
  evaluation: {
    lab_environment?: {
      task: string;
      variant: number;
      inputs: Record<string, string>;
      artifacts: Array<{
        name: string;
        size: number;
        mode: string;
        base64: string;
      }>;
    };
    program_feedback?: {
      compiler: string;
      tests: Array<{
        number: number;
        input: string;
        expected: string;
        output: string;
        passed: boolean;
        status: string;
      }>;
    };
    passed: boolean;
    first_error: LearningError | null;
    row_statuses: string[];
  } | null;
  first_error: LearningError | null;
  submission_count: number;
  correction_count: number;
  hint_count: number;
  hint: { step: number; level: number; text: string } | null;
  solution_viewed: boolean;
  previously_seen: boolean;
  first_unassisted_pass: boolean;
  recommendation: { exercise_id: string; title: string; reason: string } | null;
  solution: LearningSolution | null;
}

export interface LearningRecord extends Pick<
  LearningAttempt,
  | "id"
  | "parent_id"
  | "exercise"
  | "content_version"
  | "class_id"
  | "legacy_record"
  | "created_at"
  | "status"
  | "first_error"
  | "submission_count"
  | "correction_count"
  | "hint_count"
  | "solution_viewed"
  | "first_unassisted_pass"
  | "previously_seen"
  | "assignment"
  | "review_id"
  | "tutoring_viewed"
> {
  retests: Array<{
    id: string;
    status: LearningAttempt["status"];
    independent_pass: boolean;
  }>;
}

export interface LearningProgress {
  records: LearningRecord[];
  summary: {
    attempts: number;
    submitted: number;
    first_unassisted_passes: number;
    corrected_passes: number;
    independent_retest_passes: number;
  };
  errors: Array<{ code: string; label: string; count: number }>;
}

export interface ChapterMaterial {
  id: string;
  subject_id: LearningSubject;
  title: string;
  source: string;
  sha256: string;
  start_line: number;
  end_line: number;
  total_lines: number;
  match_line: number | null;
  text: string;
}

export interface LearningQuestionContext {
  attempt_id: string;
  subject_id: LearningSubject;
  chapter_id: string;
  title: string;
  prompt: string;
  source: string;
  start_line: number;
  end_line: number;
}

export interface ReviewEntry {
  id: string;
  exercise: LearningExercise;
  first_error: LearningError;
  errors: Array<{ label: string; count: number }>;
  corrected: boolean;
  stage: number;
  due_at: number | null;
  due: boolean;
  complete: boolean;
  active_attempt_id: string | null;
  recommendation: LearningAttempt["recommendation"];
  checks: Array<{
    attempt_id: string;
    independent_pass: boolean;
    created_at: number;
  }>;
  reason: string;
}

export interface LearningObjective {
  id: string;
  text: string;
  status: "needs_work" | "evidence" | "insufficient" | "unassessed";
  point_ids: string[];
  evidence_ids: string[];
  exercise_count: number;
}

export interface LearningPrerequisite {
  id: string;
  title: string;
  readiness: "needs_work" | "evidence" | "partial" | "insufficient";
  read_at?: number | null;
  evidence_ids?: string[];
}

export interface LearningPath {
  state_rule_version: string;
  recommendations: Array<
    LearningAction & {
      point_id: string;
      point_title: string;
      subject_id: LearningSubject;
      evidence_ids: string[];
    }
  >;
  courses: Array<
    Omit<LearningCourse, "chapters"> & {
      read_chapters: number;
      independent_chapters: number;
      chapters: Array<
        LearningCourse["chapters"][number] & {
          read_at: number | null;
          material_available: boolean;
          independent_points: number;
          point_count: number;
          goal: string;
          objectives: LearningObjective[];
          prerequisites: LearningPrerequisite[];
          prerequisite_path: string[];
          readiness: LearningPrerequisite["readiness"];
        }
      >;
    }
  >;
  points: Array<{
    id: string;
    title: string;
    keywords: string[];
    subject_id: LearningSubject;
    chapter_id: string;
    status: string;
    learning_state: KnowledgeLearningState;
    actions: LearningAction[];
    followups: LearningFollowup[];
    guidance: LearningGuidance | null;
    guidance_blocked: boolean;
    available_count: number;
    submitted_count: number;
    independent_count: number;
    evidence_ids: string[];
    prerequisites: LearningPrerequisite[];
    prerequisite_gaps: LearningPrerequisite[];
    objectives: LearningObjective[];
    exercise_id: string | null;
    reason: string;
  }>;
  reviews: ReviewEntry[];
  summary: {
    due_reviews: number;
    pending_reviews: number;
    completed_reviews: number;
  };
  notice: string;
}

export interface LearningFollowup {
  id: string;
  action: LearningAction;
  created_at: number;
  completed_at: number | null;
  attempt_id: string | null;
  before: {
    status: string;
    independent_count: number;
    pending_review_count: number;
    verified_review_count: number;
  };
  after: LearningFollowup["before"];
  validation: {
    baseline: { attempts: number; passed: number };
    followup: { attempts: number; passed: number };
    retention: "awaiting_independent" | "awaiting_delayed" | "observed" | "needs_work";
    retention_label: string;
    delayed_evidence_ids: string[];
    other_recommendations: number;
    checks: Array<{attempt_id: string; submission_number: number; created_at: number; independent: boolean; passed: boolean; delayed: boolean}>;
    notice: string;
  };
  outcome: string;
  label: string;
  reason: string;
  repeated_errors: string[];
  evidence_ids: string[];
}

export interface LearningAction {
  token: string;
  kind: "material" | "practice" | "continue" | "review" | "explain";
  title: string;
  reason: string;
  priority: number;
  point_id: string;
  chapter_id?: string;
  query?: string;
  exercise_id?: string;
  attempt_id?: string;
}

export interface LearningGuidance {
  point_id: string;
  content: string;
  created_at: number;
  evidence_ids: string[];
  stale?: boolean;
  source: {
    id: string;
    source: string;
    sha256: string;
    start_line: number;
    end_line: number;
  };
}

export interface KnowledgeLearningState {
  rule_version: string;
  code:
    | "insufficient"
    | "in_progress"
    | "assisted"
    | "initial"
    | "independent"
    | "needs_review"
    | "verified";
  status: string;
  basis: string;
  submitted_count: number;
  independent_count: number;
  non_independent_pass_count: number;
  in_progress_count: number;
  pending_review_count: number;
  verified_review_count: number;
  last_activity_at: number | null;
  last_independent_at: number | null;
  errors: Array<{
    code: string;
    label: string;
    count: number;
    attempt_count: number;
    last_seen_at: number;
    occurrences: Array<{
      attempt_id: string;
      submission_number: number;
      created_at: number;
      step: number;
      message: string;
    }>;
  }>;
  evidence: Array<{
    attempt_id: string;
    exercise_id: string;
    title: string;
    content_version: number | string;
    grading_version: string;
    created_at: number;
    updated_at: number;
    status: LearningAttempt["status"];
    review_id: string | null;
    parent_id: string | null;
    activity: {
      hint_count: number;
      solution_viewed: boolean;
      tutoring_viewed: boolean;
    };
    submissions: Array<{
      number: number;
      created_at: number;
      outcome:
        | "incorrect"
        | "independent_pass"
        | "corrected_pass"
        | "non_independent_pass";
      rows: LearningRow[];
      assistance: {
        hint_count: number;
        solution_viewed: boolean;
        tutoring_viewed: boolean;
        previously_seen: boolean;
      } | null;
      error: LearningError | null;
    }>;
  }>;
}

export interface TrainingMatch {
  id: string;
  title: string;
  subject_id: string;
  chapter_id: string;
  exercise_id: string | null;
  reason: string;
  objectives: string[];
}

export interface EvidenceEvent {
  id: number; attempt_id: string; title: string; kind: string;
  label: string; created_at: number; detail: string;
}
export interface Roadshow {
  kind: string; notice: string; subject_id: LearningSubject;
  students: Array<{
    name: string; outcome: string; independent_pass: boolean;
    memories: StudyPlan["memories"]; timeline: EvidenceEvent[];
    tasks: Array<{title: string; kind: string; reason: string; minutes: number}>;
  }>;
}
export interface LearningWorkflowRun {
  id: string;
  status: "running" | "done" | "failed" | "interrupted" | "stale";
  can_resume: boolean;
  error: string;
}

export interface StudyPlan {
  recommendation_checks: Array<LearningFollowup & {point_id: string; point_title: string}>;
  timeline: EvidenceEvent[];
  memories: Array<{
    attempt_id: string; point_id: string; title: string; updated_at: number;
    hypothesis: string; status: string; basis: string;
    answers: Array<{question: string; answer: string}>;
    predictions: Array<{title: string; prediction: string}>;
    followups: Array<{attempt_id: string; submission_number: number; passed: boolean; independent_new_question: boolean; error?: string}>;
  }>;
  ai_plan: { analysis: string; model: string; created_at: number } | null;
  ai_stale: boolean;
  subject_id: LearningSubject;
  profile: { minutes: number; chapter_id: string; configured: boolean };
  diagnostics: Array<{
    point_id: string;
    title: string;
    chapter_id: string;
    exercise_id: string | null;
    attempt_id: string | null;
    completed: boolean;
    status: string;
    basis: string;
    evidence_ids: string[];
    objectives: string[];
  }>;
  diagnostic_completed: number;
  blocked_points: number;
  tasks: Array<
    LearningAction & {
      point_title: string;
      ai_reason?: string;
      estimated_minutes: number;
      evidence_ids: string[];
    }
  >;
  estimated_minutes: number;
  notice: string;
}
