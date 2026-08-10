/**
 * API client for the job-apply backend.
 * All requests go through Next.js rewrites → /api/* → backend.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL
  ? `${process.env.NEXT_PUBLIC_API_URL}/api`
  : "/api";

export interface Job {
  id: string;
  job_id: string;
  title: string;
  company: string;
  location: string | null;
  link: string;
  description: string | null;
  role_category: string;
  source: string;
  posted_at: string | null;
  scraped_at: string | null;
  match_score: number | null;
  h1b_likely: boolean | null;
  h1b_notes: string | null;
  status: string;
  resume_file: string | null;
  cover_letter_file: string | null;
  email_file: string | null;
  s3_resume_url: string | null;
  s3_cover_letter_url: string | null;
  latex_content: string | null;
  deleted_at: string | null;
  notes: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface JobListResponse {
  total: number;
  jobs: Job[];
}

export interface StatsResponse {
  total_jobs: number;
  by_category: Record<string, number>;
  by_status: Record<string, number>;
  h1b_likely_count: number;
  resumes_generated: number;
  applied_count: number;
  interview_count: number;
}

export interface PipelineRun {
  id: string;
  started_at: string;
  completed_at: string | null;
  status: string;
  jobs_found: number;
  jobs_ranked: number;
  resumes_generated: number;
  error: string | null;
}

export interface PipelineTriggerResponse {
  run_id: string;
  message: string;
}

export interface RoleConfig {
  enabled: boolean;
  count: number;
}

export interface PipelineTriggerRequest {
  source?: "jobright" | "indeed" | null;
  hours_old?: number | null;
  roles?: Record<string, RoleConfig> | null;
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export async function fetchJobs(params?: {
  category?: string;
  status?: string;
  h1b_likely?: boolean;
  min_score?: number;
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<JobListResponse> {
  const query = new URLSearchParams();
  if (params?.category) query.set("category", params.category);
  if (params?.status) query.set("status", params.status);
  if (params?.h1b_likely !== undefined)
    query.set("h1b_likely", String(params.h1b_likely));
  if (params?.min_score !== undefined)
    query.set("min_score", String(params.min_score));
  if (params?.search) query.set("search", params.search);
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  if (params?.offset !== undefined) query.set("offset", String(params.offset));

  const url = `${API_BASE}/jobs${query.toString() ? `?${query}` : ""}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch jobs: ${res.statusText}`);
  return res.json();
}

export async function fetchStats(): Promise<StatsResponse> {
  const res = await fetch(`${API_BASE}/jobs/stats`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch stats: ${res.statusText}`);
  return res.json();
}

export async function updateJob(
  jobId: string,
  data: { status?: string; notes?: string; title?: string; company?: string }
): Promise<Job> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to update job: ${res.statusText}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Pipeline
// ---------------------------------------------------------------------------

export async function triggerPipeline(
  payload?: PipelineTriggerRequest
): Promise<PipelineTriggerResponse> {
  const res = await fetch(`${API_BASE}/pipeline/trigger`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
  });
  if (!res.ok) throw new Error(`Failed to trigger pipeline: ${res.statusText}`);
  return res.json();
}

export async function fetchPipelineRuns(): Promise<{ runs: PipelineRun[] }> {
  const res = await fetch(`${API_BASE}/pipeline/runs`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch runs: ${res.statusText}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Document download URLs
// ---------------------------------------------------------------------------

export function resumeDownloadUrl(jobId: string): string {
  return `${API_BASE}/jobs/${jobId}/resume`;
}

export function coverLetterDownloadUrl(jobId: string): string {
  return `${API_BASE}/jobs/${jobId}/cover-letter`;
}

export function emailDownloadUrl(jobId: string): string {
  return `${API_BASE}/jobs/${jobId}/email`;
}

// ---------------------------------------------------------------------------
// Apply Tool — job URL ingestion + LaTeX compilation
// ---------------------------------------------------------------------------

export interface JobInfo {
  title: string;
  company: string;
  description: string;
  h1b_likely: boolean | null;
  seniority: string | null;
  key_skills: string[];
}

export interface IngestUrlResponse {
  job_id: string;
  job_info: JobInfo;
  latex: string;
  pdf_base64: string | null;
  compile_error: string | null;
}

export interface CompileLatexResponse {
  pdf_base64: string;
  size: number;
}

export async function ingestJobUrl(
  url: string,
  roleCategory: string,
  description?: string
): Promise<IngestUrlResponse> {
  const res = await fetch(`${API_BASE}/tools/ingest-url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, role_category: roleCategory, ...(description ? { description } : {}) }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Ingest failed: ${res.status}`);
  }
  return res.json();
}

export async function compileLatex(
  texContent: string
): Promise<CompileLatexResponse> {
  const res = await fetch(`${API_BASE}/tools/compile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tex_content: texContent }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Compile failed: ${res.status}`);
  }
  return res.json();
}

export async function cancelPipeline(runId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/pipeline/${runId}/cancel`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Cancel failed: ${res.status}`);
  }
}

export async function generateResume(jobId: string): Promise<Job> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}/generate/resume`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Resume generation failed: ${res.status}`);
  }
  return res.json();
}

export async function generateCoverLetter(jobId: string): Promise<Job> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}/generate/cover-letter`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Cover letter generation failed: ${res.status}`);
  }
  return res.json();
}

export async function deleteJob(jobId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}`, { method: "DELETE" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Delete failed: ${res.status}`);
  }
}

export async function saveLatex(jobId: string, latexContent: string): Promise<Job> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ latex_content: latexContent }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Save failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchJob(jobId: string): Promise<Job> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch job: ${res.statusText}`);
  return res.json();
}

export async function fetchJobsWithLatex(): Promise<Job[]> {
  const res = await fetch(`${API_BASE}/jobs?has_latex=true&limit=200`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch jobs: ${res.statusText}`);
  const data: JobListResponse = await res.json();
  return data.jobs;
}
