# Milestone 1 — get the number

Read CLAUDE.md first. All architectural decisions there are settled.

**Definition of done:** a single command produces
`Of N npm-packaged servers in the official MCP registry, X% fail to install or
complete an MCP handshake`, with a breakdown by failure class, from a real full
sweep — not a sample.

Nothing else ships this milestone. No web UI, no badges, no percentiles.

---

## Task 1 — `sync/sync_registry.py`

Page `https://registry.modelcontextprotocol.io/v0/servers` via `metadata.nextCursor`
until exhausted.

For each server, keep only entries where
`_meta["io.modelcontextprotocol.registry/official"].isLatest == true`.

For each `packages[]` entry with `registryType == "npm"` and
`transport.type == "stdio"`, emit one target:

```json
{
  "server_name": "io.github.domdomegg/filesystem-mcp",
  "version": "1.3.0",
  "registry_type": "npm",
  "identifier": "filesystem-mcp",
  "cmd": ["npx", "-y", "filesystem-mcp@1.3.0"],
  "required_env": ["WORKSPACE_ROOT"],
  "package_arguments": [...],
  "repository_url": "..."
}
```

- `required_env` = `environmentVariables[]` where `isRequired == true`
- Targets with non-empty `required_env` still get emitted — the probe marks them
  `SKIPPED_NEEDS_CREDENTIALS`. They're counted separately, never as failures.
- Handle `packageArguments` with `isRequired: true` the same way (a server needing
  a mandatory positional path can't be probed blind — same skip bucket).

Output: `targets.json`, and a Postgres upsert into `targets` (Neon, `DATABASE_URL`).
Write the JSON path first so the sweep can run before the DB exists.

Print a summary: total servers, latest-only, npm+stdio, probeable vs needs-creds.

## Task 2 — `.github/workflows/sweep.yml`

- Trigger: `workflow_dispatch` + `schedule` (daily, pick an off-peak UTC hour)
- Job 1 `plan`: run the sync, upload `targets.json`, emit a shard matrix as output
- Job 2 `probe`: `strategy.matrix.shard: [0..19]`, `fail-fast: false`
  - each shard filters `targets.json` by `index % 20 == shard`
  - runs `probe/probe.py` over its slice
  - uploads `results-${{ matrix.shard }}.json`
- Job 3 `collect`: downloads all artifacts, merges, prints the summary table

Per-shard timeout so a hung target can't burn the job. Continue past individual
target failures — a crashing server is *data*, not a build failure.

## Task 3 — extend `probe/probe.py`

Do not rewrite it. It is tested and correct. Add:

1. **Batch mode** — accept `--targets targets.json --shard N --of M --out results.json`
   instead of the hardcoded list in `__main__`.
2. **`SKIPPED_NEEDS_CREDENTIALS`** — if a target has `required_env` or required
   `packageArguments`, short-circuit before spawning and record the skip.
3. **Split install from boot.** Currently `t_init_ms` folds in the npx download.
   Pre-warm with `npm install --prefix <tmp>` (or `npx -y <pkg> --version`),
   record `t_install_ms`, then spawn and record `t_boot_ms` separately.
   Every latency claim depends on this being honest.
4. **`stdout_polluted`** — set true when a non-JSON line appears on stdout before
   the initialize response. Currently the field exists but is never set.
5. Per-target hard wall-clock kill so nothing wedges a shard.

## Task 4 — `analyze.py`

Reads merged results, prints:

```
Total npm+stdio servers (latest):     N
  Probeable:                          N
  Skipped (needs credentials):        N

Of probeable:
  PASS                                N (X%)
  INSTALL_FAILED                      N (X%)
  CRASH_ON_START                      N (X%)
  ...

Median install: Xms   Median boot: Xms
stdout spec violations:               N
```

That output block is the launch post. Make it copy-pasteable.

---

## Order of operations

Build Task 1 and 3 first, run a **50-target sweep locally** to shake out timeouts
and classification bugs, *then* wire the Actions matrix. Don't debug your probe
logic through CI logs.

## Verification

- Sync against the live API, confirm no duplicate `server_name`
- Probe includes at least one known-good (`filesystem-mcp`) and one known-bad
  (nonexistent package) as fixtures — both must classify correctly every run
- Spot-check 5 `INSTALL_FAILED` results by hand. If any is actually a
  credentials or arguments problem, the taxonomy is wrong and the headline number
  is wrong. This check is not optional.
