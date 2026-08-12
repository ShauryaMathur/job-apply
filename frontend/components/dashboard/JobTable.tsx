"use client";

import { useState, useCallback, useRef } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ExternalLink,
  PenLine,
  Mail,
  ChevronUp,
  ChevronDown,
  Search,
  Filter,
  Sparkles,
  Loader2,
  Trash2,
} from "lucide-react";
import type { Job } from "@/lib/api";
import { updateJob, generateResume, generateCoverLetter, deleteJob, resumeDownloadUrl, coverLetterDownloadUrl, emailDownloadUrl } from "@/lib/api";
import { STATUS_OPTIONS, CATEGORY_LABELS, STATUS_VARIANT, CATEGORY_VARIANT, SOURCE_LABELS } from "@/lib/constants";

interface JobTableProps {
  jobs: Job[];
  loading?: boolean;
  onJobUpdated?: (job: Job) => void;
  onJobDeleted?: (jobId: string) => void;
}

function SortIcon({ field, sortField, sortDir }: { field: keyof Job; sortField: keyof Job; sortDir: "asc" | "desc" }) {
  if (sortField !== field) return null;
  return sortDir === "asc" ? (
    <ChevronUp className="inline h-3 w-3 ml-0.5" />
  ) : (
    <ChevronDown className="inline h-3 w-3 ml-0.5" />
  );
}

function ScoreBar({ score }: { score: number | null }) {
  if (score === null || score === undefined) {
    return <span className="text-muted-foreground text-xs">—</span>;
  }
  const pct = Math.min(100, Math.max(0, score));
  const color =
    pct >= 75
      ? "bg-green-500"
      : pct >= 50
      ? "bg-yellow-500"
      : "bg-red-400";

  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-muted rounded-full overflow-hidden">
        <div
          className={`h-full ${color} rounded-full transition-all`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs font-mono">{pct.toFixed(0)}</span>
    </div>
  );
}

export function JobTable({ jobs, loading, onJobUpdated, onJobDeleted }: JobTableProps) {
  // Single loading-state map: jobId → operation name (or undefined when idle)
  const [rowLoading, setRowLoading] = useState<Record<string, string | undefined>>({});
  const setOp = (jobId: string, op: string | undefined) =>
    setRowLoading((prev) => ({ ...prev, [jobId]: op }));

  // AbortController per job for cancellable LLM operations
  const abortControllers = useRef<Record<string, AbortController>>({});
  const [editingCell, setEditingCell] = useState<{ jobId: string; field: "title" | "company" | "source" } | null>(null);
  const [editValue, setEditValue] = useState("");
  const [filterCategory, setFilterCategory] = useState<string>("all");
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [filterH1b, setFilterH1b] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortField, setSortField] = useState<keyof Job>("match_score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const handleStatusChange = useCallback(
    async (jobId: string, newStatus: string) => {
      setOp(jobId, "status");
      try {
        const updated = await updateJob(jobId, { status: newStatus });
        onJobUpdated?.(updated);
      } catch (e) {
        console.error("Failed to update job status", e);
      } finally {
        setOp(jobId, undefined);
      }
    },
    [onJobUpdated]
  );

  const handleGenerateResume = useCallback(
    async (jobId: string) => {
      const controller = new AbortController();
      abortControllers.current[jobId] = controller;
      setOp(jobId, "resume");
      try {
        const updated = await generateResume(jobId, controller.signal);
        onJobUpdated?.(updated);
      } catch (e) {
        if ((e as Error).name !== "AbortError") console.error("Failed to generate resume", e);
      } finally {
        delete abortControllers.current[jobId];
        setOp(jobId, undefined);
      }
    },
    [onJobUpdated]
  );

  const handleGenerateCoverLetter = useCallback(
    async (jobId: string) => {
      const controller = new AbortController();
      abortControllers.current[jobId] = controller;
      setOp(jobId, "coverletter");
      try {
        const updated = await generateCoverLetter(jobId, controller.signal);
        onJobUpdated?.(updated);
      } catch (e) {
        if ((e as Error).name !== "AbortError") console.error("Failed to generate cover letter", e);
      } finally {
        delete abortControllers.current[jobId];
        setOp(jobId, undefined);
      }
    },
    [onJobUpdated]
  );

  const handleCancelOp = useCallback((jobId: string) => {
    abortControllers.current[jobId]?.abort();
  }, []);

  const startCellEdit = (jobId: string, field: "title" | "company" | "source", current: string) => {
    setEditingCell({ jobId, field });
    setEditValue(current);
  };

  const commitCellEdit = useCallback(async () => {
    if (!editingCell || !editValue.trim()) { setEditingCell(null); return; }
    const trimmed = editValue.trim();
    const job = jobs.find((j) => j.job_id === editingCell.jobId);
    if (!job || trimmed === job[editingCell.field]) { setEditingCell(null); return; }
    try {
      const updated = await updateJob(editingCell.jobId, { [editingCell.field]: trimmed });
      onJobUpdated?.(updated);
    } catch (e) {
      console.error("Failed to update field", e);
    } finally {
      setEditingCell(null);
    }
  }, [editingCell, editValue, jobs, onJobUpdated]);

  const handleDelete = useCallback(
    async (jobId: string) => {
      setOp(jobId, "delete");
      try {
        await deleteJob(jobId);
        onJobDeleted?.(jobId);
      } catch (e) {
        console.error("Failed to delete job", e);
      } finally {
        setOp(jobId, undefined);
      }
    },
    [onJobDeleted]
  );

  const toggleSort = (field: keyof Job) => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  };

  // Filter and sort
  const filtered = jobs
    .filter((j) => {
      if (filterCategory !== "all" && j.role_category !== filterCategory)
        return false;
      if (filterStatus !== "all" && j.status !== filterStatus) return false;
      if (filterH1b === "yes" && !j.h1b_likely) return false;
      if (filterH1b === "no" && j.h1b_likely) return false;
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        if (
          !j.title.toLowerCase().includes(q) &&
          !j.company.toLowerCase().includes(q)
        )
          return false;
      }
      return true;
    })
    .sort((a, b) => {
      const av = a[sortField] ?? "";
      const bv = b[sortField] ?? "";
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <CardTitle className="text-base">
            Jobs{" "}
            <span className="text-muted-foreground font-normal text-sm">
              ({filtered.length} / {jobs.length})
            </span>
          </CardTitle>

          {/* Filters */}
          <div className="flex flex-wrap items-center gap-2">
            {/* Search */}
            <div className="relative">
              <Search className="absolute left-2 top-2 h-4 w-4 text-muted-foreground" />
              <input
                type="text"
                placeholder="Search title / company..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-8 pr-3 py-1.5 text-sm rounded-md border border-input bg-background focus:outline-none focus:ring-1 focus:ring-ring w-44"
              />
            </div>

            {/* Category filter */}
            <Select value={filterCategory} onValueChange={setFilterCategory}>
              <SelectTrigger className="w-32 h-8 text-xs">
                <SelectValue placeholder="Category" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Categories</SelectItem>
                <SelectItem value="backend">Backend</SelectItem>
                <SelectItem value="fullstack">Full Stack</SelectItem>
                <SelectItem value="aiml">AI/ML</SelectItem>
              </SelectContent>
            </Select>

            {/* Status filter */}
            <Select value={filterStatus} onValueChange={setFilterStatus}>
              <SelectTrigger className="w-32 h-8 text-xs">
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Statuses</SelectItem>
                {STATUS_OPTIONS.map((s) => (
                  <SelectItem key={s} value={s}>
                    {s.charAt(0).toUpperCase() + s.slice(1)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* H1B filter */}
            <Select value={filterH1b} onValueChange={setFilterH1b}>
              <SelectTrigger className="w-28 h-8 text-xs">
                <SelectValue placeholder="H1B" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All H1B</SelectItem>
                <SelectItem value="yes">H1B Likely</SelectItem>
                <SelectItem value="no">H1B Unlikely</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </CardHeader>

      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/50">
                <th
                  className="text-left px-4 py-2 font-medium text-muted-foreground cursor-pointer hover:text-foreground whitespace-nowrap"
                  onClick={() => toggleSort("title")}
                >
                  Title <SortIcon field="title" sortField={sortField} sortDir={sortDir} />
                </th>
                <th
                  className="text-left px-4 py-2 font-medium text-muted-foreground cursor-pointer hover:text-foreground whitespace-nowrap"
                  onClick={() => toggleSort("company")}
                >
                  Company <SortIcon field="company" sortField={sortField} sortDir={sortDir} />
                </th>
                <th className="text-left px-4 py-2 font-medium text-muted-foreground whitespace-nowrap">
                  Source
                </th>
                <th className="text-left px-4 py-2 font-medium text-muted-foreground whitespace-nowrap">
                  Role
                </th>
                <th
                  className="text-left px-4 py-2 font-medium text-muted-foreground cursor-pointer hover:text-foreground whitespace-nowrap"
                  onClick={() => toggleSort("match_score")}
                >
                  Score <SortIcon field="match_score" sortField={sortField} sortDir={sortDir} />
                </th>
                <th className="text-left px-4 py-2 font-medium text-muted-foreground whitespace-nowrap">
                  H1B
                </th>
                <th className="text-left px-4 py-2 font-medium text-muted-foreground whitespace-nowrap">
                  Status
                </th>
                <th className="text-left px-4 py-2 font-medium text-muted-foreground whitespace-nowrap">
                  Resume / Docs
                </th>
                <th className="text-left px-4 py-2 font-medium text-muted-foreground whitespace-nowrap">
                  Link
                </th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td colSpan={9} className="px-4 py-8 text-center text-muted-foreground">
                    <div className="flex items-center justify-center gap-2">
                      <div className="h-4 w-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                      Loading jobs...
                    </div>
                  </td>
                </tr>
              )}
              {!loading && filtered.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-4 py-12 text-center text-muted-foreground">
                    <div className="flex flex-col items-center gap-2">
                      <Filter className="h-8 w-8 opacity-30" />
                      <p>No jobs found matching your filters.</p>
                      <p className="text-xs">
                        Run the pipeline to start discovering jobs!
                      </p>
                    </div>
                  </td>
                </tr>
              )}
              {filtered.map((job) => (
                <tr
                  key={job.job_id}
                  className="border-b border-border hover:bg-muted/30 transition-colors"
                >
                  {/* Title */}
                  <td className="px-4 py-3 max-w-[220px]">
                    {editingCell?.jobId === job.job_id && editingCell.field === "title" ? (
                      <input
                        autoFocus
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onBlur={commitCellEdit}
                        onKeyDown={(e) => { if (e.key === "Enter") commitCellEdit(); if (e.key === "Escape") setEditingCell(null); }}
                        className="font-medium text-sm bg-transparent border-b border-primary focus:outline-none w-full"
                      />
                    ) : (
                      <div
                        className="font-medium truncate cursor-pointer hover:text-primary transition-colors"
                        title="Double-click to edit title"
                        onDoubleClick={() => startCellEdit(job.job_id, "title", job.title)}
                      >
                        {job.title}
                      </div>
                    )}
                    {job.location && (
                      <div className="text-xs text-muted-foreground truncate">
                        {job.location}
                      </div>
                    )}
                  </td>

                  {/* Company */}
                  <td className="px-4 py-3 max-w-[160px]">
                    {editingCell?.jobId === job.job_id && editingCell.field === "company" ? (
                      <input
                        autoFocus
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onBlur={commitCellEdit}
                        onKeyDown={(e) => { if (e.key === "Enter") commitCellEdit(); if (e.key === "Escape") setEditingCell(null); }}
                        className="text-sm bg-transparent border-b border-primary focus:outline-none w-full"
                      />
                    ) : (
                      <span
                        className="truncate block cursor-pointer hover:text-primary transition-colors"
                        title="Double-click to edit company"
                        onDoubleClick={() => startCellEdit(job.job_id, "company", job.company)}
                      >
                        {job.company}
                      </span>
                    )}
                  </td>

                  {/* Source */}
                  <td className="px-4 py-3 max-w-[120px]">
                    {editingCell?.jobId === job.job_id && editingCell.field === "source" ? (
                      <input
                        autoFocus
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onBlur={commitCellEdit}
                        onKeyDown={(e) => { if (e.key === "Enter") commitCellEdit(); if (e.key === "Escape") setEditingCell(null); }}
                        className="text-xs bg-transparent border-b border-primary focus:outline-none w-full"
                      />
                    ) : (
                      <span
                        className="text-xs text-muted-foreground cursor-pointer hover:text-foreground transition-colors truncate block"
                        title="Double-click to edit source"
                        onDoubleClick={() => startCellEdit(job.job_id, "source", job.source || "")}
                      >
                        {SOURCE_LABELS[job.source] || job.source || "—"}
                      </span>
                    )}
                  </td>

                  {/* Category */}
                  <td className="px-4 py-3 whitespace-nowrap">
                    <Badge
                      variant={CATEGORY_VARIANT[job.role_category] || "secondary"}
                      className="text-xs"
                    >
                      {CATEGORY_LABELS[job.role_category] || job.role_category}
                    </Badge>
                  </td>

                  {/* Score */}
                  <td className="px-4 py-3 whitespace-nowrap">
                    <ScoreBar score={job.match_score} />
                  </td>

                  {/* H1B */}
                  <td className="px-4 py-3 whitespace-nowrap">
                    {job.h1b_likely === true && (
                      <Badge variant="success" className="text-xs" title={job.h1b_notes || ""}>
                        Yes
                      </Badge>
                    )}
                    {job.h1b_likely === false && (
                      <Badge variant="destructive" className="text-xs" title={job.h1b_notes || ""}>
                        No
                      </Badge>
                    )}
                    {job.h1b_likely === null && (
                      <span className="text-muted-foreground text-xs">—</span>
                    )}
                  </td>

                  {/* Status dropdown */}
                  <td className="px-4 py-3 whitespace-nowrap">
                    <Select
                      value={job.status}
                      onValueChange={(val) => handleStatusChange(job.job_id, val)}
                      disabled={rowLoading[job.job_id] === "status"}
                    >
                      <SelectTrigger className="h-7 text-xs w-28 border-0 p-0 focus:ring-0 shadow-none">
                        <Badge
                          variant={STATUS_VARIANT[job.status] || "secondary"}
                          className="text-xs cursor-pointer"
                        >
                          {rowLoading[job.job_id] === "status"
                            ? "Saving..."
                            : job.status}
                        </Badge>
                      </SelectTrigger>
                      <SelectContent>
                        {STATUS_OPTIONS.map((s) => (
                          <SelectItem key={s} value={s} className="text-xs">
                            {s.charAt(0).toUpperCase() + s.slice(1)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </td>

                  {/* Per-document generate / download */}
                  <td className="px-4 py-3 whitespace-nowrap">
                    <div className="flex items-center gap-1">
                      {/* Resume — open editor if latex exists, else generate */}
                      {rowLoading[job.job_id] === "resume" ? (
                        <div className="flex flex-col items-center">
                          <Button variant="ghost" size="icon" className="h-7 w-7" disabled>
                            <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-400" />
                          </Button>
                          <button onClick={() => handleCancelOp(job.job_id)} className="text-[10px] text-muted-foreground hover:text-destructive leading-none">cancel</button>
                        </div>
                      ) : job.latex_content ? (
                        <a href={`/editor/${job.job_id}`}>
                          <Button variant="ghost" size="icon" className="h-7 w-7" title="Open LaTeX Editor">
                            <PenLine className="h-3.5 w-3.5 text-blue-600" />
                          </Button>
                        </a>
                      ) : (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => handleGenerateResume(job.job_id)}
                          title="Generate Resume LaTeX"
                        >
                          <Sparkles className="h-3.5 w-3.5 text-blue-300" />
                        </Button>
                      )}

                      {/* Cover Letter */}
                      {(job.cover_letter_file || job.s3_cover_letter_url) ? (
                        <a href={coverLetterDownloadUrl(job.job_id)} download>
                          <Button variant="ghost" size="icon" className="h-7 w-7" title="Download Cover Letter">
                            <Mail className="h-3.5 w-3.5 text-purple-600" />
                          </Button>
                        </a>
                      ) : rowLoading[job.job_id] === "coverletter" ? (
                        <div className="flex flex-col items-center">
                          <Button variant="ghost" size="icon" className="h-7 w-7" disabled>
                            <Loader2 className="h-3.5 w-3.5 animate-spin text-purple-400" />
                          </Button>
                          <button onClick={() => handleCancelOp(job.job_id)} className="text-[10px] text-muted-foreground hover:text-destructive leading-none">cancel</button>
                        </div>
                      ) : (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => handleGenerateCoverLetter(job.job_id)}
                          title="Generate Cover Letter & Email"
                        >
                          <Sparkles className="h-3.5 w-3.5 text-purple-300" />
                        </Button>
                      )}

                      {/* Outreach Email (download only; generated alongside cover letter) */}
                      {job.email_file && (
                        <a href={emailDownloadUrl(job.job_id)} download>
                          <Button variant="ghost" size="icon" className="h-7 w-7" title="Download Outreach Email">
                            <ExternalLink className="h-3.5 w-3.5 text-green-600" />
                          </Button>
                        </a>
                      )}
                    </div>
                  </td>

                  {/* External link + delete */}
                  <td className="px-4 py-3 whitespace-nowrap">
                    <div className="flex items-center gap-1">
                      {job.link && (
                        <a href={job.link} target="_blank" rel="noopener noreferrer">
                          <Button variant="ghost" size="icon" className="h-7 w-7" title="Open job posting">
                            <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
                          </Button>
                        </a>
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-muted-foreground hover:text-destructive"
                        onClick={() => handleDelete(job.job_id)}
                        disabled={rowLoading[job.job_id] === "delete"}
                        title="Delete job"
                      >
                        {rowLoading[job.job_id] === "delete"
                          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          : <Trash2 className="h-3.5 w-3.5" />}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
