#!/usr/bin/env python3
"""
Pull a random sample of undeclared-requirement rows for manual audit.

The UNDECLARED_* classes come from a stderr heuristic validated by hand on a few
dozen rows. Across thousands of publishers phrasing their errors differently it
WILL drift, in both directions: missed rows inflate the failure rate, false
positives deflate it. So the accuracy gets measured and published next to the
stat rather than assumed.

Emit a sample, read the stderr yourself, and record a verdict per row.

  python sample_undeclared.py "results-*.json" -n 20 > audit.txt
  python sample_undeclared.py "results-*.json" --template audit.csv
"""
import argparse, glob, json, random, sys

AUDITABLE = ("UNDECLARED_CREDENTIALS", "UNDECLARED_ARGS", "NEEDS_LOCAL_SETUP")
RULE = "=" * 78


def load(patterns):
    rows = []
    for pat in patterns:
        for fp in glob.glob(pat):
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
            rows += data if isinstance(data, list) else [data]
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+")
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=None,
                    help="fix for a reproducible sample; omit for a fresh draw")
    ap.add_argument("--only", choices=AUDITABLE, help="restrict to one class")
    ap.add_argument("--template", help="also write a blank CSV to record verdicts")
    args = ap.parse_args()

    rows = [r for r in load(args.results)
            if r.get("error_class") in (AUDITABLE if not args.only else (args.only,))]
    if not rows:
        print("no undeclared-requirement rows found", file=sys.stderr)
        return 1

    rng = random.Random(args.seed)
    sample = rng.sample(rows, min(args.n, len(rows)))

    out = sys.stdout
    try:                       # stderr text is arbitrary publisher output
        out.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    print(RULE)
    print("UNDECLARED-REQUIREMENT AUDIT SAMPLE")
    print("%d of %d eligible rows%s" %
          (len(sample), len(rows), "" if args.seed is None else " (seed %d)" % args.seed))
    print()
    print("For each row: does the stderr actually show the server refusing to start")
    print("because something it needs was not supplied? Mark CORRECT or WRONG.")
    print("WRONG includes: a real crash misread as a missing requirement, or a")
    print("requirement the registry DID declare (that is a sync bug, not a finding).")
    print(RULE)

    for i, r in enumerate(sample, 1):
        print()
        print("[%02d] %s" % (i, r.get("server")))
        print("     package : %s@%s" % (r.get("identifier"), r.get("version")))
        print("     class    : %s" % r.get("error_class"))
        print("     stage    : %s" % r.get("stage_failed"))
        print("     cmd      : %s" % (r.get("cmd"),))
        print("     --- captured stderr ---")
        text = (r.get("error_stderr") or r.get("error_detail") or "").rstrip()
        if not text:
            print("     (empty -- classification rests on nothing, treat as WRONG)")
        for line in text.splitlines():
            print("     | " + line)
        print("     verdict  : [ ] CORRECT   [ ] WRONG   notes: ______________________")
        print("     " + "-" * 70)

    print()
    print(RULE)
    print("Accuracy to publish = CORRECT / %d" % len(sample))
    print(RULE)

    if args.template:
        with open(args.template, "w", encoding="utf-8", newline="") as f:
            f.write("server,identifier,error_class,verdict,notes\n")
            for r in sample:
                f.write('"%s","%s","%s",,\n' %
                        (r.get("server"), r.get("identifier"), r.get("error_class")))
        print("blank verdict sheet -> %s" % args.template, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
