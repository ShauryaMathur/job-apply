"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PipelineLog } from "@/components/dashboard/PipelineLog";
import { SearchConfigPanel } from "@/components/dashboard/SearchConfigPanel";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  fetchPipelineRuns,
  triggerPipeline,
  cancelPipeline,
  type PipelineRun,
  type PipelineTriggerRequest,
} from "@/lib/api";
import {
  CheckCircle,
  XCircle,
  Loader2,
  Square,
  Clock,
  ArrowLeft,
} from "lucide-react";

export default function PipelinePage() {
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRuns = useCallback(async () => {
    setRunsLoading(true);
    try {
      const data = await fetchPipelineRuns();
      setRuns(data.runs);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load runs");
    } finally {
      setRunsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRuns();
  }, [loadRuns]);

  const handleRunPipeline = async (config?: PipelineTriggerRequest) => {
    if (pipelineRunning) return;
    setPipelineRunning(true);
    setError(null);
    try {
      const { run_id } = await triggerPipeline(config);
      setActiveRunId(run_id);
      setShowLog(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start pipeline");
      setPipelineRunning(false);
    }
  };

  const handleStopPipeline = async () => {
    if (!activeRunId) return;
    try {
      await cancelPipeline(activeRunId);
    } catch (e) {
      console.error("Failed to cancel pipeline", e);
    }
  };

  const handlePipelineComplete = useCallback(() => {
    setPipelineRunning(false);
    setTimeout(() => loadRuns(), 1500);
  }, [loadRuns]);

  const RunStatusIcon = ({ status }: { status: string }) => {
    if (status === "running") return <Loader2 className="h-3.5 w-3.5 text-blue-500 animate-spin" />;
    if (status === "completed") return <CheckCircle className="h-3.5 w-3.5 text-green-500" />;
    return <XCircle className="h-3.5 w-3.5 text-red-500" />;
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <a href="/" className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1">
              <ArrowLeft className="h-3.5 w-3.5" /> Dashboard
            </a>
          </div>
          <h1 className="text-3xl font-bold tracking-tight">Radar</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Configure and run the multi-agent job scraping radar.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {pipelineRunning && (
            <>
              <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Running...
              </div>
              <Button
                size="sm"
                variant="destructive"
                onClick={handleStopPipeline}
                className="gap-1.5"
              >
                <Square className="h-3.5 w-3.5" />
                Stop
              </Button>
            </>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Search config + run button */}
      <SearchConfigPanel running={pipelineRunning} onRun={handleRunPipeline} />

      {/* Live log */}
      {showLog && activeRunId && (
        <PipelineLog
          runId={activeRunId}
          onClose={() => setShowLog(false)}
          onComplete={handlePipelineComplete}
        />
      )}

      {/* Run history */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Clock className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">Run History</h2>
        </div>
        {runsLoading && (
          <p className="text-xs text-muted-foreground">Loading runs…</p>
        )}
        {!runsLoading && runs.length === 0 && (
          <p className="text-xs text-muted-foreground">No pipeline runs yet.</p>
        )}
        <div className="flex flex-col gap-2">
          {runs.map((run) => (
            <div
              key={run.id}
              className="flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 text-sm cursor-pointer hover:bg-muted/40 transition-colors"
              onClick={() => {
                setActiveRunId(run.id);
                setShowLog(true);
              }}
            >
              <RunStatusIcon status={run.status} />
              <span className="font-mono text-xs text-muted-foreground">{run.id.slice(0, 8)}</span>
              <span className="font-medium capitalize">{run.status}</span>
              <span className="text-muted-foreground text-xs">
                {new Date(run.started_at).toLocaleDateString()}{" "}
                {new Date(run.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </span>
              <div className="ml-auto flex items-center gap-2">
                {run.jobs_found > 0 && (
                  <Badge variant="secondary" className="text-xs">{run.jobs_found} found</Badge>
                )}
                {run.jobs_ranked > 0 && (
                  <Badge variant="info" className="text-xs">{run.jobs_ranked} ranked</Badge>
                )}
                {run.resumes_generated > 0 && (
                  <Badge variant="success" className="text-xs">{run.resumes_generated} resumes</Badge>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
