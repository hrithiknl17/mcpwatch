#!/usr/bin/env python3
"""Draw a seeded random sample of zero-config servers, as a probe targets file.

Feeds the deferred-credentials measurement: the published "Starts with zero
configuration" bucket is a handshake result, and a server that defers its
credential check to the first tool call sits in that bucket while being
unusable. Measuring the rate needs a random sample, not the servers that
happened to look interesting.

WHY THE SAMPLE IS DRAWN FROM ALL ZERO-CONFIG SERVERS
----------------------------------------------------
The obvious design is to sample from servers that HAVE a safe-probe candidate.
That set cannot be known before probing: it needs inputSchema.required and tool
annotations, which the sweep does not retain (see safeprobe_coverage.py). So the
frame is every zero-config server, and both rates are reported -- the share with
a candidate at all, and the deferred rate among those. Sampling from a frame
defined by unmeasured data would be a selection effect, which is the exact fault
that makes the earlier 8-server run unable to produce a rate.

  python sample_zeroconf.py "shard*/results-*.json" --targets targets.json \\
      -n 300 --seed 20260907 --out sample300.json

Read-only on the sweep. Emits a targets file; it never probes anything itself.
"""
import argparse
import glob
import json
import random
import sys


def load(patterns):
    """Merge and dedupe on (server, identifier, version), as analyze.py does."""
    rows = []
    for pat in patterns:
        hits = glob.glob(pat)
        if not hits:
            print("warning: no files matched %r" % pat, file=sys.stderr)
        for fp in sorted(hits):
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
            rows += data if isinstance(data, list) else [data]
    seen, merged = set(), []
    for r in rows:
        key = (r.get("server"), r.get("identifier"), r.get("version"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(r)
    return merged


def is_zeroconf(r):
    return bool(r.get("ok")) and (r.get("tool_count") or 0) > 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="+")
    ap.add_argument("--targets", required=True,
                    help="targets.json from the same sweep, for cmd/args/env")
    ap.add_argument("-n", type=int, default=300)
    ap.add_argument("--seed", type=int, required=True,
                    help="required, and recorded in the output -- an unseeded "
                         "sample cannot be re-drawn by a second rater")
    ap.add_argument("--out", required=True, help="targets file to write")
    args = ap.parse_args()

    rows = [r for r in load(args.results)
            if is_zeroconf(r) and not str(r.get("server", "")).startswith(("_fixture", "fx/"))]

    with open(args.targets, encoding="utf-8") as f:
        targets = json.load(f)
    by_key = {(t.get("server_name"), t.get("identifier"), t.get("version")): t
              for t in targets}

    # Sort before sampling. Shard files arrive in filesystem order, so an
    # unsorted frame makes the same seed draw a different sample on a machine
    # that globs differently -- which would quietly break reproducibility.
    frame = sorted(rows, key=lambda r: (r.get("server") or "",
                                        r.get("identifier") or "",
                                        r.get("version") or ""))
    n_frame = len(frame)
    if args.n > n_frame:
        print("warning: n=%d exceeds frame of %d; taking all" % (args.n, n_frame),
              file=sys.stderr)
    pick = random.Random(args.seed).sample(frame, min(args.n, n_frame))

    out, missing = [], 0
    for r in pick:
        t = by_key.get((r.get("server"), r.get("identifier"), r.get("version")))
        if not t:
            missing += 1
            continue
        # required_env/required_args are re-asserted empty: every row here is
        # already zero-config, so probe_target must not short-circuit it into
        # SKIPPED_NEEDS_CREDENTIALS. cmd is rebuilt by the prewarm stage.
        out.append({
            "server_name": t["server_name"], "identifier": t["identifier"],
            "version": t["version"], "cmd": t["cmd"], "args": t.get("args") or [],
            "required_env": [], "required_args": [],
            "_meta": t.get("_meta"),
        })

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print(json.dumps({
        "frame_zero_config_servers": n_frame,
        "requested": args.n,
        "seed": args.seed,
        "drawn": len(out),
        "dropped_no_target_row": missing,
        "out": args.out,
    }, indent=2))


if __name__ == "__main__":
    main()
