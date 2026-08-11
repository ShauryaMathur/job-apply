export const STATUS_OPTIONS = [
  "new",
  "reviewed",
  "applying",
  "applied",
  "rejected",
  "interview",
  "offer",
] as const;

export type JobStatus = (typeof STATUS_OPTIONS)[number];

export const CATEGORY_LABELS: Record<string, string> = {
  backend: "Backend",
  fullstack: "Full Stack",
  aiml: "AI/ML",
};

export const STATUS_VARIANT: Record<
  string,
  "default" | "secondary" | "success" | "warning" | "destructive" | "info" | "purple"
> = {
  new: "secondary",
  reviewed: "info",
  applying: "info",
  applied: "success",
  rejected: "destructive",
  interview: "warning",
  offer: "purple",
};

export const CATEGORY_VARIANT: Record<
  string,
  "default" | "info" | "success" | "purple"
> = {
  backend: "info",
  fullstack: "success",
  aiml: "purple",
};

export const SOURCE_LABELS: Record<string, string> = {
  indeed: "Indeed",
  jobright: "Jobright",
  linkedin: "LinkedIn",
  lever: "Lever",
  greenhouse: "Greenhouse",
  workday: "Workday",
  ashby: "Ashby",
  smartrecruiters: "SmartRecruiters",
  glassdoor: "Glassdoor",
  dice: "Dice",
  icims: "iCIMS",
  taleo: "Taleo",
  bamboohr: "BambooHR",
  wellfound: "Wellfound",
  workable: "Workable",
  recruitee: "Recruitee",
  manual: "Manual",
};

const _SOURCE_KEYWORDS: [string, string][] = [
  ["linkedin", "linkedin"],
  ["lever", "lever"],
  ["greenhouse", "greenhouse"],
  ["myworkday", "workday"],
  ["workday", "workday"],
  ["ashbyhq", "ashby"],
  ["ashby", "ashby"],
  ["smartrecruiters", "smartrecruiters"],
  ["jobright", "jobright"],
  ["indeed", "indeed"],
  ["glassdoor", "glassdoor"],
  ["dice", "dice"],
  ["icims", "icims"],
  ["taleo", "taleo"],
  ["bamboohr", "bamboohr"],
  ["wellfound", "wellfound"],
  ["workable", "workable"],
  ["recruitee", "recruitee"],
];

export function extractSourceFromUrl(url: string): string {
  try {
    const hostname = new URL(url).hostname.toLowerCase().replace(/^www\./, "");
    for (const [keyword, source] of _SOURCE_KEYWORDS) {
      if (hostname.includes(keyword)) return source;
    }
    const parts = hostname.split(".");
    return parts.length >= 2 ? parts[parts.length - 2] : parts[0];
  } catch {
    return "";
  }
}
