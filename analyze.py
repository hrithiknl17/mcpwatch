#!/usr/bin/env python3
"""
MCPwatch result analyser.

Primary output is a four-bucket partition of every npm+stdio target. The framing
is deliberate: the headline finding is that a measurable share of the registry
declares nothing about credentials it actually requires. That is a structural
defect in registry metadata, not developer error, and it is invisible to anyone
who only reports a pass/fail rate.

Failure-rate-of-probeable is printed too, but as a secondary line. It is the
number people reach for first and the one most likely to be quoted out of
context, so it does not get the top slot.

  python analyze.py "results-*.json" --targets targets.json
"""
import argparse, glob, json, random, statistics as st, sys
from collections import Counter, defaultdict

# --- the four buckets -------------------------------------------------------
# Every adjudicated target lands in exactly one. Order is the reading order of
# the published block, not severity.
BUCKET_ZEROCONF = "Starts with zero configuration"
BUCKET_DECLARED = "Declares credential needs upfront"
BUCKET_UNDECLARED = "Undeclared credential requirement"
BUCKET_BROKEN = "Broken outright"

# Undeclared args and missing local setup are the same structural defect as an
# undeclared credential: the registry entry omits something the server demands at
# startup. They are counted in that bucket and itemised beneath it, so the
# headline stays honest about what it is aggregating.
UNDECLARED_CLASSES = ("UNDECLARED_CREDENTIALS", "UNDECLARED_ARGS", "NEEDS_LOCAL_SETUP")

# NOT a server defect and NOT adjudicable: we chose --ignore-scripts, so we never
# gave these packages the build step they declare. Counting them as broken would
# blame publishers for our own security policy. Reported separately, outside the
# partition.
POLICY_EXCLUDED = ("BUILD_SCRIPTS_REQUIRED",)

BROKEN_ORDER = ["INSTALL_FAILED", "INSTALL_TIMEOUT", "NO_ENTRYPOINT", "CRASH_ON_START",
                "INIT_TIMEOUT", "INIT_RPC_ERROR", "ZERO_TOOLS", "TOOLS_TIMEOUT",
                "TOOLS_RPC_ERROR", "COMMAND_NOT_FOUND", "PROBE_EXCEPTION"]


def load(paths):
    """Expand globs, merge, drop duplicate probes of the same target.

    Shards should be disjoint, but a re-run or an overlapping matrix would
    double-count silently and skew every percentage -- so dedupe explicitly.
    """
    rows, files = [], []
    for pat in paths:
        hits = glob.glob(pat)
        if not hits:
            print(f"warning: no files matched {pat!r}", file=sys.stderr)
        files += hits
    for fp in sorted(set(files)):
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        rows += data if isinstance(data, list) else [data]
    seen, merged, dupes = set(), [], 0
    for r in rows:
        key = (r.get("server"), r.get("identifier"), r.get("version"))
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        merged.append(r)
    return merged, len(files), dupes


def pct(n, d):
    return f"{100.0 * n / d:.1f}%" if d else "-"


def bucket_of(r):
    cls = r.get("error_class")
    if cls in POLICY_EXCLUDED:
        return None
    if r.get("ok"):
        return BUCKET_ZEROCONF
    if cls == "SKIPPED_NEEDS_CREDENTIALS":
        return BUCKET_DECLARED
    if cls in UNDECLARED_CLASSES:
        return BUCKET_UNDECLARED
    return BUCKET_BROKEN


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+", help="result JSON files or globs")
    ap.add_argument("--targets", help="targets.json, for sweep coverage")
    ap.add_argument("--json", dest="as_json", help="also write machine-readable summary")
    ap.add_argument("--top-publishers", type=int, default=5)
    args = ap.parse_args()

    rows, nfiles, dupes = load(args.results)
    if not rows:
        print("no results loaded", file=sys.stderr)
        return 2

    fixtures = [r for r in rows if str(r.get("server", "")).startswith(("_fixture", "fx/"))]
    rows = [r for r in rows if not str(r.get("server", "")).startswith(("_fixture", "fx/"))]
    fixture_bad = []
    for r in fixtures:
        got = "PASS" if r.get("ok") else r.get("error_class")
        want = "INSTALL_FAILED" if "nonexistent" in r.get("server", "") or \
                                   "does-not-exist" in r.get("server", "") else None
        if want and got != want:
            fixture_bad.append((r.get("server"), want, got))

    policy = [r for r in rows if r.get("error_class") in POLICY_EXCLUDED]
    adjudicated = [r for r in rows if bucket_of(r) is not None]
    buckets = defaultdict(list)
    for r in adjudicated:
        buckets[bucket_of(r)].append(r)

    n = len(adjudicated)
    broken = buckets[BUCKET_BROKEN]
    undeclared = buckets[BUCKET_UNDECLARED]
    probeable = buckets[BUCKET_ZEROCONF] + broken

    installs = [r["t_install_ms"] for r in rows if r.get("t_install_ms")]
    boots = [r["t_boot_ms"] for r in rows if r.get("t_boot_ms")]
    polluted = sum(1 for r in rows if r.get("stdout_polluted"))
    hashes = {r["schema_hash"] for r in rows if r.get("schema_hash")}

    out = []
    if args.targets:
        with open(args.targets, encoding="utf-8") as f:
            tgts = json.load(f)
        out.append("npm+stdio servers in the official MCP registry: %d" % len(tgts))
        out.append("Probed: %d (%s)" % (len(rows), pct(len(rows), len(tgts))))
        out.append("")

    out.append("Of %d servers probed:" % n)
    out.append("")
    for b in (BUCKET_ZEROCONF, BUCKET_DECLARED, BUCKET_UNDECLARED, BUCKET_BROKEN):
        out.append("  %-34s %4d (%s)" % (b, len(buckets[b]), pct(len(buckets[b]), n)))
        if b == BUCKET_UNDECLARED and buckets[b]:
            sub = Counter(r.get("error_class") for r in buckets[b])
            for k in UNDECLARED_CLASSES:
                if sub.get(k):
                    out.append("      %-30s %4d" % (k.lower().replace("_", " "), sub[k]))
    out.append("")

    if broken:
        out.append("  Broken outright, by class:")
        order = [k for k in BROKEN_ORDER if any(r.get("error_class") == k for r in broken)]
        cnt = Counter(r.get("error_class") for r in broken)
        for k in order + sorted(k for k in cnt if k not in BROKEN_ORDER):
            out.append("      %-30s %4d (%s of all probed)" % (k, cnt[k], pct(cnt[k], n)))
        out.append("")

    mi = int(st.median(installs)) if installs else None
    mb = int(st.median(boots)) if boots else None
    # never summed: the combined figure is ~all download and says nothing about
    # how fast a server actually starts
    out.append("  Median install time (npm):      %s ms" % (mi if mi is not None else "-"))
    out.append("  Median boot time (handshake):   %s ms" % (mb if mb is not None else "-"))
    out.append("  stdout spec violations:         %d" % polluted)
    out.append("  distinct tool schema hashes:    %d" % len(hashes))
    if policy:
        out.append("  not adjudicated (build scripts skipped by policy): %d" % len(policy))
    out.append("")
    out.append("  Secondary: failure rate among servers that need no configuration")
    out.append("             to start: %s (%d of %d)"
               % (pct(len(broken), len(probeable)), len(broken), len(probeable)))
    block = "\n".join(out)
    print(block)
    sys.stdout.flush()

    # --- checks, kept off stdout so the block pastes clean ------------------
    e = lambda s: print(s, file=sys.stderr)
    e("\n" + "-" * 62)
    e("checks (not part of the block):")
    e("  files merged: %d   duplicate rows dropped: %d" % (nfiles, dupes))
    if fixtures:
        e("  fixtures: %s (%d checked)" % ("PASS" if not fixture_bad else "MISMATCH", len(fixtures)))
        for name, want, got in fixture_bad:
            e("     %s: wanted %s, got %s" % (name, want, got))
    else:
        e("  fixtures: ABSENT -- run without --no-fixtures")
    by_pub = Counter(str(r.get("server", "?")).split("/")[0] for r in broken)
    if by_pub:
        e("  failure concentration:")
        for pub, c in by_pub.most_common(args.top_publishers):
            e("     %-34s %d (%s of broken)" % (pub, c, pct(c, len(broken))))
        if by_pub.most_common(1)[0][1] / len(broken) > 0.20:
            e("     ^ top publisher >20% of failures -- report alongside the rate")
    if undeclared:
        e("  %d undeclared-requirement rows rest on a stderr heuristic." % len(undeclared))
        e("  Audit a sample with sample_undeclared.py before publishing accuracy.")

    if args.as_json:
        with open(args.as_json, "w", encoding="utf-8") as f:
            json.dump({
                "probed": len(rows), "adjudicated": n,
                "buckets": {b: len(buckets[b]) for b in
                            (BUCKET_ZEROCONF, BUCKET_DECLARED, BUCKET_UNDECLARED, BUCKET_BROKEN)},
                "undeclared_breakdown": dict(Counter(r.get("error_class") for r in undeclared)),
                "broken_breakdown": dict(Counter(r.get("error_class") for r in broken)),
                "policy_excluded": len(policy),
                "median_install_ms": mi, "median_boot_ms": mb,
                "stdout_polluted": polluted, "distinct_schema_hashes": len(hashes),
                "secondary_failure_rate": (len(broken) / len(probeable)) if probeable else None,
                "fixtures_ok": not fixture_bad,
            }, f, indent=2)

    # a blown fixture means the numbers above are not trustworthy -- fail loudly
    # so CI cannot publish a rate produced by a broken probe
    return 1 if fixture_bad else 0


if __name__ == "__main__":
    sys.exit(main())
