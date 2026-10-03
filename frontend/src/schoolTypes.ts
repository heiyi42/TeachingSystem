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
  id: string;
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

export interface ClassReport {
  chapters: Array<{
    id: string;
    title: string;
    assigned_questions: number;
    expected_answers: number;
    submitted: number;
    passed: number;
    independent: number;
  }>;
  generated_at: number;
  member_count: number;
  task_count: number;
  expected_submissions: number;
  finalized: number;
  rows: Array<{
    task_id: string;
    task_title: string;
    kind: SchoolTask["kind"];
    student_id: string;
    student_name: string;
    status: "finalized" | "started" | "not_started";
    score: number | null;
    late: boolean;
    started: number;
    submitted: number;
    passed: number;
    independent: number;
    hints: number;
    solutions: number;
    corrections: number;
    errors: Record<string, number>;
    pending_review: boolean;
  }>;
  questions: Array<{
    task_id: string;
    exercise_id: string;
    title: string;
    chapter_id: string;
    chapter_title: string;
    content_version: number;
    started: number;
    submitted: number;
    passed: number;
    independent: number;
  }>;
}
