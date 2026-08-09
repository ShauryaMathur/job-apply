"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Loader2,
  Download,
  RefreshCw,
  CheckCircle,
  AlertCircle,
  Link2,
  ClipboardPaste,
  ChevronLeft,
  ChevronRight,
  Trash2,
  Save,
} from "lucide-react";
import {
  ingestJobUrl,
  compileLatex,
  fetchJobsWithLatex,
  deleteJob,
  saveLatex,
  type JobInfo,
  type Job,
} from "@/lib/api";

// Convert base64 PDF string to a blob URL the iframe can load
function base64ToBlobUrl(b64: string): string {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const blob = new Blob([bytes], { type: "application/pdf" });
  return URL.createObjectURL(blob);
}

export default function ApplyPage() {
  const [url, setUrl] = useState("");
  const [category, setCategory] = useState("backend");
  const [manualDescription, setManualDescription] = useState("");
  const [showManualPaste, setShowManualPaste] = useState(false);

  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [jobInfo, setJobInfo] = useState<JobInfo | null>(null);
  const [latex, setLatex] = useState("");

  const [compiling, setCompiling] = useState(false);
  const [compileError, setCompileError] = useState<string | null>(null);
  const [pdfBlobUrl, setPdfBlobUrl] = useState<string | null>(null);

  // Left pane state
  const [savedJobs, setSavedJobs] = useState<Job[]>([]);
  const [paneCollapsed, setPaneCollapsed] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [autoSaving, setAutoSaving] = useState(false);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevBlobRef = useRef<string | null>(null);
  const autoSaveRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Revoke old blob URL and set a new one
  const applyPdf = useCallback((b64: string) => {
    if (prevBlobRef.current) URL.revokeObjectURL(prevBlobRef.current);
    const blobUrl = base64ToBlobUrl(b64);
    prevBlobRef.current = blobUrl;
    setPdfBlobUrl(blobUrl);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (prevBlobRef.current) URL.revokeObjectURL(prevBlobRef.current);
      if (autoSaveRef.current) clearTimeout(autoSaveRef.current);
    };
  }, []);

  // Load saved jobs (jobs with latex_content) on mount
  useEffect(() => {
    fetchJobsWithLatex().then(setSavedJobs).catch(console.error);
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

  const handleIngest = useCallback(async () => {
    const trimmed = url.trim();
    if (!trimmed) return;

    // Cancel any pending debounced recompile
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    // Revoke previous PDF blob to avoid memory leak
    if (prevBlobRef.current) {
      URL.revokeObjectURL(prevBlobRef.current);
      prevBlobRef.current = null;
    }

    // New analysis — not a saved job
    setActiveJobId(null);

    setLoading(true);
    setLoadError(null);
    setJobInfo(null);
    setLatex("");
    setPdfBlobUrl(null);
    setCompileError(null);
    try {
      const desc = manualDescription.trim() || undefined;
      const result = await ingestJobUrl(trimmed, category, desc);
      setJobInfo(result.job_info);
      setLatex(result.latex);
      if (result.pdf_base64) {
        applyPdf(result.pdf_base64);
      }
      if (result.compile_error) {
        setCompileError(result.compile_error);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to analyze job URL";
      setLoadError(msg);
      // Auto-show paste panel for JS-rendered / scrape failures
      if (msg.toLowerCase().includes("javascript") || msg.toLowerCase().includes("paste")) {
        setShowManualPaste(true);
      }
    } finally {
      setLoading(false);
    }
  }, [url, category, manualDescription, applyPdf]);

  const handleLatexChange = useCallback((value: string) => {
    setLatex(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doCompile(value), 2500);
    // Auto-save to DB if editing a pipeline job
    if (activeJobId) {
      if (autoSaveRef.current) clearTimeout(autoSaveRef.current);
      autoSaveRef.current = setTimeout(async () => {
        setAutoSaving(true);
        try {
          await saveLatex(activeJobId, value);
          setSavedJobs(prev => prev.map(j => j.job_id === activeJobId ? { ...j, latex_content: value } : j));
        } catch (e) {
          console.error("Auto-save failed", e);
        } finally {
          setAutoSaving(false);
        }
      }, 3000);
    }
  }, [doCompile, activeJobId]);

  const loadJobFromPane = useCallback((job: Job) => {
    if (!job.latex_content) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (prevBlobRef.current) { URL.revokeObjectURL(prevBlobRef.current); prevBlobRef.current = null; }
    setActiveJobId(job.job_id);
    setJobInfo({ title: job.title, company: job.company, description: job.description || "", h1b_likely: job.h1b_likely, seniority: null, key_skills: [] });
    setLatex(job.latex_content);
    setLoadError(null);
    setPdfBlobUrl(null);
    setCompileError(null);
    setManualDescription("");
    setShowManualPaste(false);
    doCompile(job.latex_content);
  }, [doCompile]);

  const handleDeleteFromPane = useCallback(async (jobId: string) => {
    try {
      await deleteJob(jobId);
      setSavedJobs(prev => prev.filter(j => j.job_id !== jobId));
      if (activeJobId === jobId) {
        setActiveJobId(null);
        setJobInfo(null);
        setLatex("");
        setPdfBlobUrl(null);
      }
    } catch (e) {
      console.error("Failed to delete job", e);
    }
  }, [activeJobId]);

  const handleDownload = useCallback(() => {
    if (!pdfBlobUrl) return;
    const a = document.createElement("a");
    a.href = pdfBlobUrl;
    a.download = `Shaurya Mathur - ${jobInfo?.title || "Resume"}.pdf`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, [pdfBlobUrl, jobInfo]);

  const showEditor = !loading && jobInfo !== null;

  return (
    <div className="flex flex-col gap-4 flex-1 min-h-0">
      {/* Page header */}
      <div>
        <h1 className="text-xl font-bold">Apply Tool</h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Paste any job URL to generate a tailored resume, fine-tune it, and download the PDF.
        </p>
      </div>

      {/* Main row: pane + content */}
      <div className="flex gap-3 flex-1 min-h-0">

        {/* LEFT PANE */}
        <div className={`flex flex-col border border-border rounded-lg overflow-hidden transition-all duration-200 shrink-0 ${paneCollapsed ? "w-9" : "w-60"}`}>
          {/* Pane header */}
          <div className="flex items-center justify-between px-2 py-2 border-b border-border bg-muted/50 shrink-0">
            {!paneCollapsed && <span className="text-xs font-medium truncate">Saved Resumes</span>}
            <button
              onClick={() => setPaneCollapsed(p => !p)}
              className="ml-auto p-0.5 rounded hover:bg-muted text-muted-foreground hover:text-foreground"
            >
              {paneCollapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
            </button>
          </div>
          {/* Job list */}
          {!paneCollapsed && (
            <div className="flex-1 overflow-y-auto">
              {savedJobs.length === 0 && (
                <p className="text-xs text-muted-foreground text-center py-6 px-2">
                  No saved resumes yet. Generate a resume from the dashboard.
                </p>
              )}
              {savedJobs.map(job => (
                <div
                  key={job.job_id}
                  onClick={() => loadJobFromPane(job)}
                  className={`group flex items-start gap-1.5 px-2 py-2 cursor-pointer border-b border-border hover:bg-muted/40 transition-colors ${activeJobId === job.job_id ? "bg-muted" : ""}`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium leading-tight truncate" title={job.title}>{job.title}</div>
                    <div className="text-xs text-muted-foreground truncate" title={job.company}>{job.company}</div>
                    {job.match_score != null && (
                      <span className="text-xs font-mono text-muted-foreground">{job.match_score.toFixed(0)}</span>
                    )}
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDeleteFromPane(job.job_id); }}
                    className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-muted-foreground hover:text-destructive transition-opacity shrink-0 mt-0.5"
                    title="Delete job"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* RIGHT: existing content (URL card + loading + editor) */}
        <div className="flex flex-col gap-4 flex-1 min-h-0">
          {/* URL input card */}
          <Card>
            <CardContent className="pt-4 pb-4">
              <div className="flex gap-2">
                <div className="flex-1 relative">
                  <Link2 className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground pointer-events-none" />
                  <input
                    type="url"
                    placeholder="https://jobs.lever.co/... or any job posting URL"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleIngest()}
                    disabled={loading}
                    className="w-full pl-9 pr-3 py-2 text-sm rounded-md border border-input bg-background focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-60"
                  />
                </div>
                <Select value={category} onValueChange={setCategory} disabled={loading}>
                  <SelectTrigger className="w-36">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="backend">Backend</SelectItem>
                    <SelectItem value="fullstack">Full Stack</SelectItem>
                    <SelectItem value="aiml">AI/ML</SelectItem>
                  </SelectContent>
                </Select>
                <Button
                  onClick={handleIngest}
                  disabled={loading || !url.trim()}
                  className="gap-2 shrink-0"
                >
                  {loading ? (
                    <><Loader2 className="h-4 w-4 animate-spin" />Analyzing...</>
                  ) : manualDescription.trim() ? (
                    "Generate from Description"
                  ) : (
                    "Analyze & Generate"
                  )}
                </Button>
              </div>
              {loadError && (
                <div className="mt-2 space-y-2">
                  <p className="flex items-center gap-1.5 text-destructive text-sm">
                    <AlertCircle className="h-4 w-4 shrink-0" />
                    {loadError}
                  </p>
                  {!showManualPaste && (
                    <button
                      onClick={() => setShowManualPaste(true)}
                      className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                    >
                      <ClipboardPaste className="h-3.5 w-3.5" />
                      Paste job description manually instead
                    </button>
                  )}
                </div>
              )}
              {showManualPaste && (
                <div className="mt-3 space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                      <ClipboardPaste className="h-3.5 w-3.5" />
                      Job description
                    </label>
                    <button
                      onClick={() => { setShowManualPaste(false); setManualDescription(""); }}
                      className="text-xs text-muted-foreground hover:text-foreground"
                    >
                      Cancel
                    </button>
                  </div>
                  <textarea
                    value={manualDescription}
                    onChange={(e) => setManualDescription(e.target.value)}
                    placeholder="Paste the full job description here…"
                    rows={6}
                    className="w-full px-3 py-2 text-sm rounded-md border border-input bg-background focus:outline-none focus:ring-1 focus:ring-ring resize-none font-mono"
                  />
                  <p className="text-xs text-muted-foreground">
                    The URL is still used for reference. Description replaces the scrape.
                  </p>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Loading indicator */}
          {loading && (
            <div className="flex flex-col items-center gap-3 py-16 text-muted-foreground">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
              <p className="text-sm font-medium">Scraping job page and tailoring your resume…</p>
              <p className="text-xs">Scrape → extract → LLM tailor → compile. Takes ~45 seconds.</p>
            </div>
          )}

          {/* Split screen */}
          {showEditor && (
            <div className="flex flex-col gap-3 flex-1 min-h-0">
              {/* Job info + action bar */}
              <div className="flex items-center gap-2 flex-wrap min-h-[32px]">
                <span className="font-semibold text-sm truncate max-w-[200px]" title={jobInfo.title}>
                  {jobInfo.title}
                </span>
                <span className="text-muted-foreground text-sm">@</span>
                <span className="text-sm truncate max-w-[160px]" title={jobInfo.company}>
                  {jobInfo.company}
                </span>
                {jobInfo.seniority && (
                  <Badge variant="secondary" className="text-xs">{jobInfo.seniority}</Badge>
                )}
                {jobInfo.h1b_likely === true && (
                  <Badge variant="success" className="text-xs">H1B Likely</Badge>
                )}
                {jobInfo.h1b_likely === false && (
                  <Badge variant="destructive" className="text-xs">No H1B</Badge>
                )}
                {jobInfo.key_skills.slice(0, 5).map((s) => (
                  <Badge key={s} variant="outline" className="text-xs hidden lg:inline-flex">{s}</Badge>
                ))}

                <div className="ml-auto flex items-center gap-2 shrink-0">
                  {activeJobId && (
                    <span className="text-xs text-muted-foreground flex items-center gap-1">
                      {autoSaving ? <><Loader2 className="h-3 w-3 animate-spin" />Saving…</> : <><Save className="h-3 w-3" />Saved</>}
                    </span>
                  )}
                  {compiling && (
                    <span className="text-xs text-muted-foreground flex items-center gap-1">
                      <Loader2 className="h-3 w-3 animate-spin" />Compiling…
                    </span>
                  )}
                  {!compiling && compileError && (
                    <span className="text-xs text-destructive flex items-center gap-1 max-w-[200px] truncate" title={compileError}>
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
          )}
        </div>

      </div>
    </div>
  );
}
