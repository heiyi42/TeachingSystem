import type { LearningAttempt, LearningRow } from "./learningTypes";

export interface SchoolUser {
  id: string;
  username: string;
  name: string;
  role: "admin" | "teacher" | "student";
}

export interface SchoolClass {
  id: string;
  title: string;
  course_id: string;
  teacher_id: string;
  join_code?: string;
}

export interface ContentVersion {
  item_key: string;
  version: number;
  category: "training" | "qa";
  status: "draft" | "approved" | "rejected" | "published" | "withdrawn";
  source: string;
  data: Record<string, unknown>;
  review_note: string;
  reviewer_id: string | null;
}

export interface ContentDetail {
  templates: Array<Record<string, unknown>>;
  versions: ContentVersion[];
  solutions: Record<string, unknown>;
  audit: Array<{
    id: number;
    action: string;
    actor_id: string;
    actor_name: string | null;
    created_at: number;
    detail: string;
  }>;
}

export interface SchoolAttempt extends LearningAttempt {
  submissions: Array<{
    rows: LearningRow[];
    evaluation: LearningAttempt["evaluation"];
    created_at: number;
    unassisted: boolean;
  }>;
}

export interface SchoolTask {
  student_progress?: { status: "not_started" | "in_progress" | "finalized" | "ended"; submitted: number; total: number; late: boolean };
  id: string;
  lesson_plan_id?: string;
  lesson_plan_revision?: number;
  class_id: string;
  title: string;
  kind: "homework" | "quiz" | "experiment";
  instructions: string;
  rubric: string;
  due_at: number;
  late_until: number | null;
  max_submissions: number;
  status: "draft" | "published" | "closed";
  exercises: import("./learningTypes").LearningExercise[];
}

export interface TaskDetail {
  task: SchoolTask;
  feedback_hidden?: boolean;
  submission: null | {
    status: "draft" | "finalized";
    report: string;
    finalized_at?: number;
    automatic?: boolean;
    late?: boolean;
    score?: number | null;
    training_score?: number | null;
    reviews: Array<{
      teacher_name: string;
      created_at: number;
      score: number | null;
      comment: string;
    }>;
  };
  attempts: SchoolAttempt[];
  files: Array<{ id: string; name: string; size: number; sha256: string }>;
}

export interface ReportCounts {
  expected_answers: number;
  started: number;
  submitted: number;
  passed: number;
  independent: number;
  assisted: number;
  corrected: number;
  repeated: number;
  unclassified_pass: number;
  unpassed: number;
  unsubmitted: number;
}

export interface ReportError {
  id: string;
  chapter_id: string;
  chapter_title: string;
  code: string;
  label: string;
  student_count: number;
  current_student_count: number;
  occurrences: number;
  teaching_advice: string;
  practice_advice: string;
  exercises: Array<{ id: string; title: string; content_version: number }>;
  evidence: Array<{
    task_id: string; task_title: string;
    student_id: string; student_name: string;
    attempt_id: string; exercise_id: string; exercise_title: string;
    content_version: number; submission_number: number; created_at: number;
    step: number | null; field: string | null; message: string;
  }>;
}

export interface ClassReport {
  class_id: string;
  course_id: string;
  course_name: string;
  chapter_id: string | null;
  chapter_options: Array<{id: string; title: string}>;
  common_errors: ReportError[];
  totals: ReportCounts;
  chapters: Array<ReportCounts & {
    id: string;
    title: string;
    assigned_questions: number;
  }>;
  generated_at: number;
  member_count: number;
  task_count: number;
  expected_submissions: number;
  finalized: number;
  rows: Array<ReportCounts & {
    task_id: string;
    task_title: string;
    kind: SchoolTask["kind"];
    student_id: string;
    student_name: string;
    status: "finalized" | "started" | "not_started";
    score: number | null;
    late: boolean;
    hints: number;
    solutions: number;
    corrections: number;
    errors: Record<string, number>;
    pending_review: boolean;
  }>;
  questions: Array<ReportCounts & {
    task_id: string;
    task_title: string;
    exercise_id: string;
    title: string;
    chapter_id: string;
    chapter_title: string;
    content_version: number;
  }>;
}

export interface LessonPlan {
  id: string | null;
  revision: number;
  class_id: string;
  chapter_id: string;
  chapter_title: string;
  title: string;
  minutes: number;
  focus: string;
  teacher_notes: string;
  stages: Array<{ title: string; minutes: number; content: string }>;
  classroom_ids: string[];
  homework_ids: string[];
  exercises: Array<{ id: string; title: string; content_version: number }>;
  gaps: string[];
  material_available: boolean;
  baseline: {
    generated_at: number;
    member_count: number;
    counts: (ReportCounts & { id: string; title: string }) | null;
    errors: ReportError[];
  };
  updated_at?: number;
}

export interface LessonEffects {
  generated_at: number;
  tasks: Array<{ id: string; title: string; status: SchoolTask["status"]; plan_revision: number; counts: ReportCounts }>;
}
