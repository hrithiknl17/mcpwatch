#!/usr/bin/env python3
"""
Publisher concentration and distinct-problem counts.

Two questions a row count cannot answer:

  1. How much of the registry is a handful of publishers? If ten accounts are a
     large share of all entries, every registry-wide percentage is really a
     statement about those ten.

  2. How many DISTINCT problems are there? One publisher shipping seventeen
     packages from one template, each missing the same env var, is one mistake
     published seventeen times. Counting it as seventeen findings overstates the
     result and the first careful reader will say so.

  python concentration.py "results/results-*.json" --targets targets.json --top 10
"""
import argparse, glob, json, re, sys
from collections import Counter, defaultdict

# Volatile substrings that differ between two copies of the SAME mistake:
# temp dirs, versions, package names, pids, timings, hex ids. Strip them before
# fingerprinting or every row looks unique and the dedupe silently does nothing.
_NOISE = [
    (re.compile(r"[A-Za-z]:\\[^\s'\"]+|/(?:tmp|home|Users|runner)/[^\s'\"]+"), "<PATH>"),
    (re.compile(r"\bv?\d+\.\d+\.\d+(?:-[\w.]+)?\b"), "<VER>"),
    (re.compile(r"\b[0-9a-f]{7,}\b", re.I), "<HEX>"),
    (re.compile(r"\b\d+\s*ms\b|\bwithin \d+s\b"), "<TIME>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}T[\d_:.]+Z?\b"), "<TS>"),
    (re.compile(r"\(node:\d+\)"), "(node:<PID>)"),
    (re.compile(r"\s+"), " "),
]

# The identifier is the single strongest per-row difference; remove the package's
# own name so sibling packages from one template collapse together.
def _strip_identity(text, r):
    for tok in filter(None, [r.get("identifier"), r.get("server"),
                             (r.get("identifier") or "").split("/")[-1]]):
        text = text.replace(tok, "<PKG>")
    return text


def signature(r, width=200):
    """Normalised (class, stderr-shape) fingerprint for one result row."""
    raw = (r.get("error_stderr") or r.get("error_detail") or "")
    raw = _strip_identity(raw, r)
    for pat, sub in _NOISE:
        raw = pat.sub(sub, raw)
    return (r.get("error_class") or ("PASS" if r.get("ok") else "UNKNOWN"), raw.strip()[:width])


def load(patterns):
    rows = []
    for pat in patterns:
        for fp in glob.glob(pat):
            with open(fp, encoding="utf-8") as f:
                d = json.load(f)
            rows += d if isinstance(d, list) else [d]
    seen, out = set(), []
    for r in rows:
        k = (r.get("server"), r.get("identifier"), r.get("version"))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def publisher(x, key="server"):
    return str(x.get(key) or "?").split("/")[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+")
    ap.add_argument("--targets", help="targets.json -- concentration over the WHOLE registry")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--only", help="restrict the dedupe to one error_class")
    ap.add_argument("--excerpt", type=int, default=150, help="stderr chars per signature")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    rows = [r for r in load(args.results)
            if not str(r.get("server", "")).startswith(("_fixture", "fx/"))]

    # --- 3. concentration across the registry itself ------------------------
    if args.targets:
        with open(args.targets, encoding="utf-8") as f:
            tgts = json.load(f)
        pubs = Counter(publisher(t, "server_name") for t in tgts)
        n = len(tgts)
        top = pubs.most_common(args.top)
        print("PUBLISHER CONCENTRATION (all %d npm+stdio registry entries)" % n)
        print("%-42s %6s %8s" % ("publisher", "entries", "share"))
        for p, c in top:
            print("%-42s %6d %7.2f%%" % (p[:42], c, 100.0 * c / n))
        cum = sum(c for _, c in top)
        print("%-42s %6d %7.2f%%" % ("-- top %d combined" % len(top), cum, 100.0 * cum / n))
        print("%-42s %6d" % ("distinct publishers", len(pubs)))
        singles = sum(1 for c in pubs.values() if c == 1)
        print("%-42s %6d %7.2f%% of publishers" % ("publishers with exactly 1 entry",
                                                   singles, 100.0 * singles / len(pubs)))
        print()

    # --- 4. distinct problems, not rows -------------------------------------
    if args.only:
        failing = [r for r in rows if r.get("error_class") == args.only]
    else:
        failing = [r for r in rows if not r.get("ok")
                   and r.get("error_class") != "SKIPPED_NEEDS_CREDENTIALS"]
    by_pub = defaultdict(list)
    for r in failing:
        by_pub[publisher(r)].append(r)

    print("DISTINCT PROBLEMS PER PUBLISHER (non-passing, excl. declared-credential skips)")
    print("%-38s %5s %9s %8s" % ("publisher", "rows", "distinct", "collapse"))
    ranked = sorted(by_pub.items(), key=lambda kv: -len(kv[1]))[:args.top]
    tot_rows = tot_sig = 0
    for p, rs in ranked:
        sigs = {signature(r) for r in rs}
        tot_rows += len(rs)
        tot_sig += len(sigs)
        print("%-38s %5d %9d %7.1fx" % (p[:38], len(rs), len(sigs), len(rs) / len(sigs)))
    if tot_sig:
        print("%-38s %5d %9d %7.1fx" % ("-- top %d combined" % len(ranked),
                                        tot_rows, tot_sig, tot_rows / tot_sig))

    all_sigs = {signature(r) for r in failing}
    print()
    print("registry-wide: %d non-passing rows -> %d distinct failure signatures (%.1fx)"
          % (len(failing), len(all_sigs), len(failing) / len(all_sigs) if all_sigs else 0))

    print()
    # Over-collapse check: if the global signature count is far below the sum of
    # per-publisher counts, the fingerprint is merging distinct authors' distinct
    # bugs into one bucket and the "distinct problems" number is too low.
    per_pub_total = sum(len({signature(r) for r in rs}) for rs in by_pub.values())
    print()
    print("OVER-COLLAPSE CHECK")
    print("  sum of per-publisher distinct signatures : %d" % per_pub_total)
    print("  global distinct signatures               : %d" % len(all_sigs))
    print("  signatures shared across publishers      : %d" % (per_pub_total - len(all_sigs)))
    print()
    print("MOST-REPEATED SIGNATURES")
    sig_rows = defaultdict(list)
    for r in failing:
        sig_rows[signature(r)].append(r)
    for i, (((cls, shape), rs)) in enumerate(
            sorted(sig_rows.items(), key=lambda kv: -len(kv[1]))[:args.top], 1):
        pubset = {publisher(r) for r in rs}
        print("  [%02d] %4d rows  %-22s  %d publisher(s)" % (i, len(rs), cls, len(pubset)))
        print("       ex: %s" % (rs[0].get("server")))
        print("       %s" % (shape[:args.excerpt] or "(EMPTY STDERR)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
