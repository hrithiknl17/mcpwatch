#!/usr/bin/env python3
"""
MCPwatch stdio probe.
Spawns an MCP server as a child process, speaks JSON-RPC over stdio,
and records staged timings + failure classification.
"""
import argparse, json, re, subprocess, sys, time, threading, queue, os, hashlib, shutil, tempfile

PROTOCOL_VERSION = "2025-06-18"

INSTALL_TIMEOUT = 120   # cap on npm install -- one fat dep tree must not eat a job
BOOT_TIMEOUT = 45       # post-install: handshake budget for an already-cached package
RPC_TIMEOUT = 20
HARD_WALL_S = 240       # absolute per-target ceiling, install included

# npm error strings that mean "the package does not exist / cannot be installed"
INSTALL_MARKERS = ("e404", "404 not found", "npm error", "enoent", "could not determine",
                   "etarget", "no matching version", "eresolve")

# Servers routinely demand credentials the registry never declared in
# environmentVariables. Those die before initialize and look identical to a crash.
# Counting them as failures inflates the headline number; the fix is to read the
# server's own complaint. Deliberately conservative: a credential NOUN must appear
# alongside a "missing/required" phrasing, because over-matching here deflates the
# failure rate just as dishonestly as under-matching inflates it.
_CRED_NOUN = re.compile(
    r"\b(env(?:ironment)?[ _-]?var\w*|api[ _-]?key|access[ _-]?key|secret|token|"
    r"credential|wallet|password|passphrase|macaroon|private[ _-]?key|auth\w*)\b", re.I)
_CRED_VERB = re.compile(
    r"(is required|are required|required\b|missing|not set|must be set|not configured|"
    r"no .{0,20}configured|set one of|please set|unset|provide a|expected .{0,20}to be set)", re.I)


def looks_like_missing_credentials(text):
    if not text:
        return False
    if _OAUTH.search(text):          # an auth flow starting IS the missing credential
        return True
    return bool(_CRED_NOUN.search(text) and _CRED_VERB.search(text))


# A server that starts an interactive auth flow is waiting for a human, not broken.
_OAUTH = re.compile(r"(oauth|device flow|device code|authoriz\w* (?:url|code)|"
                    r"log ?in to|sign ?in to|not (?:logged|signed) in)", re.I)

# npm/npx and the OS agreeing that the package exposes nothing runnable. This is a
# genuine failure -- the package is unusable as published -- but it is NOT a crash,
# and calling it one hides the most actionable finding in the set.
_NO_ENTRY = re.compile(
    r"(could not determine executable to run|is not recognized as an internal or external|"
    r"command not found|no such file or directory.*bin|missing script|"
    r"cannot find module .{0,80}(bin|cli|index)|npm ERR! could not determine)", re.I)

# The binary printed its usage screen and exited: it needs a subcommand or argument
# that the registry entry never declared in packageArguments. Requires two markers,
# because a single "Usage:" also shows up inside ordinary error messages.
_USAGE = re.compile(r"(display help for command|^\s*usage:|^\s*commands:|"
                    r"\[options\]|--help\b|^\s*options:)", re.I | re.M)

# Local filesystem setup the server expects to exist (config dir, vault, workspace).
# Environment-dependent, exactly like credentials -- not the server being broken.
_SETUP = re.compile(
    r"((?:policy|config|vault|workspace|data|project)\s*(?:directory|dir|file|path|root)"
    r".{0,40}(?:not found|missing|does not exist|could not|no such)|"
    r"could not locate an? \w+ vault|no \w+ (?:vault|workspace) )", re.I)


def looks_like_no_entrypoint(text):
    return bool(text and _NO_ENTRY.search(text))


def looks_like_needs_args(text):
    """Two independent usage markers -- one alone is too weak to act on."""
    if not text:
        return False
    return len(set(m.group(0).lower().strip() for m in _USAGE.finditer(text))) >= 2


def looks_like_needs_setup(text):
    return bool(text and _SETUP.search(text))


def classify_prestart_stderr(text, default):
    """Shared ordering for every pre-handshake failure path.

    Order matters: entrypoint problems are checked first because a package with no
    bin often ALSO prints usage; credentials before setup because an OAuth prompt
    frequently mentions a config path too.
    """
    if looks_like_no_entrypoint(text):
        return "NO_ENTRYPOINT"
    if looks_like_missing_credentials(text):
        return "UNDECLARED_CREDENTIALS"
    if looks_like_needs_setup(text):
        return "NEEDS_LOCAL_SETUP"
    if looks_like_needs_args(text):
        return "UNDECLARED_ARGS"
    return default


class Probe:
    def __init__(self, cmd, env=None, install_timeout=120, rpc_timeout=20):
        self.cmd = cmd
        self.env = {**os.environ, **(env or {})}
        self.install_timeout = install_timeout
        self.rpc_timeout = rpc_timeout
        self.proc = None
        self.q = queue.Queue()
        self.stderr_buf = []
        self.stdout_polluted = False

    def _reader(self, stream, q):
        for line in stream:
            q.put(line)
        q.put(None)

    def _stderr_reader(self, stream):
        for line in stream:
            self.stderr_buf.append(line.rstrip())

    def stderr_tail(self, head=10, tail=8, width=2000):
        """Fatal diagnostics land at the START of stderr; chatty servers then bury
        them under banners and PATH dumps. Keeping only the tail loses the actual
        error, so keep both ends and mark the elision."""
        buf = [ln for ln in self.stderr_buf if ln.strip()]
        if len(buf) <= head + tail:
            out = "\n".join(buf)
        else:
            out = "\n".join(buf[:head] + [f"... [{len(buf) - head - tail} lines elided] ..."] + buf[-tail:])
        return out[:width]

    def start(self):
        self.proc = subprocess.Popen(
            self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=self.env,
        )
        threading.Thread(target=self._reader, args=(self.proc.stdout, self.q), daemon=True).start()
        threading.Thread(target=self._stderr_reader, args=(self.proc.stderr,), daemon=True).start()

    def send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def recv(self, want_id, timeout):
        """Read lines until we get a JSON-RPC response with the id we want.
        Servers legitimately interleave notifications and log noise on stdout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self.q.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                raise TimeoutError(f"no response to id={want_id} within {timeout}s")
            if line is None:
                raise ConnectionError("server closed stdout")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                # non-JSON on stdout = spec violation, but not fatal. Track it.
                self.stdout_polluted = True
                continue
            if msg.get("id") == want_id:
                return msg
        raise TimeoutError(f"no response to id={want_id} within {timeout}s")

    def kill(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def resolve_cmd(cmd):
    """On Windows npm/npx are .cmd shims that Popen won't find by bare name."""
    if not cmd:
        return cmd
    exe = shutil.which(cmd[0])
    return [exe] + list(cmd[1:]) if exe else list(cmd)


def _entrypoint(prefix, identifier):
    """Resolve the installed package's bin script to an absolute path.

    Spawning `node <script>` instead of `npx <spec>` is what makes t_boot_ms
    honest: npx keeps its own _npx cache and would re-resolve the package,
    folding a second install back into the boot measurement.
    """
    pkg_dir = os.path.join(prefix, "node_modules", *identifier.split("/"))
    pj = os.path.join(pkg_dir, "package.json")
    if not os.path.isfile(pj):
        return None
    try:
        with open(pj, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    binf = meta.get("bin")
    rel = None
    if isinstance(binf, str):
        rel = binf
    elif isinstance(binf, dict) and binf:
        short = identifier.split("/")[-1]
        rel = binf.get(short) or next(iter(binf.values()))
    elif meta.get("main"):
        rel = meta["main"]
    if not rel:
        return None
    path = os.path.normpath(os.path.join(pkg_dir, rel))
    return path if os.path.isfile(path) else None


def npm_prewarm(spec, identifier, timeout=INSTALL_TIMEOUT):
    """Install `spec` into a throwaway prefix so the later spawn measures boot,
    not download. Returns (ok, t_install_ms, stderr_tail, prefix_dir, entrypoint).

    Caller owns prefix_dir and must remove it. Lifecycle scripts are deliberately
    NOT disabled -- npx would run them at spawn time anyway, and suppressing them
    here would shift that cost into t_boot_ms and corrupt the split. Isolation
    comes from the ephemeral VM, not from crippling the install.
    """
    tmp = tempfile.mkdtemp(prefix="mcpwatch-warm-")
    t0 = time.time()
    try:
        cp = subprocess.run(
            resolve_cmd(["npm", "install", "--prefix", tmp, "--no-audit", "--no-fund",
                         "--no-package-lock", "--loglevel", "error", spec]),
            capture_output=True, text=True, timeout=timeout)
        dt = int((time.time() - t0) * 1000)
        tail = (cp.stderr or "")[-1500:]
        if cp.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            return False, dt, tail, None, None
        return True, dt, tail, tmp, _entrypoint(tmp, identifier)
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp, ignore_errors=True)
        # sentinel, not prose: the caller must tell a slow dep tree apart from a
        # package that does not exist (gotcha 5 -- these are different findings)
        return False, int((time.time() - t0) * 1000), f"__TIMEOUT__ npm install exceeded {timeout}s", None, None
    except FileNotFoundError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return False, int((time.time() - t0) * 1000), f"COMMAND_NOT_FOUND: {e}", None, None


def probe(name, cmd, env=None, spec=None, identifier=None, extra_args=(), prewarm=True,
          install_timeout=INSTALL_TIMEOUT, boot_timeout=BOOT_TIMEOUT,
          rpc_timeout=RPC_TIMEOUT, hard_wall=HARD_WALL_S):
    r = {
        "server": name, "cmd": cmd, "ok": False, "stage_failed": None,
        "error_class": None, "error_detail": None, "error_stderr": None,
        "t_spawn_ms": None, "t_install_ms": None, "t_boot_ms": None,
        "t_init_ms": None, "t_tools_ms": None,
        "protocol_version": None, "server_info": None,
        "tool_count": None, "tool_names": [], "schema_hash": None,
        "stdout_polluted": False, "prewarmed": False, "spawn_mode": "direct",
        "undeclared_creds": False,
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    wall0 = time.time()
    warm_dir = None
    identifier = identifier or (spec.rsplit("@", 1)[0] if spec and "@" in spec.lstrip("@") else spec)

    # --- stage 0: install, timed separately from boot (gotcha 2) ---
    if prewarm and spec:
        ok, t_install, tail, warm_dir, entry = npm_prewarm(spec, identifier, timeout=install_timeout)
        r["t_install_ms"] = t_install
        if not ok:
            low = tail.lower()
            if tail.startswith("__TIMEOUT__"):
                cls = "INSTALL_TIMEOUT"
            elif "command_not_found" in low:
                cls = "COMMAND_NOT_FOUND"
            elif looks_like_no_entrypoint(tail):
                cls = "NO_ENTRYPOINT"
            else:
                cls = "INSTALL_FAILED"
            r.update(stage_failed="install", error_class=cls,
                     error_detail=tail[:600], error_stderr=tail[-600:])
            return r
        if entry:
            # run the package we just installed -- no npx re-resolution. The
            # registry's declared args must ride along, or a server whose stdio
            # mode is a subcommand just prints its usage screen and looks broken.
            cmd = ["node", entry] + list(extra_args)
            r["prewarmed"] = True
            r["spawn_mode"] = "node-entrypoint"
        else:
            # bin unresolvable: fall back to npx. Install is warm in the npm cache
            # but npx still re-links, so t_boot_ms is an upper bound here.
            r["prewarmed"] = True
            r["spawn_mode"] = "npx-fallback"

    p = Probe(cmd, env=env, install_timeout=install_timeout, rpc_timeout=rpc_timeout)
    p.cmd = resolve_cmd(cmd)
    r["cmd"] = cmd
    # absolute ceiling -- nothing wedges a shard, even if every stage timeout is dodged
    watchdog = threading.Timer(max(1.0, hard_wall - (time.time() - wall0)), p.kill)
    watchdog.daemon = True
    watchdog.start()
    t0 = time.time()
    try:
        p.start()
        r["t_spawn_ms"] = int((time.time() - t0) * 1000)
    except (FileNotFoundError, OSError) as e:
        watchdog.cancel()
        if warm_dir:
            shutil.rmtree(warm_dir, ignore_errors=True)
        r.update(stage_failed="spawn", error_class="COMMAND_NOT_FOUND", error_detail=str(e))
        return r

    def finish():
        """Single exit path: stop the watchdog, publish the pollution flag, tear down."""
        watchdog.cancel()
        r["stdout_polluted"] = p.stdout_polluted
        r["undeclared_creds"] = r["error_class"] == "UNDECLARED_CREDENTIALS"
        if r["error_stderr"] is None and p.stderr_buf:
            r["error_stderr"] = p.stderr_tail()
        p.kill()
        if warm_dir:
            shutil.rmtree(warm_dir, ignore_errors=True)
        return r

    # --- initialize ---
    # Budget is the boot budget once the package is already cached; targets that
    # skipped prewarm still get the full install budget here.
    init_budget = boot_timeout if r["prewarmed"] else install_timeout
    try:
        p.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcpwatch-probe", "version": "0.1.0"},
        }})
        resp = p.recv(1, timeout=init_budget)
        r["t_boot_ms"] = int((time.time() - t0) * 1000)
        # t_init_ms stays install+boot for continuity; t_boot_ms is the honest one
        r["t_init_ms"] = r["t_boot_ms"] + (r["t_install_ms"] or 0)
    except TimeoutError as e:
        tail = p.stderr_tail()
        # a server hung waiting on an OAuth prompt is blocked on a human, not slow
        cls = classify_prestart_stderr(tail, "INIT_TIMEOUT")
        r.update(stage_failed="initialize", error_class=cls, error_detail=str(e),
                 error_stderr=tail)
        return finish()
    except (ConnectionError, BrokenPipeError) as e:
        tail = p.stderr_tail()
        low = tail.lower()
        # after a successful prewarm the package demonstrably installs, so a death
        # here is a crash, not an install failure -- never misattribute it
        default = "CRASH_ON_START" if r["prewarmed"] else (
            "INSTALL_FAILED" if any(k in low for k in INSTALL_MARKERS) else "CRASH_ON_START")
        # the server told us what it wanted -- believe it over the registry metadata
        cls = classify_prestart_stderr(tail, default)
        r.update(stage_failed="initialize", error_class=cls,
                 error_detail=(tail or str(e))[:600], error_stderr=tail)
        return finish()

    if "error" in resp:
        detail = json.dumps(resp["error"])[:400]
        cls = "UNDECLARED_CREDENTIALS" if looks_like_missing_credentials(detail) else "INIT_RPC_ERROR"
        if cls == "UNDECLARED_CREDENTIALS":
            r["undeclared_creds"] = True
        r.update(stage_failed="initialize", error_class=cls, error_detail=detail)
        return finish()

    result = resp.get("result", {})
    r["protocol_version"] = result.get("protocolVersion")
    r["server_info"] = result.get("serverInfo")

    # --- tools/list ---
    t1 = time.time()
    try:
        p.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        p.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = p.recv(2, timeout=p.rpc_timeout)
        r["t_tools_ms"] = int((time.time() - t1) * 1000)
    except Exception as e:
        r.update(stage_failed="tools/list", error_class="TOOLS_TIMEOUT", error_detail=str(e))
        return finish()

    if "error" in resp:
        r.update(stage_failed="tools/list", error_class="TOOLS_RPC_ERROR",
                 error_detail=json.dumps(resp["error"])[:400])
        return finish()

    tools = resp.get("result", {}).get("tools", [])
    r["tool_count"] = len(tools)
    r["tool_names"] = sorted(t.get("name", "?") for t in tools)
    # schema fingerprint -> lets you detect silent breaking changes between versions
    canonical = json.dumps(
        [{"name": t.get("name"), "schema": t.get("inputSchema")} for t in sorted(tools, key=lambda x: x.get("name", ""))],
        sort_keys=True, separators=(",", ":"))
    r["schema_hash"] = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    r["ok"] = len(tools) > 0
    if not r["ok"]:
        r.update(stage_failed="tools/list", error_class="ZERO_TOOLS",
                 error_detail="handshake succeeded but server advertises no tools")
    return finish()


# Fixtures: one known-good, one known-bad. Both must classify correctly on every
# run -- if they don't, the sweep's numbers are not trustworthy.
FIXTURES = [
    {"server_name": "_fixture/everything", "identifier": "@modelcontextprotocol/server-everything",
     "version": None, "cmd": ["npx", "-y", "@modelcontextprotocol/server-everything"],
     "required_env": [], "required_args": [], "_expect": "PASS"},
    {"server_name": "_fixture/does-not-exist", "identifier": "@mcpwatch/definitely-not-a-real-package-9f3a",
     "version": None, "cmd": ["npx", "-y", "@mcpwatch/definitely-not-a-real-package-9f3a"],
     "required_env": [], "required_args": [], "_expect": "INSTALL_FAILED"},
]


def probe_target(t, **kw):
    """Probe one sync target dict. Credential-gated targets never spawn."""
    name = t.get("server_name") or t.get("identifier")
    req_env, req_args = t.get("required_env") or [], t.get("required_args") or []
    if req_env or req_args:
        # gotcha 1: not a failure. Counted separately, excluded from the rate.
        return {
            "server": name, "cmd": t.get("cmd"), "ok": False, "skipped": True,
            "stage_failed": None, "error_class": "SKIPPED_NEEDS_CREDENTIALS",
            "error_detail": json.dumps({"required_env": req_env, "required_args": req_args}),
            "error_stderr": None, "t_spawn_ms": None, "t_install_ms": None,
            "t_boot_ms": None, "t_init_ms": None, "t_tools_ms": None,
            "protocol_version": None, "server_info": None, "tool_count": None,
            "tool_names": [], "schema_hash": None, "stdout_polluted": False,
            "prewarmed": False, "identifier": t.get("identifier"), "version": t.get("version"),
            "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    ident, ver = t.get("identifier"), t.get("version")
    spec = f"{ident}@{ver}" if ident and ver else ident
    r = probe(name, t.get("cmd"), env=t.get("env"), spec=spec, identifier=ident,
              extra_args=t.get("args") or [], **kw)
    r.update(skipped=False, identifier=ident, version=ver)
    return r


def main():
    ap = argparse.ArgumentParser(description="MCPwatch stdio probe")
    ap.add_argument("--targets", help="targets.json from sync/sync_registry.py")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--of", type=int, default=1)
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--limit", type=int, default=None, help="cap targets (local sweeps)")
    ap.add_argument("--no-fixtures", action="store_true")
    ap.add_argument("--resume", action="store_true",
                    help="keep rows already in --out and probe only what is missing")
    ap.add_argument("--no-prewarm", action="store_true", help="fold install back into boot")
    ap.add_argument("--install-timeout", type=int, default=INSTALL_TIMEOUT)
    ap.add_argument("--boot-timeout", type=int, default=BOOT_TIMEOUT)
    ap.add_argument("--hard-wall", type=int, default=HARD_WALL_S)
    args = ap.parse_args()

    if args.targets:
        with open(args.targets, encoding="utf-8") as f:
            all_targets = json.load(f)
        targets = [t for i, t in enumerate(all_targets) if i % args.of == args.shard]
    else:
        targets = []
    # fixtures ride shard 0 only, so a 20-way matrix doesn't probe them 20 times
    if not args.no_fixtures and args.shard == 0:
        targets = FIXTURES + targets

    kw = dict(prewarm=not args.no_prewarm, install_timeout=args.install_timeout,
              boot_timeout=args.boot_timeout, hard_wall=args.hard_wall)

    def flush(rows):
        """Rewrite after every target. A shard killed by the job timeout must not
        lose the work it already did -- writing once at the end throws away the
        whole slice. Write-and-replace so a kill mid-write can't truncate the file.
        """
        tmp = args.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
        os.replace(tmp, args.out)

    out = []
    if args.resume and os.path.isfile(args.out):
        with open(args.out, encoding="utf-8") as f:
            out = json.load(f)
        done = {(r.get("server"), r.get("identifier")) for r in out}
        before = len(targets)
        targets = [t for t in targets
                   if (t.get("server_name") or t.get("identifier"), t.get("identifier")) not in done]
        print(f"resume: {len(out)} already probed, {len(targets)}/{before} remaining",
              file=sys.stderr, flush=True)

    # applied last, so --limit means "probe up to N MORE this run". Applying it
    # before the resume filter would just re-select already-finished targets and
    # do nothing, which is exactly the trap that ate one chunk.
    if args.limit:
        targets = targets[:args.limit]

    for i, t in enumerate(targets, 1):
        label = t.get("server_name") or t.get("identifier")
        print(f"[{i}/{len(targets)}] → {label}", file=sys.stderr, flush=True)
        try:
            res = probe_target(t, **kw)
        except Exception as e:  # a crashing probe is data, never a dead shard
            res = {"server": label, "cmd": t.get("cmd"), "ok": False, "skipped": False,
                   "error_class": "PROBE_EXCEPTION", "error_detail": repr(e)[:400],
                   "identifier": t.get("identifier"), "version": t.get("version")}
        out.append(res)
        flush(out)
        status = "PASS" if res.get("ok") else f"FAIL[{res.get('error_class')}]"
        print(f"    {status}  install={res.get('t_install_ms')}ms boot={res.get('t_boot_ms')}ms "
              f"tools={res.get('t_tools_ms')}ms n={res.get('tool_count')}",
              file=sys.stderr, flush=True)
        if t.get("_expect"):
            got = "PASS" if res.get("ok") else res.get("error_class")
            marker = "ok" if got == t["_expect"] else f"FIXTURE MISMATCH (want {t['_expect']})"
            print(f"    fixture: {marker}", file=sys.stderr, flush=True)

    flush(out)
    print(f"wrote {len(out)} results -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
