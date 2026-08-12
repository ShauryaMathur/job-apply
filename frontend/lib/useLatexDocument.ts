"use client";

/**
 * Manages a single LaTeX document's compile/autosave/download lifecycle:
 * debounced recompile on edit, debounced autosave on edit, PDF blob URL
 * management (revoking the previous one to avoid leaking), and download.
 *
 * Used twice on the same page (editor/[job_id]/page.tsx) -- once for the
 * resume LaTeX, once for the cover letter LaTeX. Previously that page
 * duplicated this entire state machine under `cl`-prefixed names; this
 * hook is the single implementation both tabs share.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { Job } from "@/lib/api";

const RECOMPILE_DELAY_MS = 2500;
const AUTOSAVE_DELAY_MS = 3000;

function base64ToBlobUrl(b64: string): string {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const blob = new Blob([bytes], { type: "application/pdf" });
  return URL.createObjectURL(blob);
}

interface UseLatexDocumentOptions {
  jobId: string;
  saveLatex: (jobId: string, latexContent: string) => Promise<Job>;
  compileLatex: (texContent: string) => Promise<{ pdf_base64: string }>;
  onSaved?: (job: Job) => void;
}

export function useLatexDocument({ jobId, saveLatex, compileLatex, onSaved }: UseLatexDocumentOptions) {
  const [latex, setLatexState] = useState("");
  const [compiling, setCompiling] = useState(false);
  const [compileError, setCompileError] = useState<string | null>(null);
  const [pdfBlobUrl, setPdfBlobUrl] = useState<string | null>(null);
  const [autoSaving, setAutoSaving] = useState(false);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoSaveRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevBlobRef = useRef<string | null>(null);

  const applyPdf = useCallback((b64: string) => {
    if (prevBlobRef.current) URL.revokeObjectURL(prevBlobRef.current);
    const blobUrl = base64ToBlobUrl(b64);
    prevBlobRef.current = blobUrl;
    setPdfBlobUrl(blobUrl);
  }, []);

  const compile = useCallback(
    async (tex: string) => {
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
    },
    [applyPdf, compileLatex]
  );

  /** Seed the document with content loaded from the server (and compile it once). */
  const load = useCallback(
    (tex: string) => {
      setLatexState(tex);
      if (tex) compile(tex);
    },
    [compile]
  );

  const handleChange = useCallback(
    (value: string) => {
      setLatexState(value);

      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => compile(value), RECOMPILE_DELAY_MS);

      if (autoSaveRef.current) clearTimeout(autoSaveRef.current);
      autoSaveRef.current = setTimeout(async () => {
        setAutoSaving(true);
        try {
          const updated = await saveLatex(jobId, value);
          onSaved?.(updated);
        } catch (e) {
          console.error("Auto-save failed", e);
        } finally {
          setAutoSaving(false);
        }
      }, AUTOSAVE_DELAY_MS);
    },
    [compile, jobId, saveLatex, onSaved]
  );

  const download = useCallback(
    (filename: string) => {
      if (!pdfBlobUrl) return;
      const a = document.createElement("a");
      a.href = pdfBlobUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    },
    [pdfBlobUrl]
  );

  useEffect(() => {
    return () => {
      if (prevBlobRef.current) URL.revokeObjectURL(prevBlobRef.current);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (autoSaveRef.current) clearTimeout(autoSaveRef.current);
    };
  }, []);

  return { latex, load, compiling, compileError, pdfBlobUrl, autoSaving, handleChange, compile, download };
}
