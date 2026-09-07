#!/usr/bin/env python3
"""Coverage of the SAFE-PROBE.md selection rules over an existing sweep.

Answers, without calling anything: of the zero-config servers in a sweep, how
many have a tool this harness could select as a safe probe target, broken down
by which rule would fire. That number decides whether a tool-call tier is worth
building -- if almost nothing is selectable, the convention matters and the
measurement does not.

Read-only. Reads result JSON (and optionally targets.json) only; never spawns,
never hits the network.

    ./safeprobe_coverage.py "shard*/results-*.json"
    ./safeprobe_coverage.py "results*.json" --targets targets.json --json

WHAT OLDER SWEEPS CANNOT ANSWER
-------------------------------
Rules 1-3 and half of rule 4 need fields probe.py did not retain before the
safe-probe change: tool-level `_meta`, `annotations`, and `inputSchema.required`
were folded into schema_hash and then dropped. A truncated SHA is not
invertible, so none of it is recoverable from an old artifact.

Rule 4 needs a read-shaped name AND zero required parameters. Only the name half
survives in an old sweep, so a name match there is an UPPER BOUND on rule-4
coverage, never a count of it. This script labels it as such and refuses to sum
bounds into a single "probeable" figure -- the same discipline as annotations.py
refusing to print a zero it cannot distinguish from absent data.

Retaining `tool_required` and `tool_annotations` (already done in probe.py) makes
every number here exact on the next sweep, at no extra probing cost: they are
retention, not calls.
"""
import argparse
import glob
import json
import re
import sys
from collections import Counter

SAFE_PROBE_KEY = "io.mcpwatch/safe-probe"

# Must stay identical to probe.py::_READ_SHAPED. Imported when probe/ is
# importable, so the two cannot drift; the literal is the fallback for running
# this script against artifacts from a checkout without it.
_READ_SHAPED_SRC = r"^(list|get|browse|search|fetch|read|show)([_\-]|(?=[A-Z0-9])|$)"
try:
    sys.path.insert(0, "probe")
    from probe import _READ_SHAPED, required_params  # noqa: E402
except Exception:
    _READ_SHAPED = re.compile(_READ_SHAPED_SRC)
    required_params = None

FIXTURE_PREFIX = ("_fixture", "fx/")


def load(paths):
    """Merge result files, dedupe by (server, identifier, version).

    Same key as analyze.py::load -- a 20-shard matrix with any overlap would
    otherwise double-count servers and skew every denominator here.
    """
    rows, files = [], []
    for pat in paths:
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


def is_zeroconf(r):
    """analyze.py's BUCKET_ZEROCONF, restated: ok is set only after initialize
    succeeded and tools/list returned >=1 tool. tool_count is checked too so an
    older-schema or hand-edited row cannot slip through on ok alone."""
    return bool(r.get("ok")) and (r.get("tool_count") or 0) > 0


def read_shaped(names):
    return [n for n in names if _READ_SHAPED.match(n or "")]


def zero_required(r):
    """Tool names with an empty required list, or None when unmeasured.

    None is not zero. A sweep predating the retention change carries no
    `tool_required`, and reporting 0 for it would be indistinguishable from
    'measured, nobody qualifies' -- the exact confusion this returns None to
    prevent.
    """
    tr = r.get("tool_required")
    if not isinstance(tr, dict):
        return None
    return [n for n, req in tr.items() if req == []]


def tool_declared(r):
    """Tool names carrying an explicit safe-probe _meta key, or None if the raw
    tools array was not retained. Sparse by nature: publishers have to opt in."""
    tools = r.get("tools")
    if not isinstance(tools, list):
        return None
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        meta = t.get("_meta")
        if isinstance(meta, dict) and SAFE_PROBE_KEY in meta \
                and meta[SAFE_PROBE_KEY] is not False:
            out.append(t.get("name"))
    return out


def annotated_readonly(r):
    """Tool names with readOnlyHint:true, or None when unmeasured."""
    ta = r.get("tool_annotations")
    if not isinstance(ta, dict):
        return None
    return [n for n, a in ta.items()
            if isinstance(a, dict) and a.get("readOnlyHint") is True]


def pct(k, n):
    return ("%.1f%%" % (100.0 * k / n)) if n else "n/a"


def line(label, k, n, note=""):
    return "  %-46s %5d / %-5d %7s %s" % (label, k, n, pct(k, n), note)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="+", help="result JSON files or globs")
    ap.add_argument("--targets", help="targets.json, to count registry-side declarations")
    ap.add_argument("--include-fixtures", action="store_true")
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args()

    rows, files, dupes = load(args.results)
    if not args.include_fixtures:
        rows = [r for r in rows if not str(r.get("server", "")).startswith(FIXTURE_PREFIX)]
    if not rows:
        print("no results loaded", file=sys.stderr)
        return 2

    zc = [r for r in rows if is_zeroconf(r)]
    n = len(zc)

    # --- rule 1: registry-side declarations ------------------------------
    declared_registry = None
    if args.targets:
        with open(args.targets, encoding="utf-8") as f:
            tg = json.load(f)
        if any("_meta" in t for t in tg):
            declared_registry = sum(
                1 for t in tg if isinstance(t.get("_meta"), dict)
                and SAFE_PROBE_KEY in t["_meta"])
        # else: sync predates the retention change -- stays None, not 0

    # --- rules 2-4 -------------------------------------------------------
    measured_req = [r for r in zc if zero_required(r) is not None]
    measured_ann = [r for r in zc if annotated_readonly(r) is not None]
    have_tool_meta = any(isinstance(r.get("tools"), list) for r in zc)

    name_hits = [r for r in zc if read_shaped(r.get("tool_names") or [])]
    no_name = n - len(name_hits)

    out = {
        "files": files,
        "duplicate_rows_dropped": dupes,
        "rows_after_fixture_filter": len(rows),
        "zero_config_servers": n,
        "total_tools_across_zero_config": sum(r.get("tool_count") or 0 for r in zc),
        "registry_declared": declared_registry,
        "tool_declared": None,
        "readonly_hint_servers": None,
        "zero_required_servers": None,
        "name_shaped_servers": len(name_hits),
        "name_shaped_and_zero_required": None,
        "no_read_shaped_name": no_name,
        "selectable_total": None,
        "required_data_present": len(measured_req) == n and n > 0,
        "annotation_data_present": len(measured_ann) == n and n > 0,
        "tool_meta_present": have_tool_meta,
    }

    if len(measured_ann) == n and n:
        out["readonly_hint_servers"] = sum(1 for r in zc if annotated_readonly(r))
    if len(measured_req) == n and n:
        out["zero_required_servers"] = sum(1 for r in zc if zero_required(r))
        out["name_shaped_and_zero_required"] = sum(
            1 for r in zc
            if set(read_shaped(r.get("tool_names") or [])) & set(zero_required(r)))

    exact = out["required_data_present"] and out["annotation_data_present"]
    if exact:
        # Two views, because they answer different questions and the difference
        # between them IS the overlap. Marginals count a server under every
        # criterion it meets; by_rule assigns it to the single rule that would
        # actually fire. Publishing only the marginals would double-count the
        # servers that satisfy several, and only the priority split would hide
        # how much redundancy the convention has.
        rule, marg = Counter(), Counter()
        for r in zc:
            ro = set(annotated_readonly(r))
            zr = set(zero_required(r))
            names = set(read_shaped(r.get("tool_names") or []))
            td_raw = tool_declared(r)
            if td_raw is None:
                marg["tool_declared_unmeasured"] += 1
            td = set(td_raw or [])
            if zr:
                marg["zero_required"] += 1
            if ro:
                marg["readonly_hint"] += 1
            if td:
                marg["tool_declared"] += 1
            if names:
                marg["read_shaped_name"] += 1
            if names & zr:
                marg["name_and_zero_required"] += 1
            if ro & zr:
                marg["readonly_and_zero_required"] += 1
            if td:
                rule["tool-declared"] += 1
            elif ro & zr:
                rule["readonly-hint"] += 1
            elif names & zr:
                rule["name-heuristic"] += 1
            else:
                rule["none"] += 1
        out["by_rule"] = dict(rule)
        out["marginals"] = dict(marg)
        out["selectable_total"] = n - rule["none"]

    if args.as_json:
        print(json.dumps(out, indent=2))
        return 0 if exact else 1

    msg = ["SAFE-PROBE COVERAGE  (no calls made)",
           "",
           "  result files                 : %d" % len(files),
           "  rows after fixture filter    : %d%s" % (
               len(rows), (" (%d duplicates dropped)" % dupes) if dupes else ""),
           "  zero-config servers          : %d   (ok AND tools/list >= 1 tool)" % n,
           "  tools across those servers   : %d" % out["total_tools_across_zero_config"],
           ""]

    if declared_registry is None:
        why = ("no --targets given" if not args.targets
               else "targets.json carries no _meta")
        msg.append("  rule 1 registry-declared     : NOT MEASURABLE (%s)" % why)
    else:
        msg.append(line("rule 1 registry-declared", declared_registry, len(tg),
                        "of registry targets"))
    msg.append("  rule 2 tool-declared _meta   : %s" % (
        "measurable" if have_tool_meta else "NOT MEASURABLE (raw tools not retained)"))
    if out["readonly_hint_servers"] is None:
        msg.append("  rule 3 readOnlyHint          : NOT MEASURABLE "
                   "(tool_annotations not retained)")
    else:
        msg.append(line("rule 3 readOnlyHint (>=1 tool)",
                        out["readonly_hint_servers"], n))
    msg.append("")

    if exact:
        m = out["marginals"]
        msg += [
            "  MARGINALS -- servers meeting each criterion, overlapping",
            line(">=1 tool with zero required params", m.get("zero_required", 0), n),
            line(">=1 tool with readOnlyHint:true", m.get("readonly_hint", 0), n),
            ("  %-46s %s" % (">=1 tool with a safe-probe declaration",
                              "NOT MEASURABLE (raw tool _meta not retained)"))
            if m.get("tool_declared_unmeasured") else
            line(">=1 tool with a safe-probe declaration",
                 m.get("tool_declared", 0), n),
            line(">=1 read-shaped tool name", m.get("read_shaped_name", 0), n),
            "",
            "  PAIRED -- criterion AND zero-required on the SAME tool",
            line("readOnlyHint + zero-required", m.get("readonly_and_zero_required", 0), n),
            line("read-shaped name + zero-required",
                 m.get("name_and_zero_required", 0), n),
            "",
            "  UNION -- each server counted once, under the rule that fires",
            line("SELECTABLE (any rule)", out["selectable_total"], n,
                 "LOWER BOUND" if m.get("tool_declared_unmeasured") else ""),
            ("  %-46s %s" % ("  rule 2  tool-declared _meta",
                              "NOT MEASURABLE -- absent from the union below"))
            if m.get("tool_declared_unmeasured") else
            line("  rule 2  tool-declared _meta",
                 out["by_rule"].get("tool-declared", 0), n),
            line("  rule 3  readOnlyHint + zero-required",
                 out["by_rule"].get("readonly-hint", 0), n),
            line("  rule 4  read-shaped name + zero-required",
                 out["by_rule"].get("name-heuristic", 0), n),
            line("NO_SAFE_PROBE (no candidate)", out["by_rule"].get("none", 0), n,
                 "UPPER BOUND, not a failure"
                 if m.get("tool_declared_unmeasured") else "not a failure"),
            "",
            "  Overlap = marginals minus union. A server meeting several criteria",
            "  is one probe, not several, so only the union sizes the tier.",
        ]
        print("\n".join(msg))
        return 0

    # --- bounded reporting -------------------------------------------------
    msg += [
        "  WHAT THIS SWEEP CAN AND CANNOT SUPPORT",
        "",
        line(">=1 read-shaped tool NAME", len(name_hits), n, "UPPER BOUND on rule 4"),
        line("no read-shaped tool name at all", no_name, n, "rule 4 impossible"),
        "",
        "  Rule 4 needs a read-shaped name AND zero required parameters. This",
        "  sweep retains tool_names but not inputSchema.required, so the first",
        "  line above counts servers that pass HALF the rule. Some unknown share",
        "  of them have a required parameter on every read-shaped tool and are",
        "  not selectable at all.",
        "",
        "  Refusing to print a single 'probeable' number: it would be a sum of",
        "  one bound and two unmeasured terms, presented as a measurement.",
        "",
        "  probe.py now retains tool_required and tool_annotations. Those are",
        "  retention, not calls -- the next scheduled sweep makes every line here",
        "  exact at no extra cost, and this script prints EXACT COVERAGE instead.",
    ]
    print("\n".join(msg))
    return 1


if __name__ == "__main__":
    sys.exit(main())
