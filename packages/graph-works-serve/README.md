# graph-works-serve

`graph-works-serve` provides `gw-serve`, a loopback HTTP sidecar over
graph-works-core that emits graph-works-wire JSON projections for local agents.

```text
gw-serve [--workspace PATH] [--port 0] [--allow-origin ORIGIN ...] [--describe]
```

`--allow-origin` lets a browser page on another origin call the sidecar. It is repeatable and matches exactly (`scheme://host[:port]`, or the literal `null`). For an allowed origin, a CORS preflight is answered without a token, and every response carries `Access-Control-Allow-Origin`. A preflight from any other origin is refused with 403. With no flag, no CORS header is ever sent. Typical: `gw-serve --allow-origin http://127.0.0.1:4780 --allow-origin http://localhost:5173`.

`--allow-origin null` admits the opaque origin a browser sends from a `file://` page — for example an Electron renderer such as gw-orca's packaged build: `gw-serve --allow-origin http://127.0.0.1:5188 --allow-origin null`. `null` is not one page's identity: every `file://` page, sandboxed iframe without `allow-same-origin`, `data:` document and redirected cross-origin request shares it, and any of them may then pass the preflight. None of them can authenticate without the token, which lives only in `serve.json` (mode `0600`) and in the memory of the process that read it, and the `Host` check still rejects DNS rebinding. The allow-list only decides which pages may read responses; it was never the authentication. `null` is matched exactly — `Null` or `NULL` is rejected at startup.

`serve.json` records `schema_version`, `pid`, `host`, `port`, `token`,
`gw_version`, `workspace`, and `started_at` in the workspace cache directory.

## Platform

POSIX (Linux, macOS) and Windows through WSL (ADR 2026-08-19-windows-is-supported). Native Windows is
deferred: `serve.json`'s `0600` mode and the pid-liveness probe
(`os.kill(pid, 0)`, in `discovery_file.pid_alive`) are POSIX behaviour, and the
agent-config route injects `os.getuid()` on POSIX only. Discovery-file writes
and ownership cleanup use a shared `fcntl.flock` record lock, while a separate
nonblocking lifetime `fcntl.flock` claim prevents a concurrent launcher from
starting before the first sidecar's HTTP health endpoint is ready. Each seam is
one small function, so a native-Windows arm is additive.
