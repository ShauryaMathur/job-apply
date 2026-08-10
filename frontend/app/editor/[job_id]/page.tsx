"use client";

import { useState, useEffect, useCallback, useRef } from "react";
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
} from "lucide-react";
import { fetchJob, compileLatex, saveLatex, updateJob, type Job } from "@/lib/api";

function base64ToBlobUrl(b64: string): string {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const blob = new Blob([bytes], { type: "application/pdf" });
  return URL.createObjectURL(blob);
}

const STATUS_VARIANT: Record<string, "default" | "secondary" | "success" | "warning" | "destructive" | "info" | "purple"> = {
  new: "secondary",
  reviewed: "info",
  applying: "info",
  applied: "success",
  rejected: "destructive",
  interview: "warning",
  offer: "purple",
};

const CATEGORY_LABELS: Record<string, string> = {
  backend: "Backend",
  fullstack: "Full Stack",
  aiml: "AI/ML",
};

export default function EditorPage({ params }: { params: { job_id: string } }) {
  const { job_id } = params;

  const [job, setJob] = useState<Job | null>(null);
  const [latex, setLatex] = useState("");
  const [loadingJob, setLoadingJob] = useState(true);
  const [jobError, setJobError] = useState<string | null>(null);

  const [compiling, setCompiling] = useState(false);
  const [compileError, setCompileError] = useState<string | null>(null);
  const [pdfBlobUrl, setPdfBlobUrl] = useState<string | null>(null);

  const [autoSaving, setAutoSaving] = useState(false);

  // Inline editing for title / company in breadcrumb
  const [editingField, setEditingField] = useState<"title" | "company" | null>(null);
  const [editValue, setEditValue] = useState("");

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoSaveRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevBlobRef = useRef<string | null>(null);

  const applyPdf = useCallback((b64: string) => {
    if (prevBlobRef.current) URL.revokeObjectURL(prevBlobRef.current);
    const blobUrl = base64ToBlobUrl(b64);
    prevBlobRef.current = blobUrl;
    setPdfBlobUrl(blobUrl);
  }, []);

  useEffect(() => {
    return () => {
      if (prevBlobRef.current) URL.revokeObjectURL(prevBlobRef.current);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (autoSaveRef.current) clearTimeout(autoSaveRef.current);
    };
  }, []);

  const doCompile = useCallback(async (tex: string) => {
    if (!tex) return;
    setCompiling(true);
    setCompileError(null);
    try {
      const result = await compileLatex(tex);
      applyPdf(result.pdf_base64);
    } catch (e) {
      setCompileError(e instanceof Error ? e.message : "Compilation failed");
    } finally {
      setCompiling(false);
    }
  }, [applyPdf]);

  // Load job on mount
  useEffect(() => {
    setLoadingJob(true);
    fetchJob(job_id)
      .then((j) => {
        setJob(j);
        const tex = j.latex_content || "";
        setLatex(tex);
        setLoadingJob(false);
        if (tex) doCompile(tex);
      })
      .catch((e) => {
        setJobError(e instanceof Error ? e.message : "Failed to load job");
        setLoadingJob(false);
      });
  }, [job_id]); // intentionally exclude doCompile to avoid re-running on compile state changes

  const handleLatexChange = useCallback(
    (value: string) => {
      setLatex(value);
      // Debounced recompile (2.5s)
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => doCompile(value), 2500);
      // Auto-save (3s)
      if (autoSaveRef.current) clearTimeout(autoSaveRef.current);
      autoSaveRef.current = setTimeout(async () => {
        setAutoSaving(true);
        try {
          const updated = await saveLatex(job_id, value);
          setJob(updated);
        } catch (e) {
          console.error("Auto-save failed", e);
        } finally {
          setAutoSaving(false);
        }
      }, 3000);
    },
    [doCompile, job_id]
  );

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

  const handleDownload = useCallback(() => {
    if (!pdfBlobUrl || !job) return;
    const a = document.createElement("a");
    a.href = pdfBlobUrl;
    a.download = `Shaurya Mathur - ${job.company} - ${job.title} Resume.pdf`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, [pdfBlobUrl, job]);

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

  if (!latex) {
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
        {job.match_score != null && (
          <span className="text-xs text-muted-foreground font-mono">Score: {job.match_score.toFixed(0)}</span>
        )}
        {job.link && (
          <a href={job.link} target="_blank" rel="noopener noreferrer" className="text-xs text-muted-foreground hover:text-foreground underline truncate max-w-[120px]">
            Job Posting ↗
          </a>
        )}

        <div className="ml-auto flex items-center gap-2 shrink-0">
          <span className="text-xs text-muted-foreground flex items-center gap-1">
            {autoSaving
              ? <><Loader2 className="h-3 w-3 animate-spin" />Saving…</>
              : <><Save className="h-3 w-3" />Saved</>}
          </span>
          {compiling && (
            <span className="text-xs text-muted-foreground flex items-center gap-1">
              <Loader2 className="h-3 w-3 animate-spin" />Compiling…
            </span>
          )}
          {!compiling && compileError && (
            <span className="text-xs text-destructive flex items-center gap-1" title={compileError}>
              <AlertCircle className="h-3 w-3 shrink-0" />LaTeX error
            </span>
          )}
          {!compiling && !compileError && pdfBlobUrl && (
            <span className="text-xs text-green-600 flex items-center gap-1">
              <CheckCircle className="h-3 w-3" />PDF ready
            </span>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => doCompile(latex)}
            disabled={compiling || !latex}
            className="gap-1.5 h-7 text-xs"
          >
            <RefreshCw className="h-3 w-3" />
            Recompile
          </Button>
          <Button
            size="sm"
            onClick={handleDownload}
            disabled={!pdfBlobUrl}
            className="gap-1.5 h-7 text-xs"
          >
            <Download className="h-3 w-3" />
            Download PDF
          </Button>
        </div>
      </div>

      {/* Split screen */}
      <div className="grid grid-cols-2 gap-3 flex-1 min-h-0">
        {/* LaTeX editor */}
        <div className="flex flex-col border border-border rounded-lg overflow-hidden">
          <div className="px-3 py-1.5 border-b border-border bg-muted/50 flex items-center justify-between shrink-0">
            <span className="text-xs font-medium text-muted-foreground">LaTeX Source</span>
            <span className="text-xs text-muted-foreground">{latex.split("\n").length} lines</span>
          </div>
          <textarea
            value={latex}
            onChange={(e) => handleLatexChange(e.target.value)}
            className="flex-1 p-3 font-mono text-xs bg-background resize-none focus:outline-none leading-relaxed"
            spellCheck={false}
            autoComplete="off"
            autoCorrect="off"
          />
        </div>

        {/* PDF preview */}
        <div className="flex flex-col border border-border rounded-lg overflow-hidden">
          <div className="px-3 py-1.5 border-b border-border bg-muted/50 shrink-0">
            <span className="text-xs font-medium text-muted-foreground">PDF Preview</span>
          </div>
          {pdfBlobUrl ? (
            <iframe
              src={pdfBlobUrl}
              className="flex-1 w-full bg-white"
              title="Resume PDF Preview"
            />
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground gap-2">
              {compiling ? (
                <><Loader2 className="h-5 w-5 animate-spin" /><span className="text-sm">Compiling PDF…</span></>
              ) : compileError ? (
                <>
                  <AlertCircle className="h-5 w-5 text-destructive" />
                  <span className="text-sm text-destructive">Compilation error</span>
                  <pre className="text-xs text-muted-foreground max-w-xs whitespace-pre-wrap text-center mt-1">
                    {compileError.slice(0, 300)}
                  </pre>
                </>
              ) : (
                <span className="text-sm">PDF will appear here</span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
