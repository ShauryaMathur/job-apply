"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { StatsCards } from "@/components/dashboard/StatsCards";
import { JobTable } from "@/components/dashboard/JobTable";
import { PipelineLog } from "@/components/dashboard/PipelineLog";
import { SearchConfigPanel } from "@/components/dashboard/SearchConfigPanel";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  fetchJobs,
  fetchStats,
  fetchPipelineRuns,
  triggerPipeline,
  cancelPipeline,
  type Job,
  type StatsResponse,
  type PipelineRun,
  type PipelineTriggerRequest,
} from "@/lib/api";
import {
  Play,
  RefreshCw,
  Clock,
  CheckCircle,
  XCircle,
  Loader2,
  Square,
} from "lucide-react";

export default function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [statsLoading, setStatsLoading] = useState(true);
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const refreshTimerRef = useRef<NodeJS.Timeout | null>(null);

  // ---------------------------------------------------------------------------
  // Data fetching
  // ---------------------------------------------------------------------------

  const loadData = useCallback(async () => {
    setLoading(true);
    setStatsLoading(true);
    setError(null);
    try {
      const [jobsData, statsData, runsData] = await Promise.all([
        fetchJobs({ limit: 500 }),
        fetchStats(),
        fetchPipelineRuns(),
      ]);
      setJobs(jobsData.jobs);
      setStats(statsData);
      setRuns(runsData.runs);
      setLastRefreshed(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load data");
    } finally {
      setLoading(false);
      setStatsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // ---------------------------------------------------------------------------
  // Pipeline trigger
  // ---------------------------------------------------------------------------

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
    // Refresh data after pipeline completes
    setTimeout(() => {
      loadData();
    }, 1500);
  }, [loadData]);

  const handleJobUpdated = useCallback((updatedJob: Job) => {
    setJobs((prev) =>
      prev.map((j) => (j.job_id === updatedJob.job_id ? updatedJob : j))
    );
  }, []);

  // ---------------------------------------------------------------------------
  // Recent runs
  // ---------------------------------------------------------------------------

  const recentRuns = runs.slice(0, 5);

  const RunStatusIcon = ({ status }: { status: string }) => {
    if (status === "running") return <Loader2 className="h-3.5 w-3.5 text-blue-500 animate-spin" />;
    if (status === "completed") return <CheckCircle className="h-3.5 w-3.5 text-green-500" />;
    return <XCircle className="h-3.5 w-3.5 text-red-500" />;
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Job Dashboard</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Multi-agent job search powered by LangGraph + LiteLLM + ChromaDB
          </p>
        </div>
        <div className="flex items-center gap-2">
          {lastRefreshed && (
            <span className="text-xs text-muted-foreground">
              Updated {lastRefreshed.toLocaleTimeString()}
            </span>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={loadData}
            disabled={loading}
            className="gap-1.5"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          {pipelineRunning && (
            <Button
              size="sm"
              variant="destructive"
              onClick={handleStopPipeline}
              className="gap-1.5"
            >
              <Square className="h-3.5 w-3.5" />
              Stop
            </Button>
          )}
          {pipelineRunning && (
            <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Running...
            </div>
          )}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Search config + run button */}
      <SearchConfigPanel running={pipelineRunning} onRun={handleRunPipeline} />

      {/* Stats cards */}
      <StatsCards stats={stats} loading={statsLoading} />

      {/* Recent pipeline runs */}
      {recentRuns.length > 0 && (
        <div className="flex items-center gap-3 overflow-x-auto pb-1">
          <span className="text-xs font-medium text-muted-foreground shrink-0 flex items-center gap-1">
            <Clock className="h-3.5 w-3.5" />
            Recent Runs:
          </span>
          {recentRuns.map((run) => (
            <button
              key={run.id}
              onClick={() => {
                setActiveRunId(run.id);
                setShowLog(true);
              }}
              className="flex items-center gap-1.5 text-xs rounded-full border px-3 py-1 hover:bg-muted transition-colors shrink-0"
              title={`Jobs: ${run.jobs_found} | Ranked: ${run.jobs_ranked} | Resumes: ${run.resumes_generated}${run.error ? ` | Error: ${run.error}` : ""}`}
            >
              <RunStatusIcon status={run.status} />
              <span className="font-mono">{run.id.slice(0, 8)}</span>
              <span className="text-muted-foreground">
                {new Date(run.started_at).toLocaleDateString()}{" "}
                {new Date(run.started_at).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
              {run.jobs_found > 0 && (
                <Badge variant="secondary" className="text-xs px-1 py-0">
                  {run.jobs_found} jobs
                </Badge>
              )}
            </button>
          ))}
        </div>
      )}

      {/* Live pipeline log panel */}
      {showLog && activeRunId && (
        <PipelineLog
          runId={activeRunId}
          onClose={() => setShowLog(false)}
          onComplete={handlePipelineComplete}
        />
      )}

      {/* Job table */}
      <JobTable
        jobs={jobs}
        loading={loading}
        onJobUpdated={handleJobUpdated}
        onJobDeleted={(jobId) => setJobs(prev => prev.filter(j => j.job_id !== jobId))}
      />

      {/* Empty state when no jobs */}
      {!loading && jobs.length === 0 && !showLog && (
        <div className="flex flex-col items-center justify-center py-16 gap-4 text-center">
          <div className="text-5xl">&#128269;</div>
          <h2 className="text-xl font-semibold">No Jobs Yet</h2>
          <p className="text-muted-foreground max-w-md">
            Click{" "}
            <strong>Run Job Search</strong> to start the multi-agent pipeline.
            The system will scrape Indeed, rank jobs by relevance, tailor your
            resume, and display results here.
          </p>
          <Button onClick={() => handleRunPipeline()} disabled={pipelineRunning} className="gap-2">
            <Play className="h-4 w-4" />
            Run Job Search
          </Button>
        </div>
      )}
    </div>
  );
}
