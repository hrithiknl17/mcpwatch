# Limitations

Every number MCPwatch publishes comes from a stdio probe and a stderr classifier.
Both have measured error. This file states what that error is, how it was
measured, and what the probe does not measure at all.

Read this before quoting any figure.

## 1. Classification accuracy, measured in both directions

The failure classes that are not mechanical (`UNDECLARED_CREDENTIALS`,
`UNDECLARED_ARGS`, `NEEDS_LOCAL_SETUP`, `MISSING_SYSTEM_DEPENDENCY`) come from
pattern-matching a server's own stderr and stdout. That is a heuristic. It was
audited in both directions against `classifier-v1`:

| direction | what it measures | result | 95% CI |
|---|---|---|---|
| **Audit A** (n=40) | precision of `UNDECLARED_CREDENTIALS` — of rows we call a credential requirement, how many are | **85.0%** | [70.9 – 92.9] |
| **Audit B** (n=40) | precision of `CRASH_ON_START` — of rows we call a crash, how many are | **57.5%** | [42.2 – 71.5] |

Audit A's 6 errors: 4 usage screens whose *help text* mentioned a token or
wallet, 1 missing system dependency, 1 server that was not failing at all (it
logged a non-fatal warning and listened on HTTP).

Audit B's 17 errors: 4 missed credentials, 2 missed local-setup requirements,
3 missed usage screens, 2 missed build casualties, 5 missing system
dependencies, 1 platform mismatch.

**Consequence:** `Undeclared credential requirement` is an over-estimate by
roughly 15%, and `Broken outright` contains a substantial minority of rows that
are really unconfigured rather than broken. Both directions are stated so a
reader can correct in either direction rather than trusting a single figure.

### The audit is single-rater and unblinded

The verdicts were produced by the author of the classifier, reading stderr with
knowledge of what the classifier had decided. That is the weakest form of
validation that still deserves the name. It is not inter-rater agreement and it
is not blind. The samples (`auditA40`, `auditB40`) are reproducible from their
seeds, and the blank verdict sheets are kept so an independent rater can redo
them and publish a competing accuracy figure.

### Precision did not converge

Three successive rounds of pattern fixes never pushed crash-bucket precision past
57.5%. Each round closed the errors found by the previous audit and the next
audit found a new class of phrasing. That is a convergence problem, not a
coverage problem, and further lexical patching was stopped deliberately rather
than continued to an asymptote that does not exist.

## 2. Categories the probe does not measure

Each was observed in the 80-row audit and each needs infrastructure this version
does not have. Rates are from that audit and are indicative only.

| category | observed | why it is unmeasured |
|---|---|---|
| **Remote dependency down** | 4/80 (5%) | Servers whose startup requires a live vendor endpoint (SSE 404, config fetch 503, DNS `ENOTFOUND`). A single probe cannot separate "permanently broken" from "vendor had an outage during our sweep". Needs retry-over-time across independent runs. Currently counted as `CRASH_ON_START`, which overstates brokenness. |
| **Transport mismatch** | 1/80 (1.3%) | A server registered as `stdio` that actually binds an HTTP port and waits. It never fails — the probe times out. Detecting it needs HTTP probing alongside stdio, which this version does not do. Currently misclassified. |
| **Node version incompatibility** | 5/80 (6.3%) | `ERR_UNSUPPORTED_NODE_MODULES_TYPE_STRIPPING` (packages shipping raw TypeScript) and removed import-assertion syntax. Whether these are publisher defects or our Node pin is unanswerable from one Node version. Needs a multi-Node matrix. Currently counted as `CRASH_ON_START`. |

## 3. Environment: results are conditional on it

**Node 22.23.1**, resolved by `actions/setup-node@v4` from the runner tool cache.
Every "crash" is a crash *on Node 22.23.1*. Packages targeting older or newer
Node may pass or fail differently. This directly affects the categories above.

**Linux only** (`ubuntu-latest`). Packages declaring `"os": "darwin"` or
`"os": "win32"` cannot run. Those are classified `PLATFORM_UNSUPPORTED` and held
**outside** the partition — they are not broken, we ran the wrong OS. Both the
install-time (`EBADPLATFORM`) and runtime variants are in that bucket. A
macOS/Windows matrix would move them.

**GitHub-hosted runners.** All timings are from those runners. Median install and
median boot are always reported separately and never summed: the combined figure
is dominated by npm download time and says nothing about how fast a server
starts. The same probe run from a home connection measured ~10x slower installs.

**`--ignore-scripts`.** npm lifecycle scripts do not run. This is a security
boundary — postinstall scripts from thousands of unvetted publishers would
otherwise execute with the runner's privileges before any JSON-RPC is spoken —
and it is not negotiable. Packages that genuinely need an install-time script are
classified `BUILD_SCRIPTS_REQUIRED` and held **outside** the partition, because
counting them as broken would blame publishers for our policy.

The lifecycle set is `preinstall`, `install`, `postinstall` — exactly what npm
runs for a registry tarball. A package whose only build script is `build` or
`prepack` was never going to be built by npm regardless, so it is a publisher
defect and stays *inside* the partition.

**`roots`-only capability advertisement.** The probe advertises exactly one
client capability, `roots`, because it is the only one it can honestly serve
(with an empty list). Advertising `sampling` or `elicitation` would invite
requests it cannot answer, and the server would block — turning a healthy server
into a fake timeout.

Consequence: **tools gated on capabilities we do not advertise are invisible.**
This is measured, not assumed. Against the official MCP inspector,
`server-everything` exposes 14 tools to a fully-capable client and 13 to this
probe; `get-roots-list` appears only with `roots` advertised. `tool_count` and
`schema_hash` are therefore lower bounds for any server that gates tools on
`sampling` or `elicitation`.

## 4. Sampling and scope

**npm + stdio only.** PyPI, OCI/Docker, and remote (`streamable-http`) servers are
out of scope. Every percentage is a share of npm+stdio entries, not of the
registry as a whole.

**Latest versions only** (`isLatest`). Historical versions are not probed.

**The registry moves.** It grew from 6675 to 6779 npm+stdio entries over three
days of measurement. Any before/after comparison across runs mixes classifier
changes with registry drift. Sweep timestamps are recorded in the artifacts.

**Publisher concentration.** 88.6% of publishers have exactly one entry, and the
top 10 publishers are 9.8% of all entries. But failures concentrate far more than
entries do: a single publisher shipping one templated mistake across 17 packages
once moved the headline by 27 points. Every partition is therefore published
alongside a largest-publisher-excluded view, and `concentration.py` reports
distinct failure *signatures* rather than row counts.

**A sweep is a census, not a sample.** The confidence intervals describe binomial
sampling error only. They say nothing about classifier error — which, per section
1, is the larger term. Read them as "how much would this move on a re-run", not
as a bound on total error.

## 5. Reproducibility

Classifier versions are tagged (`classifier-v1`, `classifier-v2`). Every published
figure traces to a tag and a workflow run id. Audit samples are drawn with
recorded seeds. Sweep artifacts (per-shard results, merged summary, audit sample)
are retained on the workflow run.
