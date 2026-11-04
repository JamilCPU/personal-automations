# Running the hub on Linux (k3s)

Since 2026-11 jw runs Ubuntu + k3s, so the hub and the MCP HTTP server run as containers built from the
root `Dockerfile`. The Kubernetes manifests live in the private `lab-gitops` repo (`apps/personal-automations`).

- Hub and MCP use host networking, so they keep listening on jw's LAN address (`:7700` and `:7702`).
  The hub keeps its existing TLS certificate, so the paired Pixel app and stdio MCP clients that
  pin `hub/cert.pem` keep working.
- State lives in a host directory mounted at `/data`, i.e. `/data/personal-automations/{hub.toml,
  hub.secrets.toml, history.db, hub/, agents/}`; paths in `hub.toml` are Linux paths.
- jw itself is no longer a `kind = "local"` (Windows) device: it runs the normal Pi agent
  (`agent/deploy/deploy-agent.sh <jw-ip> jw-pc --k3s`) and is registered as `kind = "agent"`.
  jw is the k3s server now, so `k3s_source = true` moves from pi1 to jw-pc.

Build (multi-arch not needed, the hub only runs on jw):

```bash
docker buildx build --platform linux/amd64 -t <registry>/jamil/personal-automations-hub:<tag> --push .
```
