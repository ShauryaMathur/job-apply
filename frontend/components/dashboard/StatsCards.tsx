"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { StatsResponse } from "@/lib/api";
import {
  Briefcase,
  CheckCircle,
  MessageSquare,
  Shield,
  FileText,
  TrendingUp,
} from "lucide-react";

interface StatsCardsProps {
  stats: StatsResponse | null;
  loading?: boolean;
}

function StatCard({
  title,
  value,
  icon: Icon,
  sub,
  color,
}: {
  title: string;
  value: string | number;
  icon: React.ElementType;
  sub?: string;
  color?: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
        <Icon className={`h-4 w-4 ${color || "text-muted-foreground"}`} />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {sub && <p className="text-xs text-muted-foreground mt-1">{sub}</p>}
      </CardContent>
    </Card>
  );
}

export function StatsCards({ stats, loading }: StatsCardsProps) {
  if (loading || !stats) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <Card key={i} className="animate-pulse">
            <CardHeader className="pb-2">
              <div className="h-4 bg-muted rounded w-3/4" />
            </CardHeader>
            <CardContent>
              <div className="h-8 bg-muted rounded w-1/2" />
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  const byCategory = stats.by_category || {};
  const categoryLabels: Record<string, string> = {
    backend: "Backend",
    fullstack: "Full Stack",
    aiml: "AI/ML",
  };

  const categoryBreakdown = Object.entries(byCategory)
    .map(([k, v]) => `${categoryLabels[k] || k}: ${v}`)
    .join(" · ");

  const byStatus = stats.by_status || {};
  const statusBreakdown = Object.entries(byStatus)
    .filter(([k]) => k !== "new")
    .map(([k, v]) => `${k}: ${v}`)
    .join(" · ");

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        <StatCard
          title="Total Jobs"
          value={stats.total_jobs}
          icon={Briefcase}
          sub={categoryBreakdown || "No jobs yet"}
          color="text-blue-500"
        />
        <StatCard
          title="Backend"
          value={byCategory.backend || 0}
          icon={TrendingUp}
          sub="Java / Python / Go"
          color="text-indigo-500"
        />
        <StatCard
          title="Full Stack"
          value={byCategory.fullstack || 0}
          icon={TrendingUp}
          sub="React + Node / Python"
          color="text-violet-500"
        />
        <StatCard
          title="AI / ML"
          value={byCategory.aiml || 0}
          icon={TrendingUp}
          sub="PyTorch / LLMs / MLOps"
          color="text-purple-500"
        />
        <StatCard
          title="Applied"
          value={stats.applied_count}
          icon={CheckCircle}
          sub={statusBreakdown || "No applications yet"}
          color="text-green-500"
        />
        <StatCard
          title="Interviews"
          value={stats.interview_count}
          icon={MessageSquare}
          sub="Scheduled or completed"
          color="text-orange-500"
        />
      </div>

      {/* Secondary row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          title="H1B Sponsors"
          value={stats.h1b_likely_count}
          icon={Shield}
          sub="Likely to sponsor H1B"
          color="text-emerald-500"
        />
        <StatCard
          title="Resumes Generated"
          value={stats.resumes_generated}
          icon={FileText}
          sub="Tailored LaTeX PDFs"
          color="text-sky-500"
        />
        <Card className="col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Status Breakdown
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {Object.entries(byStatus).map(([status, count]) => (
                <Badge
                  key={status}
                  variant={
                    status === "applied"
                      ? "success"
                      : status === "interview"
                      ? "warning"
                      : status === "rejected"
                      ? "destructive"
                      : status === "new"
                      ? "secondary"
                      : "info"
                  }
                >
                  {status}: {count}
                </Badge>
              ))}
              {Object.keys(byStatus).length === 0 && (
                <span className="text-xs text-muted-foreground">
                  No jobs yet — run the pipeline to start
                </span>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
