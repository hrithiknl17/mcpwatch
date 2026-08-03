#!/usr/bin/env python3
"""
MCPwatch stdio probe.
Spawns an MCP server as a child process, speaks JSON-RPC over stdio,
and records staged timings + failure classification.
"""
import json, subprocess, sys, time, threading, queue, os, hashlib

PROTOCOL_VERSION = "2025-06-18"

class Probe:
    def __init__(self, cmd, env=None, install_timeout=120, rpc_timeout=20):
        self.cmd = cmd
        self.env = {**os.environ, **(env or {})}
        self.install_timeout = install_timeout
        self.rpc_timeout = rpc_timeout
        self.proc = None
        self.q = queue.Queue()
        self.stderr_buf = []

    def _reader(self, stream, q):
        for line in stream:
            q.put(line)
        q.put(None)

    def _stderr_reader(self, stream):
        for line in stream:
            self.stderr_buf.append(line.rstrip())

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
                continue  # non-JSON on stdout = spec violation, but not fatal
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


def probe(name, cmd, env=None):
    r = {
        "server": name, "cmd": cmd, "ok": False, "stage_failed": None,
        "error_class": None, "error_detail": None,
        "t_spawn_ms": None, "t_init_ms": None, "t_tools_ms": None,
        "protocol_version": None, "server_info": None,
        "tool_count": None, "tool_names": [], "schema_hash": None,
        "stdout_polluted": False,
    }
    p = Probe(cmd, env=env)
    t0 = time.time()
    try:
        p.start()
    except FileNotFoundError as e:
        r.update(stage_failed="spawn", error_class="COMMAND_NOT_FOUND", error_detail=str(e))
        return r

    # --- initialize ---
    try:
        p.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcpwatch-probe", "version": "0.1.0"},
        }})
        resp = p.recv(1, timeout=p.install_timeout)
        r["t_init_ms"] = int((time.time() - t0) * 1000)
    except TimeoutError as e:
        r.update(stage_failed="initialize", error_class="INIT_TIMEOUT", error_detail=str(e),
                 error_stderr="\n".join(p.stderr_buf[-8:]))
        p.kill(); return r
    except (ConnectionError, BrokenPipeError) as e:
        tail = "\n".join(p.stderr_buf[-8:])
        cls = "INSTALL_FAILED" if any(k in tail.lower() for k in ("npm error", "404", "enoent", "could not determine")) else "CRASH_ON_START"
        r.update(stage_failed="initialize", error_class=cls, error_detail=tail[:600])
        p.kill(); return r

    if "error" in resp:
        r.update(stage_failed="initialize", error_class="INIT_RPC_ERROR",
                 error_detail=json.dumps(resp["error"])[:400])
        p.kill(); return r

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
        p.kill(); return r

    if "error" in resp:
        r.update(stage_failed="tools/list", error_class="TOOLS_RPC_ERROR",
                 error_detail=json.dumps(resp["error"])[:400])
        p.kill(); return r

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
    p.kill()
    return r


if __name__ == "__main__":
    targets = [
        ("io.github.modelcontextprotocol/everything",
         ["npx", "-y", "@modelcontextprotocol/server-everything"], None),
        ("io.github.domdomegg/filesystem-mcp",
         ["npx", "-y", "filesystem-mcp"], None),
        ("io.github.fake/does-not-exist",
         ["npx", "-y", "@mcpwatch/definitely-not-a-real-package-9f3a"], None),
    ]
    out = []
    for name, cmd, env in targets:
        print(f"→ probing {name} ...", file=sys.stderr, flush=True)
        res = probe(name, cmd, env)
        out.append(res)
        status = "PASS" if res["ok"] else f"FAIL[{res['error_class']}]"
        print(f"   {status}  init={res['t_init_ms']}ms tools={res['t_tools_ms']}ms n={res['tool_count']}",
              file=sys.stderr, flush=True)
    print(json.dumps(out, indent=2))
