"use client";

import { useState, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Loader2,
  Download,
  RefreshCw,
  CheckCircle,
  AlertCircle,
  ArrowLeft,
  Save,
  BarChart2,
} from "lucide-react";
import {
  fetchJob,
  compileLatex,
  saveLatex,
  saveCoverLetterLatex,
  updateJob,
  rescoreJob,
  generateCoverLetter,
  type Job,
} from "@/lib/api";
import { CATEGORY_LABELS } from "@/lib/constants";
import { useLatexDocument } from "@/lib/useLatexDocument";
import { LatexEditorPane } from "@/components/editor/LatexEditorPane";

export default function EditorPage({ params }: { params: { job_id: string } }) {
  const { job_id } = params;

  const [job, setJob] = useState<Job | null>(null);
  const [loadingJob, setLoadingJob] = useState(true);
  const [jobError, setJobError] = useState<string | null>(null);

  const [tab, setTab] = useState<"resume" | "cover-letter">("resume");
  const [rescoring, setRescoring] = useState(false);
  const [clGenerating, setClGenerating] = useState(false);

  // Inline editing for title / company in breadcrumb
  const [editingField, setEditingField] = useState<"title" | "company" | null>(null);
  const [editValue, setEditValue] = useState("");

  const resumeDoc = useLatexDocument({ jobId: job_id, saveLatex, compileLatex, onSaved: setJob });
  const clDoc = useLatexDocument({ jobId: job_id, saveLatex: saveCoverLetterLatex, compileLatex, onSaved: setJob });

  // Load job on mount
  useEffect(() => {
    setLoadingJob(true);
    fetchJob(job_id)
      .then((j) => {
        setJob(j);
        resumeDoc.load(j.latex_content || "");
        clDoc.load(j.cover_letter_latex || "");
        setLoadingJob(false);
      })
      .catch((e) => {
        setJobError(e instanceof Error ? e.message : "Failed to load job");
        setLoadingJob(false);
      });
    // intentionally exclude resumeDoc/clDoc.load to avoid re-running on their internal state changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job_id]);

  const startEditing = (field: "title" | "company") => {
    if (!job) return;
    setEditingField(field);
    setEditValue(job[field]);
  };

  const commitEdit = useCallback(async () => {
    if (!editingField || !job || !editValue.trim()) {
      setEditingField(null);
      return;
    }
    const trimmed = editValue.trim();
    if (trimmed === job[editingField]) { setEditingField(null); return; }
    try {
      const updated = await updateJob(job_id, { [editingField]: trimmed });
      setJob(updated);
    } catch (e) {
      console.error("Failed to update field", e);
    } finally {
      setEditingField(null);
    }
  }, [editingField, editValue, job, job_id]);

  const handleRescore = useCallback(async () => {
    if (!job || !resumeDoc.latex) return;
    setRescoring(true);
    try {
      // Flush latest edits to DB first so rescore uses current content
      await saveLatex(job_id, resumeDoc.latex);
      const updated = await rescoreJob(job_id);
      setJob(updated);
    } catch (e) {
      console.error("Rescore failed", e);
    } finally {
      setRescoring(false);
    }
  }, [job, resumeDoc.latex, job_id]);

  const handleGenerateCoverLetter = useCallback(async () => {
    setClGenerating(true);
    try {
      const updated = await generateCoverLetter(job_id);
      setJob(updated);
      clDoc.load(updated.cover_letter_latex || "");
    } catch (e) {
      console.error("Cover letter generation failed", e);
    } finally {
      setClGenerating(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job_id]);

  if (loadingJob) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-3 text-muted-foreground flex-1">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        <p className="text-sm">Loading resume…</p>
      </div>
    );
  }

  if (jobError || !job) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-3 text-muted-foreground flex-1">
        <AlertCircle className="h-8 w-8 text-destructive" />
        <p className="text-sm text-destructive">{jobError || "Job not found"}</p>
        <a href="/">
          <Button variant="outline" size="sm" className="gap-1.5">
            <ArrowLeft className="h-3.5 w-3.5" /> Back to Dashboard
          </Button>
        </a>
      </div>
    );
  }

  if (!resumeDoc.latex && tab === "resume") {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-3 text-muted-foreground flex-1">
        <AlertCircle className="h-8 w-8 text-muted-foreground" />
        <p className="text-sm">No LaTeX resume generated yet for this job.</p>
        <p className="text-xs">Go to the Dashboard and click Generate Resume first.</p>
        <a href="/">
          <Button variant="outline" size="sm" className="gap-1.5">
            <ArrowLeft className="h-3.5 w-3.5" /> Back to Dashboard
          </Button>
        </a>
      </div>
    );
  }

  const handleDownload = () => {
    if (!job) return;
    resumeDoc.download(`Shaurya Mathur - ${job.company} - ${job.title} Resume.pdf`);
  };

  const handleClDownload = () => {
    if (!job) return;
    clDoc.download(`Shaurya Mathur - ${job.company} - ${job.title} Cover Letter.pdf`);
  };

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-3">
      {/* Header bar */}
      <div className="flex items-center gap-2 flex-wrap shrink-0">
        <a href="/" className="text-muted-foreground hover:text-foreground transition-colors">
          <Button variant="ghost" size="sm" className="gap-1.5 h-7 text-xs">
            <ArrowLeft className="h-3 w-3" /> Dashboard
          </Button>
        </a>
        <span className="text-muted-foreground text-xs">/</span>
        {editingField === "title" ? (
          <input
            autoFocus
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={commitEdit}
            onKeyDown={(e) => { if (e.key === "Enter") commitEdit(); if (e.key === "Escape") setEditingField(null); }}
            className="font-semibold text-sm bg-transparent border-b border-primary focus:outline-none w-48"
          />
        ) : (
          <span
            className="font-semibold text-sm truncate max-w-[180px] cursor-pointer hover:text-primary transition-colors"
            title="Double-click to edit title"
            onDoubleClick={() => startEditing("title")}
          >
            {job.title}
          </span>
        )}
        <span className="text-muted-foreground text-xs">@</span>
        {editingField === "company" ? (
          <input
            autoFocus
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={commitEdit}
            onKeyDown={(e) => { if (e.key === "Enter") commitEdit(); if (e.key === "Escape") setEditingField(null); }}
            className="text-sm bg-transparent border-b border-primary focus:outline-none w-36"
          />
        ) : (
          <span
            className="text-sm truncate max-w-[140px] cursor-pointer hover:text-primary transition-colors"
            title="Double-click to edit company"
            onDoubleClick={() => startEditing("company")}
          >
            {job.company}
          </span>
        )}
        {job.role_category && (
          <Badge variant="secondary" className="text-xs">{CATEGORY_LABELS[job.role_category] || job.role_category}</Badge>
        )}
        {job.h1b_likely === true && <Badge variant="success" className="text-xs">H1B Likely</Badge>}
        {job.h1b_likely === false && <Badge variant="destructive" className="text-xs">No H1B</Badge>}
        {tab === "resume" && (
          <Button
            variant="outline"
            size="sm"
            onClick={handleRescore}
            disabled={rescoring || !resumeDoc.latex}
            className="gap-1.5 h-7 text-xs"
          >
            {rescoring
              ? <><Loader2 className="h-3 w-3 animate-spin" />Rescoring…</>
              : <><BarChart2 className="h-3 w-3" />{job.match_score != null ? `Score: ${job.match_score.toFixed(0)}` : "Rescore"}</>}
          </Button>
        )}
        {job.link && (
          <a href={job.link} target="_blank" rel="noopener noreferrer" className="text-xs text-muted-foreground hover:text-foreground underline truncate max-w-[120px]">
            Job Posting ↗
          </a>
        )}

        <div className="ml-auto flex items-center gap-2 shrink-0">
          {/* Tab-aware status indicators */}
          {tab === "resume" && (
            <>
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                {resumeDoc.autoSaving
                  ? <><Loader2 className="h-3 w-3 animate-spin" />Saving…</>
                  : <><Save className="h-3 w-3" />Saved</>}
              </span>
              {resumeDoc.compiling && (
                <span className="text-xs text-muted-foreground flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />Compiling…
                </span>
              )}
              {!resumeDoc.compiling && resumeDoc.compileError && (
                <span className="text-xs text-destructive flex items-center gap-1" title={resumeDoc.compileError}>
                  <AlertCircle className="h-3 w-3 shrink-0" />LaTeX error
                </span>
              )}
              {!resumeDoc.compiling && !resumeDoc.compileError && resumeDoc.pdfBlobUrl && (
                <span className="text-xs text-green-600 flex items-center gap-1">
                  <CheckCircle className="h-3 w-3" />PDF ready
                </span>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => resumeDoc.compile(resumeDoc.latex)}
                disabled={resumeDoc.compiling || !resumeDoc.latex}
                className="gap-1.5 h-7 text-xs"
              >
                <RefreshCw className="h-3 w-3" />
                Recompile
              </Button>
              <Button
                size="sm"
                onClick={handleDownload}
                disabled={!resumeDoc.pdfBlobUrl}
                className="gap-1.5 h-7 text-xs"
              >
                <Download className="h-3 w-3" />
                Download PDF
              </Button>
            </>
          )}

          {tab === "cover-letter" && (
            <>
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                {clDoc.autoSaving
                  ? <><Loader2 className="h-3 w-3 animate-spin" />Saving…</>
                  : <><Save className="h-3 w-3" />Saved</>}
              </span>
              {clDoc.compiling && (
                <span className="text-xs text-muted-foreground flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />Compiling…
                </span>
              )}
              {!clDoc.compiling && clDoc.compileError && (
                <span className="text-xs text-destructive flex items-center gap-1" title={clDoc.compileError}>
                  <AlertCircle className="h-3 w-3 shrink-0" />LaTeX error
                </span>
              )}
              {!clDoc.compiling && !clDoc.compileError && clDoc.pdfBlobUrl && (
                <span className="text-xs text-green-600 flex items-center gap-1">
                  <CheckCircle className="h-3 w-3" />PDF ready
                </span>
              )}
              {clDoc.latex && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => clDoc.compile(clDoc.latex)}
                  disabled={clDoc.compiling || !clDoc.latex}
                  className="gap-1.5 h-7 text-xs"
                >
                  <RefreshCw className="h-3 w-3" />
                  Recompile
                </Button>
              )}
              <Button
                size="sm"
                onClick={handleClDownload}
                disabled={!clDoc.pdfBlobUrl}
                className="gap-1.5 h-7 text-xs"
              >
                <Download className="h-3 w-3" />
                Download PDF
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Tab switcher */}
      <div className="flex gap-1 shrink-0">
        <Button
          variant={tab === "resume" ? "default" : "outline"}
          size="sm"
          className="h-7 text-xs"
          onClick={() => setTab("resume")}
        >
          Resume
        </Button>
        <Button
          variant={tab === "cover-letter" ? "default" : "outline"}
          size="sm"
          className="h-7 text-xs"
          onClick={() => setTab("cover-letter")}
        >
          Cover Letter
        </Button>
      </div>

      {/* Resume tab */}
      {tab === "resume" && (
        <LatexEditorPane
          sourceLabel="LaTeX Source"
          latex={resumeDoc.latex}
          onChange={resumeDoc.handleChange}
          compiling={resumeDoc.compiling}
          compileError={resumeDoc.compileError}
          pdfBlobUrl={resumeDoc.pdfBlobUrl}
          iframeTitle="Resume PDF Preview"
        />
      )}

      {/* Cover letter tab */}
      {tab === "cover-letter" && (
        <LatexEditorPane
          sourceLabel="Cover Letter LaTeX"
          latex={clDoc.latex}
          onChange={clDoc.handleChange}
          compiling={clDoc.compiling}
          compileError={clDoc.compileError}
          pdfBlobUrl={clDoc.pdfBlobUrl}
          iframeTitle="Cover Letter PDF Preview"
          emptyState={
            <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground gap-3">
              <AlertCircle className="h-5 w-5 text-muted-foreground" />
              <span className="text-sm">No cover letter generated yet</span>
              <Button
                size="sm"
                onClick={handleGenerateCoverLetter}
                disabled={clGenerating}
                className="gap-1.5"
              >
                {clGenerating
                  ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />Generating…</>
                  : "Generate Cover Letter"}
              </Button>
            </div>
          }
        />
      )}
    </div>
  );
}
