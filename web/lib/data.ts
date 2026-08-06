import { readFileSync } from "node:fs";
import { join } from "node:path";
import { marked } from "marked";

/**
 * Build-time data loading.
 *
 * Every failure here throws. A measurement page that renders stale or zeroed
 * numbers is worse than one that fails to deploy: the reader cannot tell the
 * difference, and the whole claim of the page is that its numbers are real.
 */

const ROOT = join(process.cwd(), "..");
const SUMMARY = join(process.cwd(), "data", "summary.json");
const POST = join(ROOT, "POST.md");

function fail(what: string, why: string): never {
  throw new Error(
    `\n\n  BUILD FAILED — ${what}\n  ${why}\n\n` +
      `  The page renders sweep results at build time. Rather than deploy a page\n` +
      `  showing stale or empty numbers, this build stops.\n\n` +
      `  Fix: run a sweep, then commit web/data/summary.json.\n` +
      `  Locally:  python analyze.py "results/results-*.json" --targets targets.json \\\n` +
      `              --json web/data/summary.json --classifier "$(git describe --tags)"\n`,
  );
}

export type Bucket = {
  key: string;
  plain: string;
  technical: string;
  n: number;
  pct: number;
  ci: [number, number];
};

export type Summary = {
  generatedAt: string;
  classifier: string;
  runId: string;
  registryTotal: number;
  probed: number;
  adjudicated: number;
  buckets: Bucket[];
  medianInstallMs: number;
  medianBootMs: number;
  distinctSchemaHashes: number;
  policyExcluded: number;
};

// The four buckets, in reading order. Plain English leads: a visitor does not
// know what MCP is, let alone what "undeclared credential requirement" means.
const LABELS: { key: string; plain: string; technical: string }[] = [
  {
    key: "Starts with zero configuration",
    plain: "Works immediately",
    technical: "starts with zero configuration",
  },
  {
    key: "Declares credential needs upfront",
    plain: "Needs a key, and says so",
    technical: "declares credential needs upfront",
  },
  {
    key: "Undeclared credential requirement",
    plain: "Needs a key, doesn’t say so",
    technical: "undeclared requirement",
  },
  {
    key: "Broken outright",
    plain: "Doesn’t start",
    technical: "broken outright",
  },
];

function num(raw: Record<string, unknown>, field: string): number {
  const v = raw[field];
  if (typeof v !== "number" || !Number.isFinite(v)) {
    fail(`summary.json field "${field}" is missing or not a number`, `Got: ${JSON.stringify(v)}`);
  }
  return v as number;
}

export function loadSummary(): Summary {
  let raw: Record<string, unknown>;
  try {
    raw = JSON.parse(readFileSync(SUMMARY, "utf8"));
  } catch (e) {
    fail(
      "web/data/summary.json is missing or is not valid JSON",
      (e as Error).message,
    );
  }

  const adjudicated = num(raw, "adjudicated");
  if (adjudicated <= 0) {
    fail("summary.json reports zero adjudicated servers", "A sweep that probed nothing is not publishable.");
  }

  const rawBuckets = raw.buckets as Record<string, number> | undefined;
  const rawCis = raw.bucket_ci95 as Record<string, [number, number]> | undefined;
  if (!rawBuckets || !rawCis) fail("summary.json has no buckets or bucket_ci95", "Regenerate with analyze.py.");

  const buckets: Bucket[] = LABELS.map(({ key, plain, technical }) => {
    const n = rawBuckets[key];
    const ci = rawCis[key];
    if (typeof n !== "number") fail(`bucket "${key}" missing from summary.json`, "Bucket names changed?");
    if (!Array.isArray(ci) || ci.length !== 2) fail(`CI for "${key}" is malformed`, JSON.stringify(ci));
    return { key, plain, technical, n, pct: (100 * n) / adjudicated, ci: [ci[0], ci[1]] };
  });

  // The buckets partition the adjudicated set. If they don't sum, something
  // upstream changed and every percentage on the page is wrong.
  const sum = buckets.reduce((a, b) => a + b.n, 0);
  if (sum !== adjudicated) {
    fail(
      "buckets do not sum to the adjudicated total",
      `Buckets sum to ${sum}, adjudicated is ${adjudicated}. The partition is broken.`,
    );
  }

  const generatedAt = raw.generated_at;
  const classifier = raw.classifier;
  if (typeof generatedAt !== "string" || !generatedAt) {
    fail("summary.json has no generated_at", "The page must state when the numbers were measured.");
  }
  if (typeof classifier !== "string" || !classifier || classifier === "unknown") {
    fail(
      "summary.json has no classifier tag",
      "The page must state which classifier produced the numbers. Pass --classifier to analyze.py.",
    );
  }

  return {
    generatedAt,
    classifier,
    runId: typeof raw.run_id === "string" ? raw.run_id : "",
    registryTotal: num(raw, "registry_total"),
    probed: num(raw, "probed"),
    adjudicated,
    buckets,
    medianInstallMs: num(raw, "median_install_ms"),
    medianBootMs: num(raw, "median_boot_ms"),
    distinctSchemaHashes: num(raw, "distinct_schema_hashes"),
    policyExcluded: num(raw, "policy_excluded"),
  };
}

export function loadPost(): string {
  let md: string;
  try {
    md = readFileSync(POST, "utf8");
  } catch {
    fail("POST.md is missing from the repo root", "The page renders it as the body below the table.");
  }
  if (!md.trim()) fail("POST.md is empty", "Nothing to render below the table.");
  marked.setOptions({ gfm: true });
  const html = marked.parse(md) as string;
  // A wide markdown table would otherwise scroll the whole page sideways on a
  // phone, which is where most of this traffic will read it. Give the table its
  // own scroll container instead of letting it push the body.
  return html.replace(
    /<table>([\s\S]*?)<\/table>/g,
    '<div class="prose-scroll"><table>$1</table></div>',
  );
}
