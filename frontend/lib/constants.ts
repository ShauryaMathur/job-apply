export const STATUS_OPTIONS = [
  "new",
  "reviewed",
  "applying",
  "applied",
  "rejected",
  "interview",
  "offer",
] as const;

export type JobStatus = (typeof STATUS_OPTIONS)[number];

export const CATEGORY_LABELS: Record<string, string> = {
  backend: "Backend",
  fullstack: "Full Stack",
  aiml: "AI/ML",
};

export const STATUS_VARIANT: Record<
  string,
  "default" | "secondary" | "success" | "warning" | "destructive" | "info" | "purple"
> = {
  new: "secondary",
  reviewed: "info",
  applying: "info",
  applied: "success",
  rejected: "destructive",
  interview: "warning",
  offer: "purple",
};

export const CATEGORY_VARIANT: Record<
  string,
  "default" | "info" | "success" | "purple"
> = {
  backend: "info",
  fullstack: "success",
  aiml: "purple",
};
