"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Play, Settings2, ChevronDown, ChevronUp } from "lucide-react";
import type { PipelineTriggerRequest, RoleConfig } from "@/lib/api";

const ROLES = [
  { key: "backend",   label: "Backend"    },
  { key: "fullstack", label: "Full Stack" },
  { key: "aiml",      label: "AI/ML"      },
] as const;

const DEFAULT_HOURS  = 24;
const DEFAULT_COUNT  = 10;
const DEFAULT_SOURCE = "jobright" as "jobright" | "indeed";

interface SearchConfigPanelProps {
  running: boolean;
  onRun: (config: PipelineTriggerRequest) => void;
}

export function SearchConfigPanel({ running, onRun }: SearchConfigPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [source,   setSource]   = useState<"jobright" | "indeed">(DEFAULT_SOURCE);
  const [hoursOld, setHoursOld] = useState<number>(DEFAULT_HOURS);
  const [roles,    setRoles]    = useState<Record<string, RoleConfig>>({
    backend:   { enabled: true, count: DEFAULT_COUNT },
    fullstack: { enabled: true, count: DEFAULT_COUNT },
    aiml:      { enabled: true, count: DEFAULT_COUNT },
  });

  const toggleRole = (key: string) =>
    setRoles((prev) => ({ ...prev, [key]: { ...prev[key], enabled: !prev[key].enabled } }));

  const setCount = (key: string, count: number) =>
    setRoles((prev) => ({ ...prev, [key]: { ...prev[key], count } }));

  // Both sources respect the enabled toggle
  const totalJobs = ROLES.reduce(
    (s, r) => s + (roles[r.key].enabled ? roles[r.key].count : 0),
    0
  );

  const handleRun = () => {
    onRun({ source, hours_old: hoursOld, roles });
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2">
            <Settings2 className="h-4 w-4 text-muted-foreground" />
            Search Configuration
          </CardTitle>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 transition-colors"
          >
            {expanded ? (
              <>Collapse <ChevronUp className="h-3.5 w-3.5" /></>
            ) : (
              <>Expand <ChevronDown className="h-3.5 w-3.5" /></>
            )}
          </button>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Always-visible summary row */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Source badge summary */}
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Badge
              variant={source === "jobright" ? "success" : "info"}
              className="text-xs font-medium"
            >
              {source === "jobright" ? "Jobright" : "Indeed"}
            </Badge>
            <span>·</span>
            {expanded ? (
              <>
                <span>Last</span>
                <input
                  type="number"
                  min={1}
                  max={72}
                  value={hoursOld}
                  onChange={(e) =>
                    setHoursOld(Math.min(72, Math.max(1, Number(e.target.value))))
                  }
                  className="w-14 px-2 py-0.5 text-sm rounded-md border border-input bg-background focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
                />
                <span>hours</span>
              </>
            ) : (
              <span>Last <Badge variant="secondary">{hoursOld}h</Badge></span>
            )}
            <span>· Up to</span>
            <Badge variant="secondary">{totalJobs} jobs</Badge>
            {source === "jobright" && (
              <span className="text-xs text-muted-foreground hidden sm:inline">
                (using your saved Jobright filters)
              </span>
            )}
          </div>

          <div className="ml-auto">
            <Button
              size="sm"
              onClick={handleRun}
              disabled={running || totalJobs === 0}
              className="gap-1.5"
            >
              <Play className="h-3.5 w-3.5" />
              Run Job Search
            </Button>
          </div>
        </div>

        {/* Expanded config */}
        {expanded && (
          <div className="space-y-4 pt-1 border-t border-border">
            {/* Source selector */}
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-2">Source</p>
              <div className="flex gap-2">
                {(["jobright", "indeed"] as const).map((s) => (
                  <button
                    key={s}
                    onClick={() => setSource(s)}
                    className={`px-4 py-1.5 rounded-full text-sm font-medium border transition-colors ${
                      source === s
                        ? "bg-primary text-primary-foreground border-primary"
                        : "bg-background text-muted-foreground border-border hover:border-foreground"
                    }`}
                  >
                    {s === "jobright" ? "Jobright (Recommended)" : "Indeed (Keyword search)"}
                  </button>
                ))}
              </div>
              {source === "jobright" && (
                <p className="text-xs text-muted-foreground mt-2">
                  Scrapes your Jobright Recommended feed using your saved filters.
                  Set per-category job counts below. Category is inferred from job title.
                </p>
              )}
              {source === "indeed" && (
                <p className="text-xs text-muted-foreground mt-2">
                  Keyword-based scraping from Indeed.com. Enable/disable categories and set job counts.
                </p>
              )}
            </div>

            {/* Per-role config — toggles for both sources */}
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-2">Categories</p>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {ROLES.map(({ key, label }) => {
                  const cfg    = roles[key];
                  const dimmed = !cfg.enabled;
                  return (
                    <div
                      key={key}
                      className={`rounded-lg border p-3 transition-colors ${
                        dimmed
                          ? "border-border/50 bg-muted/10 opacity-60"
                          : "border-border bg-muted/30"
                      }`}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-sm font-medium">{label}</span>
                        <button
                          onClick={() => toggleRole(key)}
                          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none ${
                            cfg.enabled ? "bg-primary" : "bg-muted-foreground/30"
                          }`}
                        >
                          <span
                            className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${
                              cfg.enabled ? "translate-x-4" : "translate-x-0.5"
                            }`}
                          />
                        </button>
                      </div>
                      <div className="flex items-center gap-2">
                        <label className="text-xs text-muted-foreground">Jobs:</label>
                        <input
                          type="number"
                          min={1}
                          max={100}
                          value={cfg.count}
                          disabled={!cfg.enabled}
                          onChange={(e) =>
                            setCount(key, Math.min(100, Math.max(1, Number(e.target.value))))
                          }
                          className="w-16 px-2 py-0.5 text-sm rounded-md border border-input bg-background focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50 disabled:cursor-not-allowed text-foreground"
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
