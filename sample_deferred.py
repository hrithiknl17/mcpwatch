#!/usr/bin/env python3
"""Blank verdict sheet for hand-auditing the deferred-v1 classifier.

The DEFERRED_* rate corrects a published figure, so it gets a measured precision
before it ships -- the same rule as Audit A and Audit B in LIMITATIONS.md. This
draws a fresh-seeded sample from a --tool-call run, prints the full error text
the classifier saw, and leaves the verdict blank.

STRATIFIED BY DEFAULT, IN BOTH DIRECTIONS
-----------------------------------------
Half the sheet is rows the classifier called DEFERRED_* and half is rows it did
not. Auditing only the positives measures precision and hides misses; the
residual half is what caught Audit B's 17 errors. An unstratified draw of 30 from
a sample where most calls succeed would spend most of the sheet confirming
TOOL_CALL_OK rows, which teaches nothing.

  python sample_deferred.py "sp/sp-*.json" -n 30 --seed 90714 \\
      --template audit_deferred30.csv > audit_deferred30.txt

The seed is required and printed, so a second rater can redraw the same sheet
and publish a competing figure.
"""
import argparse
import glob
import json
import random
import sys

sys.path.insert(0, ".")
from deferred import classify, DEFERRED  # noqa: E402

RULE = "=" * 78


def load(patterns):
    rows, seen = [], set()
    for pat in patterns:
        for fp in sorted(glob.glob(pat)):
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
            for r in (data if isinstance(data, list) else [data]):
                key = (r.get("server"), r.get("identifier"), r.get("version"))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="+")
    ap.add_argument("-n", type=int, default=30)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--template", help="also write a blank CSV of verdicts")
    ap.add_argument("--unstratified", action="store_true",
                    help="draw from all called rows instead of half/half")
    args = ap.parse_args()

    rows = [r for r in load(args.results)
            if r.get("tool_call_attempted")
            and not str(r.get("server", "")).startswith(("_fixture", "fx/"))]
    called = []
    for r in rows:
        cls, _ = classify(r)
        if cls == "NO_SAFE_PROBE":
            continue          # nothing was called; there is no verdict to give
        r = dict(r, _cls=cls)
        called.append(r)
    if not called:
        print("no called rows found", file=sys.stderr)
        return 1

    rng = random.Random(args.seed)
    pos = [r for r in called if r["_cls"] in DEFERRED]
    neg = [r for r in called if r["_cls"] not in DEFERRED]
    if args.unstratified:
        sample = rng.sample(called, min(args.n, len(called)))
        design = "unstratified, %d of %d called rows" % (len(sample), len(called))
    else:
        half = args.n // 2
        # Short strata spend their shortfall on the other side, so the sheet is
        # always n rows -- otherwise a run with few positives silently returns a
        # smaller audit than the one that was commissioned.
        take_pos = min(half, len(pos))
        take_neg = min(args.n - take_pos, len(neg))
        take_pos = min(args.n - take_neg, len(pos))
        sample = rng.sample(pos, take_pos) + rng.sample(neg, take_neg)
        rng.shuffle(sample)
        design = ("stratified: %d of %d DEFERRED_*, %d of %d other called rows"
                  % (take_pos, len(pos), take_neg, len(neg)))

    out = sys.stdout
    try:                       # error text is arbitrary publisher output
        out.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    print(RULE)
    print("DEFERRED-REQUIREMENT AUDIT SAMPLE   classifier: deferred-v1")
    print("%s (seed %d)" % (design, args.seed))
    print()
    print("Every row below is a server that handshook, listed tools, and had ONE")
    print("tool called with the arguments shown. For each row decide what the")
    print("error text actually shows, then mark the classifier CORRECT or WRONG.")
    print()
    print("CORRECT for DEFERRED_CREDENTIALS means: the tool refused because a")
    print("credential was missing, and the registry entry never declared it.")
    print("WRONG includes -- a broken tool misread as a credential demand; a")
    print("failure caused by the empty arguments WE sent (that is")
    print("PROBE_ARGS_FAULT); a remote dependency being down; and a genuine")
    print("deferred credential the classifier called something else.")
    print(RULE)

    for i, r in enumerate(sample, 1):
        print()
        print("[%02d] %s" % (i, r.get("server")))
        print("     package  : %s@%s" % (r.get("identifier"), r.get("version")))
        print("     tool     : %s   (selected by: %s)"
              % (r.get("probe_tool"), r.get("probe_rule")))
        print("     arguments: %s" % json.dumps(r.get("probe_args")))
        print("     probe says : %s" % r.get("tool_call_class"))
        print("     classifier : %s" % r["_cls"])
        print("     --- error text the classifier read ---")
        text = (r.get("tool_call_detail") or "").rstrip()
        if not text:
            print("     (empty -- the classification rests on nothing)")
        for ln in text.splitlines():
            print("     | " + ln)
        print("     verdict  : [ ] CORRECT   [ ] WRONG   true class: _______________")
        print("     notes    : ___________________________________________________")
        print("     " + "-" * 70)

    print()
    print(RULE)
    print("Precision to publish = CORRECT / %d, reported separately for the" % len(sample))
    print("DEFERRED_* half and the residual half.")
    print(RULE)

    if args.template:
        with open(args.template, "w", encoding="utf-8", newline="") as f:
            f.write("server,identifier,tool,classifier_class,verdict,true_class,notes\n")
            for r in sample:
                f.write('"%s","%s","%s","%s",,,\n' % (
                    r.get("server"), r.get("identifier"),
                    r.get("probe_tool"), r["_cls"]))
        print("blank verdict sheet -> %s" % args.template, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
