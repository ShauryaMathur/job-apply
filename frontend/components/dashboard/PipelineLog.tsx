"use client";

import { useEffect, useRef, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { X, Download, Activity } from "lucide-react";

interface LogEntry {
  timestamp: string;
  level: string;
  agent: string;
  message: string;
  type?: string;
}

interface PipelineLogProps {
  runId: string | null;
  onClose: () => void;
  onComplete?: () => void;
}

const AGENT_COLORS: Record<string, string> = {
  orchestrator: "text-purple-600",
  scraper: "text-blue-600",
  ranker: "text-indigo-600",
  resume_tailor: "text-green-600",
  cover_letter: "text-yellow-600",
  sheets_sync: "text-orange-600",
};

const LEVEL_BADGE: Record<string, "default" | "success" | "warning" | "destructive" | "info"> = {
  info: "info",
  warning: "warning",
  error: "destructive",
};

export function PipelineLog({ runId, onClose, onComplete }: PipelineLogProps) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [isRunning, setIsRunning] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!runId) return;

    setLogs([]);
    setIsRunning(true);
    setError(null);

    const apiBase = process.env.NEXT_PUBLIC_API_URL
      ? `${process.env.NEXT_PUBLIC_API_URL}/api`
      : "/api";

    const es = new EventSource(`${apiBase}/pipeline/${runId}/logs`);
    esRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === "done" || data.type === "heartbeat") {
          if (data.type === "done") {
            setIsRunning(false);
            onComplete?.();
          }
          return;
        }

        setLogs((prev) => [...prev, data as LogEntry]);
      } catch {
        // Ignore parse errors
      }
    };

    es.onerror = () => {
      setIsRunning(false);
      setError("Connection to pipeline lost");
      es.close();
    };

    return () => {
      es.close();
    };
  }, [runId]);

  // Auto-scroll to bottom on new logs
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const downloadLogs = () => {
    const text = logs
      .map(
        (l) =>
          `[${l.timestamp}] [${l.level?.toUpperCase()}] [${l.agent}] ${l.message}`
      )
      .join("\n");
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `pipeline_${runId}_logs.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Card className="border-2 border-primary/20">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CardTitle className="text-base">Pipeline Log</CardTitle>
            {isRunning && (
              <span className="flex items-center gap-1">
                <Activity className="h-4 w-4 text-green-500 animate-pulse" />
                <Badge variant="success" className="text-xs">
                  Running
                </Badge>
              </span>
            )}
            {!isRunning && !error && (
              <Badge variant="success" className="text-xs">
                Completed
              </Badge>
            )}
            {error && (
              <Badge variant="destructive" className="text-xs">
                {error}
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-1">
            <span className="text-xs text-muted-foreground mr-2">
              Run: {runId?.slice(0, 8)}...
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={downloadLogs}
              title="Download logs"
            >
              <Download className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={() => {
                esRef.current?.close();
                onClose();
              }}
              title="Close"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="log-panel h-72 overflow-y-auto font-mono text-xs bg-muted/30 rounded-b-lg px-4 py-3">
          {logs.length === 0 && isRunning && (
            <div className="text-muted-foreground animate-pulse">
              Connecting to pipeline...
            </div>
          )}
          {logs.map((entry, i) => (
            <div key={i} className="py-0.5 flex gap-2 items-start">
              <span className="text-muted-foreground shrink-0 w-[100px] truncate">
                {entry.timestamp
                  ? new Date(entry.timestamp).toLocaleTimeString()
                  : ""}
              </span>
              <span
                className={`shrink-0 uppercase font-bold w-[50px] ${
                  entry.level === "error"
                    ? "text-red-500"
                    : entry.level === "warning"
                    ? "text-yellow-500"
                    : "text-green-600"
                }`}
              >
                {entry.level?.toUpperCase()}
              </span>
              <span
                className={`shrink-0 w-[120px] truncate font-semibold ${
                  AGENT_COLORS[entry.agent] || "text-muted-foreground"
                }`}
              >
                [{entry.agent}]
              </span>
              <span className="text-foreground break-all">{entry.message}</span>
            </div>
          ))}
          {!isRunning && logs.length > 0 && (
            <div className="pt-2 text-muted-foreground border-t border-border mt-2">
              Pipeline finished. {logs.length} log entries.
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </CardContent>
    </Card>
  );
}
