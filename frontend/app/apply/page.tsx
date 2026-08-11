"use client";

import { useState, useCallback, useRef } from "react";
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
  AlertCircle,
  Link2,
  ClipboardPaste,
  CheckCircle,
  ExternalLink,
  PenLine,
} from "lucide-react";
import { ingestJobUrl, type JobInfo } from "@/lib/api";
import { extractSourceFromUrl, SOURCE_LABELS } from "@/lib/constants";
import { notifyTaskDone, requestNotifyPermission } from "@/lib/notify";

export default function ApplyPage() {
  const [url, setUrl] = useState("");
  const [category, setCategory] = useState("backend");
  const [manualDescription, setManualDescription] = useState("");
  const [showManualPaste, setShowManualPaste] = useState(false);
  const [source, setSource] = useState("");

  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const abortController = useRef<AbortController | null>(null);

  const isValidUrl = (val: string) => {
    try {
      const u = new URL(val.trim());
      return u.protocol === "http:" || u.protocol === "https:";
    } catch {
      return false;
    }
  };

  const urlTouched = url.length > 0;
  const urlValid = isValidUrl(url);
  // Can submit if: URL is valid, OR no URL but description is provided
  const canSubmit = urlValid || (!url.trim() && manualDescription.trim().length > 0);

  // After successful ingest
  const [successJobId, setSuccessJobId] = useState<string | null>(null);
  const [successJobInfo, setSuccessJobInfo] = useState<JobInfo | null>(null);

  const handleIngest = useCallback(async () => {
    if (!canSubmit) return;
    const trimmed = url.trim();

    // Request desktop notification permission from this click (user gesture)
    // so we can pull focus back once the scrape/tailor run finishes.
    requestNotifyPermission();

    const controller = new AbortController();
    abortController.current = controller;
    setLoading(true);
    setLoadError(null);
    setSuccessJobId(null);
    setSuccessJobInfo(null);

    try {
      const desc = manualDescription.trim() || undefined;
      const result = await ingestJobUrl(trimmed || undefined, category, desc, controller.signal, source || undefined);
      setSuccessJobId(result.job_id);
      setSuccessJobInfo(result.job_info);
      notifyTaskDone(
        "Scout finished",
        `${result.job_info.title} at ${result.job_info.company} — resume ready`
      );
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      const msg = e instanceof Error ? e.message : "Failed to analyze job URL";
      setLoadError(msg);
      if (msg.toLowerCase().includes("javascript") || msg.toLowerCase().includes("paste")) {
        setShowManualPaste(true);
      }
      notifyTaskDone("Scout failed", msg);
    } finally {
      abortController.current = null;
      setLoading(false);
    }
  }, [url, category, manualDescription, canSubmit, source]);

  const handleUrlChange = (val: string) => {
    setUrl(val);
    // Auto-detect source from URL, only if user hasn't manually overridden it
    if (isValidUrl(val)) {
      const detected = extractSourceFromUrl(val);
      if (detected) setSource(detected);
    }
  };

  const handleReset = () => {
    setUrl("");
    setSource("");
    setManualDescription("");
    setShowManualPaste(false);
    setLoadError(null);
    setSuccessJobId(null);
    setSuccessJobInfo(null);
  };

  return (
    <div className="flex flex-col gap-6 max-w-2xl mx-auto w-full">
      {/* Page header */}
      <div>
        <h1 className="text-xl font-bold">Scout</h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Paste a job URL to scrape, extract, and generate a tailored LaTeX resume saved to your dashboard.
        </p>
      </div>

      {/* URL input card */}
      {!successJobId && (
        <Card>
          <CardContent className="pt-4 pb-4">
            <div className="flex gap-2">
              <div className="flex-1 relative">
                <Link2 className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground pointer-events-none" />
                <input
                  type="url"
                  placeholder="https://jobs.lever.co/... or any job posting URL"
                  value={url}
                  onChange={(e) => handleUrlChange(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && canSubmit && handleIngest()}
                  disabled={loading}
                  className={`w-full pl-9 pr-3 py-2 text-sm rounded-md border bg-background focus:outline-none focus:ring-1 disabled:opacity-60 ${
                    urlTouched && !urlValid
                      ? "border-destructive focus:ring-destructive"
                      : "border-input focus:ring-ring"
                  }`}
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
                disabled={loading || !canSubmit}
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

            {urlTouched && !urlValid && (
              <p className="mt-1.5 text-xs text-destructive">
                Enter a valid URL starting with https://
              </p>
            )}

            {/* Source — auto-detected from URL, manually editable */}
            <div className="mt-2 flex items-center gap-2">
              <label className="text-xs text-muted-foreground shrink-0">Source</label>
              <input
                type="text"
                placeholder={urlValid ? (extractSourceFromUrl(url) || "auto-detect…") : "e.g. LinkedIn, Lever (optional)"}
                value={source}
                onChange={(e) => setSource(e.target.value)}
                disabled={loading}
                className="flex-1 px-2 py-1 text-xs rounded-md border border-input bg-background focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-60"
              />
              {source && SOURCE_LABELS[source] && source !== SOURCE_LABELS[source].toLowerCase() && (
                <span className="text-xs text-muted-foreground">{SOURCE_LABELS[source]}</span>
              )}
            </div>

            {/* Always-visible toggle for manual description */}
            {!showManualPaste && (
              <div className="mt-2">
                <button
                  onClick={() => setShowManualPaste(true)}
                  className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  <ClipboardPaste className="h-3.5 w-3.5" />
                  Add job description manually
                </button>
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
                    Hide
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
                  URL is used for reference only. Description overrides scraping.
                </p>
              </div>
            )}

            {loadError && (
              <p className="mt-2 flex items-center gap-1.5 text-destructive text-sm">
                <AlertCircle className="h-4 w-4 shrink-0" />
                {loadError}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex flex-col items-center gap-3 py-16 text-muted-foreground">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <p className="text-sm font-medium">Scraping job page and tailoring your resume…</p>
          <p className="text-xs">Scrape → extract → LLM tailor → save. Takes ~45 seconds.</p>
          <button
            onClick={() => { abortController.current?.abort(); setLoading(false); }}
            className="text-xs text-muted-foreground hover:text-destructive mt-1"
          >
            cancel
          </button>
        </div>
      )}

      {/* Success card */}
      {successJobId && successJobInfo && (
        <Card className="border-green-500/30 bg-green-500/5">
          <CardContent className="pt-5 pb-5">
            <div className="flex items-start gap-3">
              <CheckCircle className="h-5 w-5 text-green-500 shrink-0 mt-0.5" />
              <div className="flex-1 space-y-3">
                <div>
                  <p className="text-xs text-muted-foreground mb-0.5">Resume generated and saved</p>
                  <p className="font-semibold text-base">{successJobInfo.title}</p>
                  <p className="text-sm text-muted-foreground">{successJobInfo.company}</p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {successJobInfo.seniority && (
                    <Badge variant="secondary" className="text-xs">{successJobInfo.seniority}</Badge>
                  )}
                  {successJobInfo.h1b_likely === true && (
                    <Badge variant="success" className="text-xs">H1B Likely</Badge>
                  )}
                  {successJobInfo.h1b_likely === false && (
                    <Badge variant="destructive" className="text-xs">No H1B</Badge>
                  )}
                  {successJobInfo.key_skills.slice(0, 6).map((s) => (
                    <Badge key={s} variant="outline" className="text-xs">{s}</Badge>
                  ))}
                </div>
                <div className="flex gap-2 pt-1">
                  <a href={`/editor/${successJobId}`}>
                    <Button size="sm" className="gap-2">
                      <PenLine className="h-3.5 w-3.5" />
                      Open in Editor
                    </Button>
                  </a>
                  <a href="/">
                    <Button variant="outline" size="sm" className="gap-2">
                      <ExternalLink className="h-3.5 w-3.5" />
                      View in Dashboard
                    </Button>
                  </a>
                  <Button variant="ghost" size="sm" onClick={handleReset}>
                    Analyze another job
                  </Button>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
