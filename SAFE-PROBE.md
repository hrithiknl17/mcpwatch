# The safe-probe convention

A successful MCP handshake is **liveness, not correctness**. A server can install,
answer `initialize`, and enumerate its tools while every one of those tools fails
on the first real call. MCPwatch measures liveness today and says so in
LIMITATIONS.md. This document proposes the smallest thing that would let a
harness measure one step further, at registry scale, without credentials and
without side effects.

The problem is that a harness cannot know which of a server's tools is safe to
call. `delete_repo` and `list_repos` are both just strings. Per-server knowledge
does not exist at 6800 servers. So the convention moves the knowledge to the only
party that already has it: **the publisher declares which tool is safe to call.**

## What "safe to probe" means

A tool is safe-probeable when calling it, exactly once, with the declared
arguments:

- requires **no credentials** — no API key, token, or account
- has **no side effects** — writes nothing, sends nothing, bills nothing, and
  leaves no state a second call would observe
- is **idempotent and cheap** — safe to run daily, from many IPs, forever

That is the whole contract. It is a claim about *callability*, not about quality.

## 1. Explicit publisher declaration (primary)

Two places, either sufficient. Both use a namespaced `_meta` key so nothing in
the MCP spec or the registry schema has to change to adopt this.

**In the registry entry** (`server.json`), readable before a single process is
spawned — this is the preferred form, because a harness can plan its whole sweep
from registry data:

```json
{
  "name": "io.github.example/weather",
  "_meta": {
    "io.mcpwatch/safe-probe": {
      "tool": "get_forecast",
      "arguments": { "city": "London" }
    }
  }
}
```

`arguments` is optional and defaults to `{}`. Naming it explicitly is what lets a
publisher declare a tool that *does* take required parameters — which is most
useful read tools, and which no heuristic below can ever reach.

**On the tool itself**, in the `tools/list` response, for servers whose registry
entry they do not control:

```json
{
  "name": "get_forecast",
  "inputSchema": { "...": "..." },
  "annotations": { "readOnlyHint": true },
  "_meta": { "io.mcpwatch/safe-probe": { "arguments": { "city": "London" } } }
}
```

`_meta` present at all — even as `{}` — is the declaration.

**`annotations.readOnlyHint: true` is accepted as a weaker declaration.** It is
already in the MCP spec, publishers already set it, and it asserts no side
effects. It does *not* assert "no credentials", so it ranks below an explicit
safe-probe key and above every heuristic. A read-only tool that needs an API key
fails the probe as `TOOL_CALL_ERROR`, which is a survivable misread, not a
destructive one.

If more than one tool is declared, a harness picks one. One call per server.

## 2. Naming convention (weak fallback)

`list_*`, `get_*`, `browse_*`, `search_*` read as retrieval. **This is a weak
signal and should be labelled as such wherever it is used.** It is a convention
about English, not about behaviour: `get_or_create_session` writes,
`search_and_notify` sends. A harness that leans on this alone will eventually
call something it should not.

It is included because it is the only signal available for the large majority of
servers that have declared nothing, and because reporting its coverage separately
from declared coverage is itself a finding.

## 3. Zero required parameters (mechanical fallback)

A tool whose `inputSchema` has an empty or absent `required` array can be called
with `{}` — no invented values, no guessed IDs, no argument the harness had to
make up.

**This is the only criterion on this page a harness can verify mechanically
rather than infer.** Naming is a guess about intent and a declaration is a claim
taken on trust; zero-required is a fact about the schema in front of you. It is
also the criterion that keeps the probe honest: calling a tool with arguments a
harness invented tests the harness's guess, not the server.

Zero-required alone is not sufficient — a zero-argument `reset_database` exists
somewhere. It is used **in conjunction with** a read-shaped name, never on its own.

## Selection order

A harness takes the first that matches, and records which rule fired so the
resulting numbers can be split by evidence strength:

1. registry `_meta["io.mcpwatch/safe-probe"]`
2. tool `_meta["io.mcpwatch/safe-probe"]`
3. `annotations.readOnlyHint: true` **and** zero required parameters
4. read-shaped name **and** zero required parameters
5. no candidate → `NO_SAFE_PROBE`

A server with no candidate is **not broken**. It sits outside the pass/fail
judgment, for the same reason `BUILD_SCRIPTS_REQUIRED` and `PLATFORM_UNSUPPORTED`
do: it reflects a limit of our method, not a defect in theirs.

## What this convention does NOT promise

Stated plainly, in the same discipline as LIMITATIONS.md:

- **It does not mean the call was correct.** A probe checks that a response came
  back and that its content parses. It does not check that the content is right,
  useful, or what the tool's description claimed. A weather server returning
  `{"temp": null}` passes.
- **It does not mean the server's other tools work.** One tool out of forty
  responded. The other thirty-nine are exactly as unmeasured as before.
- **It does not mean the tool works for real inputs.** The declared arguments are
  a sample of one, usually the emptiest possible one. Passing says nothing about
  behaviour under an argument a user would actually send.
- **It does not mean the server is safe.** Nothing here is a security audit. It
  is a claim about one call, made by the publisher, about their own software.

## The failure mode we are accepting

**A publisher can declare a tool safe when it is not.** The harness cannot verify
the claim — verifying it would require exactly the per-server knowledge the
convention exists to avoid needing. If a publisher marks `send_invoices` as
safe-probeable, a harness will send invoices, daily, on schedule.

This is a trust assumption and it is load-bearing. It is stated here rather than
buried because a reader should be able to decide whether to accept it before
adopting anything on this page.

Three things bound the damage, and none of them eliminate it:

1. **The blast radius is the publisher's own server.** A publisher who
   mis-declares harms mainly themselves, which is the right incentive alignment.
2. **A harness should cap itself at one call per server per sweep** — a
   mis-declaration is then a slow leak, not an amplifier.
3. **A harness should publish which rule fired per server**, so a reader can
   discount declaration-based results independently of heuristic-based ones.

The alternative to accepting this is not calling anything, which is where we
started, and which is the thing multiple people independently pointed out is not
enough.
