# MCPwatch

Continuous, public, at-scale health auditing of the official MCP registry.

## What this is

The official MCP Registry (`registry.modelcontextprotocol.io`) lists thousands of
servers. Nobody knows how many of them actually install and run. MCPwatch answers
that, continuously, in public.

**v0 goal is a number, not a website:** *"Of the N servers in the official MCP
registry, X% fail to install or complete a handshake. Here is the breakdown."*

## Decisions already made — do not relitigate these

### 1. Registry servers are stdio, not remote

Verified against the live API. The overwhelming majority of registry entries are
`transport: {"type": "stdio"}` npm/PyPI/OCI packages. Entries that declare
`streamable-http` usually point at `http://localhost:3000/mcp` — still local.

**Consequence:** you cannot HTTP-ping these. You must spawn each one as a child
process and speak JSON-RPC over stdio. That is the whole technical core.

### 2. GitHub Actions is the compute fleet

Running untrusted `npx` from thousands of publishers on a Render box is wrong:
no isolation, no minutes. GitHub Actions is free/unlimited for public repos and
gives a disposable VM per job.

- Scheduled workflow shards targets into a matrix (start with 20 parallel jobs)
- Each job probes its shard, writes a JSON artifact
- Ephemeral VM disposed after every job

This isolation property is the point. Don't "optimize" it into a long-lived worker.

### 3. Failure taxonomy, not a boolean

`ok: true/false` is a toy. Every probe classifies into:

| class | meaning |
|---|---|
| `COMMAND_NOT_FOUND` | runtime (npx/uvx) missing |
| `INSTALL_FAILED` | package doesn't exist / deps broke — check stderr for E404, ENOENT |
| `CRASH_ON_START` | installed, process died before handshake |
| `INIT_TIMEOUT` | no `initialize` response in budget |
| `INIT_RPC_ERROR` | server returned JSON-RPC error to `initialize` |
| `ZERO_TOOLS` | handshake fine, `tools/list` empty |
| `TOOLS_TIMEOUT` / `TOOLS_RPC_ERROR` | `tools/list` failed |
| `SKIPPED_NEEDS_CREDENTIALS` | **not a failure** — see gotcha 1 |

"31% broken" is a tweet. The breakdown of *how* is what maintainers act on.

### 4. schema_hash is a first-class feature

SHA256 over sorted `[{name, inputSchema}]` for all tools. Diffing this across
versions detects **silent breaking changes** — a patch bump that renames a tool
param and breaks every agent using it. Nobody is watching for this. Keep it.

## Gotchas that will wreck the headline stat

1. **Required env vars.** A server needing `GITHUB_TOKEN` fails `initialize` and
   looks broken when it's fine. The registry gives `packages[].environmentVariables`
   with `isRequired: true`. Bucket those as `SKIPPED_NEEDS_CREDENTIALS` and exclude
   them from the failure rate. Getting this wrong makes the headline number garbage
   and someone will say so in public.
2. **Cold install dominates timing.** Measured `t_init_ms` of 5113ms was mostly
   `npx` download, not server boot. Split install time from boot time before
   publishing any latency claim.
3. **`isLatest`.** The API returns every historical version. Filter
   `_meta["io.modelcontextprotocol.registry/official"].isLatest == true` or you'll
   probe the same server 25 times.
4. **stdout pollution.** Some servers print non-JSON logs to stdout, violating the
   spec. The probe skips unparseable lines rather than dying. Track it as a signal —
   it's a real quality finding.
5. **Install timeout cap.** 120s. Without it one fat dep tree eats a whole job.

## Architecture

```
registry.modelcontextprotocol.io/v0/servers
        │ daily sync (sync/)
        ▼
Neon Postgres: targets(server_name, version, registry_type, identifier,
                       cmd, required_env, is_latest, ...)
        │ shard into matrix
        ▼
GitHub Actions matrix (.github/workflows/) → probe/probe.py
        │ JSON artifacts → POST
        ▼
FastAPI on Render (api/) → Neon: probe_runs, rollups_hourly
        ▼
Next.js on Vercel (web/, later): leaderboard, per-server pages, SVG badges
```

Free tier only. Neon, Render free, Vercel hobby, GitHub Actions on a public repo.

## Prior art — the gap is real

- **openstatus MCP health check / mcpplaygroundonline** — one-shot, paste-a-URL,
  remote only. Both state stdio needs a local child process and they can't do it.
- **MCP Observatory / mcp-doctor** — local CLI, on-demand, against your own configs.

Nobody does continuous + at-scale + public + stdio. That's the whole opening.

## Repo layout

```
probe/     probe.py — WORKING AND TESTED, see below. Extend, don't rewrite.
sync/      registry → Neon targets table
api/       FastAPI ingest + read endpoints
web/       Next.js dashboard (week 2+)
.github/workflows/  the matrix fleet
```

## Status of probe/probe.py

Already written and verified against live npm packages:

- `@modelcontextprotocol/server-everything` → PASS, tools enumerated, schema_hash ok
- `filesystem-mcp` → PASS, 4 tools, init 5113ms / tools 6ms
- nonexistent package → FAIL, correctly classified `INSTALL_FAILED` (E404)

It handles interleaved notifications on stdout, non-JSON noise, staged timeouts,
and process teardown. Treat it as the reference implementation.

## Scope discipline

Weekend 1 ships **the number**. Explicitly out of scope until it exists:
website, badges, latency percentiles, LLM-judge correctness scoring, auth'd
servers, OCI/docker targets, PyPI (npm-only first — halves surface area, covers
most of the registry).
