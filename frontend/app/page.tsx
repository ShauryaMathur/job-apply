"use client";

import { useCallback, useEffect, useState } from "react";
import { StatsCards } from "@/components/dashboard/StatsCards";
import { JobTable } from "@/components/dashboard/JobTable";
import { Button } from "@/components/ui/button";
import {
  fetchJobs,
  fetchStats,
  type Job,
  type StatsResponse,
} from "@/lib/api";
import { RefreshCw, Play } from "lucide-react";

export default function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [statsLoading, setStatsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setStatsLoading(true);
    setError(null);
    try {
      const [jobsData, statsData] = await Promise.all([
        fetchJobs({ limit: 500 }),
        fetchStats(),
      ]);
      setJobs(jobsData.jobs);
      setStats(statsData);
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

  const handleJobUpdated = useCallback((updatedJob: Job) => {
    setJobs((prev) =>
      prev.map((j) => (j.job_id === updatedJob.job_id ? updatedJob : j))
    );
  }, []);

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
          <p className="text-muted-foreground text-sm mt-1">
            All jobs — discovered by Radar or manually added via Scout.
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
          <a href="/pipeline">
            <Button size="sm" className="gap-1.5">
              <Play className="h-3.5 w-3.5" />
              Run Radar
            </Button>
          </a>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <StatsCards stats={stats} loading={statsLoading} />

      <JobTable
        jobs={jobs}
        loading={loading}
        onJobUpdated={handleJobUpdated}
        onJobDeleted={(jobId) => setJobs((prev) => prev.filter((j) => j.job_id !== jobId))}
      />

      {!loading && jobs.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 gap-4 text-center">
          <div className="text-5xl">&#128269;</div>
          <h2 className="text-xl font-semibold">No Jobs Yet</h2>
          <p className="text-muted-foreground max-w-md">
            Run Radar to scrape and rank jobs, or use{" "}
            <a href="/apply" className="underline">Scout</a> to manually add a job.
          </p>
          <a href="/pipeline">
            <Button className="gap-2">
              <Play className="h-4 w-4" />
              Go to Radar
            </Button>
          </a>
        </div>
      )}
    </div>
  );
}
