# graph-works-serve

`graph-works-serve` provides `gw-serve`, a loopback HTTP sidecar over
graph-works-core that emits graph-works-wire JSON projections for local agents.

```text
gw-serve [--workspace PATH] [--port 0] [--allow-origin ORIGIN ...] [--describe]
```

`--allow-origin` lets a browser page on another origin call the sidecar. It is repeatable and matches exactly (`scheme://host[:port]`). For an allowed origin, a CORS preflight is answered without a token, and every response carries `Access-Control-Allow-Origin`. A preflight from any other origin is refused with 403. With no flag, no CORS header is ever sent. Typical: `gw-serve --allow-origin http://127.0.0.1:4780 --allow-origin http://localhost:5173`.

`serve.json` records `schema_version`, `pid`, `host`, `port`, `token`,
`gw_version`, `workspace`, and `started_at` in the workspace cache directory.

## Platform

POSIX (Linux, macOS) and Windows through WSL (ADR-0021). Native Windows is
deferred: `serve.json`'s `0600` mode and the pid-liveness probe
(`os.kill(pid, 0)`, in `discovery_file.pid_alive`) are POSIX behaviour, and the
agent-config route injects `os.getuid()` on POSIX only. Discovery-file writes
and ownership cleanup use a shared `fcntl.flock` record lock, while a separate
nonblocking lifetime `fcntl.flock` claim prevents a concurrent launcher from
starting before the first sidecar's HTTP health endpoint is ready. Each seam is
one small function, so a native-Windows arm is additive.
