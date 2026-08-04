#!/usr/bin/env python3
"""
MCPwatch result analyser.

Merges shard result files and prints the milestone-1 number. The headline block
is meant to be copy-pasted verbatim into the launch post, so every figure in it
has to survive a hostile reading.

  python analyze.py results-*.json --targets targets.json
"""
import argparse, glob, json, statistics as st, sys
from collections import Counter, defaultdict

# Buckets kept OUT of the denominator: the server is not broken, it is unconfigured.
# Each needs something from the operator that a blind probe cannot supply, so
# counting them as failures would overstate the headline (gotcha 1, generalised).
# The declared/undeclared split is the interesting part -- undeclared means the
# registry metadata is wrong, which is a finding in its own right.
EXCLUDED = {
    "SKIPPED_NEEDS_CREDENTIALS": "credentials declared",
    "UNDECLARED_CREDENTIALS":    "credentials UNDECLARED",
    "UNDECLARED_ARGS":           "args UNDECLARED (printed usage)",
    "NEEDS_LOCAL_SETUP":         "local setup (config/vault dir)",
}

# NO_ENTRYPOINT stays a failure on purpose: nothing the operator supplies fixes a
# package that ships no runnable binary. It is the most actionable class here.
# Fixed order so the block reads the same every run rather than reshuffling by count.
CLASS_ORDER = ["PASS", "INSTALL_FAILED", "INSTALL_TIMEOUT", "NO_ENTRYPOINT",
               "CRASH_ON_START", "INIT_TIMEOUT", "INIT_RPC_ERROR", "ZERO_TOOLS",
               "TOOLS_TIMEOUT", "TOOLS_RPC_ERROR", "COMMAND_NOT_FOUND", "PROBE_EXCEPTION"]


def load(paths):
    """Expand globs, merge, and drop duplicate probes of the same target.

    Shards should be disjoint, but a re-run or an overlapping matrix would
    silently double-count and skew every percentage -- so dedupe explicitly.
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


def median(xs):
    return int(st.median(xs)) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+", help="result JSON files or globs")
    ap.add_argument("--targets", help="targets.json, to report sweep coverage")
    ap.add_argument("--json", dest="as_json", help="also write machine-readable summary here")
    ap.add_argument("--top-publishers", type=int, default=5)
    args = ap.parse_args()

    rows, nfiles, dupes = load(args.results)
    if not rows:
        print("no results loaded", file=sys.stderr)
        return 2

    # Fixtures are a self-check, not data. Pull them out before counting anything.
    fixtures = [r for r in rows if str(r.get("server", "")).startswith("_fixture")]
    rows = [r for r in rows if not str(r.get("server", "")).startswith("_fixture")]

    fixture_bad = []
    for r in fixtures:
        got = "PASS" if r.get("ok") else r.get("error_class")
        want = "PASS" if "does-not-exist" not in r.get("server", "") else "INSTALL_FAILED"
        if got != want:
            fixture_bad.append((r.get("server"), want, got))

    excluded = {k: [r for r in rows if r.get("error_class") == k] for k in EXCLUDED}
    probeable = [r for r in rows if r.get("error_class") not in EXCLUDED]

    counts = Counter("PASS" if r.get("ok") else (r.get("error_class") or "UNKNOWN")
                     for r in probeable)
    failures = sum(v for k, v in counts.items() if k != "PASS")

    installs = [r["t_install_ms"] for r in rows if r.get("t_install_ms")]
    boots = [r["t_boot_ms"] for r in rows if r.get("t_boot_ms")]
    polluted = sum(1 for r in rows if r.get("stdout_polluted"))

    total_line = len(rows)
    coverage = ""
    if args.targets:
        with open(args.targets, encoding="utf-8") as f:
            tgts = json.load(f)
        total_line = len(tgts)
        coverage = f"  Probed this run:                     {len(rows)} ({pct(len(rows), len(tgts))})\n"

    ordered = [k for k in CLASS_ORDER if counts.get(k)] + \
              sorted(k for k in counts if k not in CLASS_ORDER)

    out = []
    out.append("Total npm+stdio servers (latest):     %d" % total_line)
    if coverage:
        out.append(coverage.rstrip("\n"))
    out.append("  Probeable:                          %d" % len(probeable))
    for k, label in EXCLUDED.items():
        out.append("  Excl. %-34s %d" % (label + ":", len(excluded[k])))
    out.append("")
    out.append("Of probeable:")
    for k in ordered:
        out.append("  %-34s %d (%s)" % (k, counts[k], pct(counts[k], len(probeable))))
    out.append("")
    out.append("FAILURE RATE:                         %s" % pct(failures, len(probeable)))
    out.append("")
    mi, mb = median(installs), median(boots)
    out.append("Median install: %sms   Median boot: %sms" % (mi if mi else "-", mb if mb else "-"))
    out.append("stdout spec violations:               %d" % polluted)
    block = "\n".join(out)

    print(block)
    sys.stdout.flush()  # block goes first even when stdout is piped and stderr isn't

    # --- caveats: everything that makes the block above defensible -------------
    print("\n" + "-" * 60, file=sys.stderr)
    print("checks (not part of the block):", file=sys.stderr)
    print("  result files merged:   %d  (duplicate rows dropped: %d)" % (nfiles, dupes), file=sys.stderr)
    if fixtures:
        state = "PASS" if not fixture_bad else "MISMATCH"
        print("  fixtures:              %s (%d checked)" % (state, len(fixtures)), file=sys.stderr)
        for name, want, got in fixture_bad:
            print("     %s: wanted %s, got %s" % (name, want, got), file=sys.stderr)
    else:
        print("  fixtures:              ABSENT -- run without --no-fixtures", file=sys.stderr)

    # One prolific publisher can carry the whole failure rate. Say so before
    # someone else does.
    by_pub = defaultdict(int)
    for r in probeable:
        if not r.get("ok"):
            by_pub[str(r.get("server", "?")).split("/")[0]] += 1
    top = sorted(by_pub.items(), key=lambda kv: -kv[1])[:args.top_publishers]
    if top and failures:
        print("  failure concentration:", file=sys.stderr)
        for pub, n in top:
            print("     %-34s %d (%s of all failures)" % (pub, n, pct(n, failures)), file=sys.stderr)
        if top[0][1] / failures > 0.20:
            print("     ^ top publisher is >20% of failures -- report this alongside the rate",
                  file=sys.stderr)
    meta_gap = len(excluded["UNDECLARED_CREDENTIALS"]) + len(excluded["UNDECLARED_ARGS"])
    if meta_gap:
        print("  NOTE: %d servers needed credentials or arguments the registry never" % meta_gap,
              file=sys.stderr)
        print("        declared. Excluded from the rate -- that metadata gap is itself",
              file=sys.stderr)
        print("        a finding, and arguably the more interesting one.", file=sys.stderr)

    if args.as_json:
        with open(args.as_json, "w", encoding="utf-8") as f:
            json.dump({
                "total_targets": total_line, "probed": len(rows),
                "probeable": len(probeable),
                "excluded": {k: len(v) for k, v in excluded.items()},
                "counts": dict(counts), "failures": failures,
                "failure_rate": (failures / len(probeable)) if probeable else None,
                "median_install_ms": mi, "median_boot_ms": mb,
                "stdout_polluted": polluted,
                "fixtures_ok": not fixture_bad,
                "failure_concentration": dict(top),
            }, f, indent=2)

    # A blown fixture means the sweep's numbers are not trustworthy -- fail loudly
    # so CI cannot publish a rate produced by a broken probe.
    return 1 if fixture_bad else 0


if __name__ == "__main__":
    sys.exit(main())
