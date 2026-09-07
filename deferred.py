#!/usr/bin/env python3
"""Deferred-requirement rate from a --tool-call sample.  classifier: deferred-v1

The published "Starts with zero configuration" bucket is a HANDSHAKE result. A
server that accepts initialize, lists its tools, and only then demands an API key
sits inside that bucket while being unusable with zero configuration. This script
measures how often that happens, from a seeded random sample, and states the
correction to the published figure.

It is a separate classifier from probe.py on purpose. probe.py stays mechanical
-- it records what came back. Deciding that a particular error MEANS a deferred
credential is a heuristic over publisher prose, exactly like the stderr
classifier, and it gets versioned, sampled and hand-audited the same way rather
than being buried inside the probe.

    python deferred.py "sp/sp-*.json" --json summary_deferred.json

CLASSES
  DEFERRED_CREDENTIALS  the call failed demanding a credential nobody declared
  DEFERRED_SETUP        the call failed demanding config/a file/a local resource
  PROBE_ARGS_FAULT      the call failed because OUR arguments were wrong
  TOOL_CALL_OK / _ERROR / _MALFORMED / _TIMEOUT / _CRASH   as probe.py recorded
  NO_SAFE_PROBE         no tool met the SAFE-PROBE.md criteria

PROBE_ARGS_FAULT is excluded from the rate rather than dropped silently. Every
call here was made with empty or declared-only arguments, so a tool that needs a
parameter we did not supply failed because of our method. Counting that as a
publisher defect would manufacture the very overcount this script exists to
correct.
"""
import argparse
import glob
import json
import re
import sys
from collections import Counter

sys.path.insert(0, "probe")
# Reuse of the audited stderr heuristics is deliberate: DEFERRED_CREDENTIALS and
# UNDECLARED_CREDENTIALS are the same judgment about the same kind of prose, and
# a second, differently-worded matcher would need its own separate audit while
# quietly disagreeing with the first. Audit A measured this matcher at 85.0%
# precision [70.9-92.9] on STDERR text; tool-call errors are a different text
# distribution, so that figure does not transfer and this classifier gets its
# own audit (see sample_deferred.py).
from probe import looks_like_missing_credentials, looks_like_needs_setup  # noqa: E402

# Unambiguous "we sent the wrong arguments" signals. Deliberately narrow: these
# are schema-validation and JSON-RPC shapes, not prose. Anything vaguer would
# start absorbing real publisher defects into the excluded bucket and deflate the
# rate, which is as dishonest as inflating it.
_ARGS_FAULT = re.compile(
    r"(invalid[ _-]?(?:params|arguments|input)|"
    r"must have required property|required property|"
    r"missing required (?:parameter|argument|field|property)|"
    r"unrecognized_keys|invalid_type|too_small|"                 # zod
    r"should have required property|"                            # ajv
    r"expected (?:string|number|object|array|boolean), (?:but )?received|"
    r"argument .{0,30}(?:is required|must be provided)|"
    r"provide (?:the|a|an) .{0,40}\bas\b|"                       # "provide X as `y`"
    # anyOf/oneOf schemas: nothing sits in `required`, yet the tool demands one
    # of a set. Seen live as "Provide either username or userId". probe.py now
    # refuses to select those at all, but a server can still phrase a demand
    # this way, and it is our argument gap either way -- not a publisher defect.
    r"provide either\b|either .{0,40} or .{0,40} (?:is |must be )?(?:required|provided)|"
    r"(?:one|at least one) of .{0,60}(?:is |must be )?(?:required|provided|specified)|"
    r"(?:parameter|argument|field) [`'\"]?\w+[`'\"]? is required)", re.I)

# JSON-RPC -32602 is Invalid params by definition. Mechanical, no prose involved.
_INVALID_PARAMS_CODE = -32602

# A paywall is a deferred REQUIREMENT, but it is not a credential and it is not
# local setup: the server works and it wants money. Folding it into
# DEFERRED_CREDENTIALS would overstate the credential finding; dropping it in the
# residual would hide a server that is genuinely unusable with zero
# configuration. x402 is the HTTP-402 micropayment scheme several of these use.
_PAYWALL = re.compile(
    r"(x402|payment required|requires? payment|insufficient (?:credits?|balance|funds)|"
    r"purchase (?:a )?(?:plan|subscription|credits)|subscription required|"
    r"upgrade (?:your|to) .{0,20}plan|free tier .{0,30}exceeded|quota exceeded)", re.I)

PASSTHROUGH = ("TOOL_CALL_OK", "TOOL_CALL_MALFORMED", "TOOL_CALL_TIMEOUT",
               "TOOL_CALL_CRASH", "NO_SAFE_PROBE")
DEFERRED = ("DEFERRED_CREDENTIALS", "DEFERRED_SETUP", "DEFERRED_PAYMENT")


def wilson(k, n, z=1.96):
    """95% Wilson score interval as (lo%, hi%). Same function as analyze.py.

    Sampling error only. It says nothing about classifier error, which section 1
    of LIMITATIONS.md shows is the larger term.
    """
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (100 * max(0.0, (c - m) / d), 100 * min(1.0, (c + m) / d))


def error_text(r):
    """Everything the failed call said, for classification."""
    return " ".join(str(x) for x in (r.get("tool_call_detail") or "",
                                     r.get("probe_tool") or "") if x)


def rpc_code(r):
    d = r.get("tool_call_detail") or ""
    m = re.search(r'"code"\s*:\s*(-?\d+)', d)
    return int(m.group(1)) if m else None


def classify(r):
    """One row -> (class, collided) where collided flags text matching BOTH the
    args-fault and the credential patterns.

    Order is args-fault FIRST, and collisions resolve to args-fault. That is the
    conservative direction: this number corrects a published figure downward, so
    a tie must shrink the correction rather than inflate it. Collisions are
    counted and reported so the cost of that choice is visible instead of
    assumed to be zero.
    """
    cls = r.get("tool_call_class")
    if cls != "TOOL_CALL_ERROR":
        return cls, False
    text = error_text(r)
    cred = looks_like_missing_credentials(text)
    argf = rpc_code(r) == _INVALID_PARAMS_CODE or bool(_ARGS_FAULT.search(text))
    if argf:
        return "PROBE_ARGS_FAULT", cred
    if cred:
        return "DEFERRED_CREDENTIALS", False
    if _PAYWALL.search(text):
        return "DEFERRED_PAYMENT", False
    if looks_like_needs_setup(text):
        return "DEFERRED_SETUP", False
    return "TOOL_CALL_ERROR", False


def load(patterns):
    rows, files = [], []
    for pat in patterns:
        hits = glob.glob(pat)
        if not hits:
            print("warning: no files matched %r" % pat, file=sys.stderr)
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
    return merged, sorted(set(files)), dupes


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="+")
    ap.add_argument("--published-zeroconf", type=float, default=None,
                    help="published zero-config share, e.g. 52.0")
    ap.add_argument("--published-undeclared", type=float, default=None,
                    help="published undeclared-credential share, e.g. 7.3 -- the "
                         "bucket deferred-credential servers move INTO")
    ap.add_argument("--json", dest="as_json", help="write machine-readable summary")
    args = ap.parse_args()

    rows, files, dupes = load(args.results)
    rows = [r for r in rows if not str(r.get("server", "")).startswith(("_fixture", "fx/"))]
    attempted = [r for r in rows if r.get("tool_call_attempted")]
    if not attempted:
        print("no --tool-call rows in input", file=sys.stderr)
        return 2

    counts, collisions = Counter(), 0
    labelled = []
    for r in attempted:
        cls, coll = classify(r)
        collisions += bool(coll)
        counts[cls] += 1
        labelled.append((r, cls))

    n_sample = len(attempted)
    n_candidate = n_sample - counts["NO_SAFE_PROBE"]
    n_args_fault = counts["PROBE_ARGS_FAULT"]
    # Denominator: servers where a call was actually made AND the outcome is
    # attributable to the server. Excluding args-fault is the whole point of
    # measuring it separately.
    denom = n_candidate - n_args_fault
    k_def = sum(counts[c] for c in DEFERRED)
    lo, hi = wilson(k_def, denom)
    k_cred = counts["DEFERRED_CREDENTIALS"]
    clo, chi = wilson(k_cred, denom)

    out = {
        "classifier": "deferred-v1",
        "files": files,
        "duplicate_rows_dropped": dupes,
        "sample_size": n_sample,
        "with_safe_probe_candidate": n_candidate,
        "no_safe_probe": counts["NO_SAFE_PROBE"],
        "probe_args_fault_excluded": n_args_fault,
        "args_fault_collided_with_credential_text": collisions,
        "rate_denominator": denom,
        "counts": dict(counts),
        "deferred_any": k_def,
        "deferred_rate_pct": (100.0 * k_def / denom) if denom else None,
        "deferred_ci95": [lo, hi],
        "deferred_credentials": k_cred,
        "deferred_credentials_rate_pct": (100.0 * k_cred / denom) if denom else None,
        "deferred_credentials_ci95": [clo, chi],
        "deferred_setup": counts["DEFERRED_SETUP"],
        "deferred_payment": counts["DEFERRED_PAYMENT"],
        "candidate_coverage_pct": 100.0 * n_candidate / n_sample if n_sample else None,
    }

    # --- correction to the published zero-config figure --------------------
    if args.published_zeroconf is not None and denom:
        p_def, p_lo, p_hi = k_def / denom, lo / 100.0, hi / 100.0
        cov = n_candidate / n_sample
        z = args.published_zeroconf
        # Upper correction assumes servers WITHOUT a candidate defer at the same
        # rate as those with one. That is an extrapolation onto servers this
        # method cannot probe, and it is untestable here -- stated, not hidden.
        out["corrected_zeroconf_extrapolated"] = z * (1 - p_def)
        out["corrected_zeroconf_extrapolated_ci95"] = [z * (1 - p_hi), z * (1 - p_lo)]
        # Lower correction assumes they never defer. Together the two bracket the
        # answer without either being asserted as the estimate.
        out["corrected_zeroconf_candidates_only"] = z * (1 - p_def * cov)
        out["corrected_zeroconf_candidates_only_ci95"] = [
            z * (1 - p_hi * cov), z * (1 - p_lo * cov)]
        out["published_zeroconf"] = z
        # Where the mass goes. A deferred credential is not a new kind of defect
        # -- it is an UNDECLARED_CREDENTIALS finding the handshake could not see,
        # so it leaves zero-config and lands in the bucket that already exists
        # for it. Reporting only the subtraction would imply the servers vanish.
        if args.published_undeclared is not None:
            p_cred = k_cred / denom
            u = args.published_undeclared
            out["published_undeclared"] = u
            out["corrected_undeclared_extrapolated"] = u + z * p_cred
            out["corrected_undeclared_candidates_only"] = u + z * p_cred * cov

    order = ["TOOL_CALL_OK", "DEFERRED_CREDENTIALS", "DEFERRED_SETUP",
             "DEFERRED_PAYMENT", "TOOL_CALL_ERROR", "TOOL_CALL_MALFORMED",
             "TOOL_CALL_TIMEOUT", "TOOL_CALL_CRASH", "PROBE_ARGS_FAULT",
             "NO_SAFE_PROBE"]
    w = max(len(c) for c in order)
    print("DEFERRED-REQUIREMENT RATE   classifier: deferred-v1")
    print("  sample (tool calls attempted) : %d" % n_sample)
    print("  with a safe-probe candidate   : %d  (%.1f%%)"
          % (n_candidate, out["candidate_coverage_pct"]))
    print()
    for c in order:
        if counts.get(c):
            print("  %-*s %5d  %5.1f%%" % (w, c, counts[c], 100.0 * counts[c] / n_sample))
    print()
    print("  rate denominator              : %d  (candidates minus %d args-fault)"
          % (denom, n_args_fault))
    if collisions:
        print("  ...of which ALSO matched credential text: %d  (resolved to "
              "args-fault, the conservative direction)" % collisions)
    print("  DEFERRED_* (any)              : %d / %d = %.1f%%  95%% CI [%.1f-%.1f]"
          % (k_def, denom, 100.0 * k_def / denom, lo, hi))
    print("  DEFERRED_CREDENTIALS only     : %d / %d = %.1f%%  95%% CI [%.1f-%.1f]"
          % (k_cred, denom, 100.0 * k_cred / denom, clo, chi))

    if "corrected_zeroconf_extrapolated" in out:
        print()
        print("  CORRECTION TO THE PUBLISHED %.1f%% ZERO-CONFIG FIGURE" % z)
        print("    extrapolated to all zero-config : %.1f%%  [%.1f-%.1f]"
              % (out["corrected_zeroconf_extrapolated"],
                 *out["corrected_zeroconf_extrapolated_ci95"]))
        print("      assumes servers with no safe-probe candidate defer at the")
        print("      same rate as those with one -- untestable by this method")
        print("    candidate-bearing servers only  : %.1f%%  [%.1f-%.1f]"
              % (out["corrected_zeroconf_candidates_only"],
                 *out["corrected_zeroconf_candidates_only_ci95"]))
        print("      assumes servers with no candidate never defer")
        print("    The two bracket the answer. Neither is the estimate on its own.")
        if "corrected_undeclared_extrapolated" in out:
            print()
            print("    WHERE THE MASS GOES -- undeclared-credential bucket, published %.1f%%" % u)
            print("      extrapolated                  : %.1f%%"
                  % out["corrected_undeclared_extrapolated"])
            print("      candidate-bearing only        : %.1f%%"
                  % out["corrected_undeclared_candidates_only"])
            print("      (DEFERRED_CREDENTIALS only -- setup and payment are")
            print("       separate findings and do not belong in that bucket)")

    if args.as_json:
        with open(args.as_json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        print("\n  wrote %s" % args.as_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
