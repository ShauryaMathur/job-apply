"use client";

/**
 * LaTeX source editor (Monaco) + compiled PDF preview, side by side.
 *
 * Shared by the resume and cover-letter tabs in editor/[job_id]/page.tsx,
 * which previously each had their own near-identical copy of this markup.
 */

import type { ReactNode } from "react";
import Editor from "@monaco-editor/react";
import { Loader2, AlertCircle } from "lucide-react";

const MONACO_OPTIONS = {
  fontSize: 12,
  fontFamily: "ui-monospace, 'Cascadia Code', 'Source Code Pro', Menlo, monospace",
  minimap: { enabled: false },
  wordWrap: "on" as const,
  lineNumbers: "on" as const,
  scrollBeyondLastLine: false,
  automaticLayout: true,
  tabSize: 2,
  renderWhitespace: "none" as const,
  overviewRulerLanes: 0,
  hideCursorInOverviewRuler: true,
  scrollbar: { vertical: "auto" as const, horizontal: "hidden" as const },
};

interface LatexEditorPaneProps {
  sourceLabel: string;
  latex: string;
  onChange: (value: string) => void;
  compiling: boolean;
  compileError: string | null;
  pdfBlobUrl: string | null;
  iframeTitle: string;
  /** Rendered instead of the Monaco editor when `latex` is empty (e.g. cover letter not generated yet). */
  emptyState?: ReactNode;
}

export function LatexEditorPane({
  sourceLabel,
  latex,
  onChange,
  compiling,
  compileError,
  pdfBlobUrl,
  iframeTitle,
  emptyState,
}: LatexEditorPaneProps) {
  return (
    <div className="grid grid-cols-2 gap-3 flex-1 min-h-0">
      {/* LaTeX editor */}
      <div className="flex flex-col border border-border rounded-lg overflow-hidden">
        <div className="px-3 py-1.5 border-b border-border bg-muted/50 flex items-center justify-between shrink-0">
          <span className="text-xs font-medium text-muted-foreground">{sourceLabel}</span>
          {latex && (
            <span className="text-xs text-muted-foreground">{latex.split("\n").length} lines</span>
          )}
        </div>
        {latex || !emptyState ? (
          <Editor
            language="latex"
            value={latex}
            theme="vs-dark"
            onChange={(val) => onChange(val ?? "")}
            loading={<div className="flex-1 flex items-center justify-center text-muted-foreground text-xs">Loading editor…</div>}
            options={MONACO_OPTIONS}
          />
        ) : (
          emptyState
        )}
      </div>

      {/* PDF preview */}
      <div className="flex flex-col border border-border rounded-lg overflow-hidden">
        <div className="px-3 py-1.5 border-b border-border bg-muted/50 shrink-0">
          <span className="text-xs font-medium text-muted-foreground">PDF Preview</span>
        </div>
        {pdfBlobUrl ? (
          <iframe src={pdfBlobUrl} className="flex-1 w-full bg-white" title={iframeTitle} />
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground gap-2">
            {compiling ? (
              <>
                <Loader2 className="h-5 w-5 animate-spin" />
                <span className="text-sm">Compiling PDF…</span>
              </>
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
  );
}
