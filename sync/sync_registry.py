#!/usr/bin/env python3
"""
MCPwatch registry sync.

Pages the official MCP registry, keeps latest-version entries only, and emits
one probe target per npm+stdio package.

Writes targets.json unconditionally. Upserts into Postgres only if DATABASE_URL
is set and psycopg is importable -- the sweep must be runnable before the DB exists.
"""
import argparse, json, os, sys, time, urllib.error, urllib.parse, urllib.request

REGISTRY = "https://registry.modelcontextprotocol.io/v0/servers"
OFFICIAL_META = "io.modelcontextprotocol.registry/official"
USER_AGENT = "mcpwatch-sync/0.1 (+https://github.com/mcpwatch)"


def fetch_page(cursor=None, limit=100, retries=4):
    url = f"{REGISTRY}?limit={limit}"
    if cursor:
        url += "&cursor=" + urllib.parse.quote(cursor, safe="")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"registry fetch failed after {retries} tries: {url}: {last}")


def iter_servers(max_pages=None, verbose=True):
    cursor, page = None, 0
    while True:
        data = fetch_page(cursor)
        entries = data.get("servers", [])
        for e in entries:
            yield e
        page += 1
        if verbose:
            print(f"  page {page}: {len(entries)} entries", file=sys.stderr, flush=True)
        cursor = (data.get("metadata") or {}).get("nextCursor")
        if not cursor or not entries:
            return
        if max_pages and page >= max_pages:
            return


def required_names(items):
    """environmentVariables[] / packageArguments[] entries flagged isRequired."""
    out = []
    for it in items or []:
        if it.get("isRequired"):
            out.append(it.get("name") or it.get("value") or it.get("valueHint") or "?")
    return out


def build_cmd(pkg):
    """npx invocation pinned to the registry's declared version.

    runtimeHint is honoured when the publisher set one (some declare 'npx'
    explicitly, a few declare 'node'); otherwise npx is the npm default.
    """
    ident, version = pkg["identifier"], pkg.get("version")
    spec = f"{ident}@{version}" if version else ident
    hint = (pkg.get("runtimeHint") or "npx").strip()
    if hint in ("npx", "npm", "node", ""):
        return ["npx", "-y", spec]
    return [hint, "-y", spec]


def targets_from_entry(entry):
    srv = entry.get("server") or {}
    meta = (entry.get("_meta") or {}).get(OFFICIAL_META) or {}
    name = srv.get("name")
    if not name:
        return []
    out = []
    for pkg in srv.get("packages") or []:
        if pkg.get("registryType") != "npm":
            continue
        if ((pkg.get("transport") or {}).get("type")) != "stdio":
            continue
        if not pkg.get("identifier"):
            continue
        req_env = required_names(pkg.get("environmentVariables"))
        req_args = required_names(pkg.get("packageArguments")) + required_names(pkg.get("runtimeArguments"))
        out.append({
            "server_name": name,
            "version": pkg.get("version") or srv.get("version"),
            "registry_type": "npm",
            "identifier": pkg["identifier"],
            "cmd": build_cmd(pkg),
            "required_env": req_env,
            "required_args": req_args,
            "package_arguments": pkg.get("packageArguments") or [],
            "repository_url": (srv.get("repository") or {}).get("url"),
            "server_version": srv.get("version"),
            "status": meta.get("status"),
            "published_at": meta.get("publishedAt"),
        })
    return out


def sync(max_pages=None):
    total = latest = 0
    targets, seen = [], set()
    dupes = 0
    for entry in iter_servers(max_pages=max_pages):
        total += 1
        meta = (entry.get("_meta") or {}).get(OFFICIAL_META) or {}
        if not meta.get("isLatest"):
            continue
        latest += 1
        for t in targets_from_entry(entry):
            key = (t["server_name"], t["identifier"], t["version"])
            if key in seen:
                dupes += 1
                continue
            seen.add(key)
            targets.append(t)
    return targets, {"total_versions": total, "latest": latest, "dupes_dropped": dupes}


def upsert_pg(targets):
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL unset -- skipping Postgres upsert", file=sys.stderr)
        return
    try:
        import psycopg
    except ImportError:
        print("psycopg not installed -- skipping Postgres upsert", file=sys.stderr)
        return
    ddl = """
    CREATE TABLE IF NOT EXISTS targets (
      server_name     text NOT NULL,
      identifier      text NOT NULL,
      version         text NOT NULL,
      registry_type   text NOT NULL,
      cmd             jsonb NOT NULL,
      required_env    jsonb NOT NULL DEFAULT '[]',
      required_args   jsonb NOT NULL DEFAULT '[]',
      repository_url  text,
      is_latest       boolean NOT NULL DEFAULT true,
      synced_at       timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (server_name, identifier, version)
    );"""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(ddl)
        for t in targets:
            cur.execute("""
                INSERT INTO targets (server_name, identifier, version, registry_type, cmd,
                                     required_env, required_args, repository_url, is_latest, synced_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true, now())
                ON CONFLICT (server_name, identifier, version) DO UPDATE SET
                  cmd = EXCLUDED.cmd, required_env = EXCLUDED.required_env,
                  required_args = EXCLUDED.required_args, repository_url = EXCLUDED.repository_url,
                  is_latest = true, synced_at = now();
            """, (t["server_name"], t["identifier"], t["version"] or "", t["registry_type"],
                  json.dumps(t["cmd"]), json.dumps(t["required_env"]),
                  json.dumps(t["required_args"]), t["repository_url"]))
        conn.commit()
    print(f"upserted {len(targets)} targets into Postgres", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="targets.json")
    ap.add_argument("--max-pages", type=int, default=None, help="debug: stop early")
    ap.add_argument("--no-db", action="store_true")
    args = ap.parse_args()

    print("syncing registry ...", file=sys.stderr)
    targets, stats = sync(max_pages=args.max_pages)

    needs_creds = [t for t in targets if t["required_env"] or t["required_args"]]
    probeable = len(targets) - len(needs_creds)
    distinct_servers = len({t["server_name"] for t in targets})

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(targets, f, indent=2)

    print(f"""
registry sync summary
  server versions seen:      {stats['total_versions']}
  isLatest only:             {stats['latest']}
  npm + stdio targets:       {len(targets)}  ({distinct_servers} distinct servers)
    probeable:               {probeable}
    needs credentials/args:  {len(needs_creds)}
  duplicate targets dropped: {stats['dupes_dropped']}
  wrote:                     {args.out}
""".rstrip(), file=sys.stderr)

    if not args.no_db:
        upsert_pg(targets)


if __name__ == "__main__":
    main()
