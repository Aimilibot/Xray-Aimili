# Aimili-VPNGate Product UX Roadmap

## Product Direction

Aimili-VPNGate should feel like a stable "public VPN exit manager", not a mixed control panel where VPNGate, OpenVPN, proxy, Xray, subscriptions, and routing all compete for attention.

The core user promise:

- Find usable public VPNGate exits.
- Connect one reliably through OpenVPN.
- Expose a local HTTP/SOCKS proxy.
- Explain clearly which layer is broken when it fails.

## Keep

- Dashboard with current health, active node, proxy port, and exit IP.
- VPNGate node list, country filter, latency, protocol, and blacklist.
- Manual start/stop OpenVPN.
- Proxy connectivity test.
- Logs, but grouped by module and failure reason.
- Basic settings: panel login, panel port, proxy port, fixed country, scan intervals.

## Remove Or Hide From The Default Experience

These should move behind an "Advanced Gateway" mode or separate product area:

- Xray inbound management.
- Subscription-node workspace.
- Complex routing rules.
- WARP outbound management.
- Certificate/domain management.
- Traffic quotas and per-client Xray stats.

Reason: these are gateway-provider features, while the most urgent user job is making VPNGate exits stable and understandable.

## Add

- Layered health model:
  - API source health: VPNGate API reachable, mirror used, last sync time.
  - Node pool health: fetched, tested, usable, blacklisted.
  - OpenVPN health: installed, process running, tun device available, active config.
  - Proxy health: port listening, outbound test IP, latency.
  - Container health: NET_ADMIN, `/dev/net/tun`, data volume writable.

- Node quality labels:
  - Recommended: recently successful and stable.
  - Usable: connects but slower or less proven.
  - Unstable: intermittent failure.
  - Blocked: repeated failures or manual blacklist.

- Repair actions:
  - Missing OpenVPN: show install/package hint.
  - Missing tun device: show Docker permission hint.
  - Permission denied: show root/NET_ADMIN hint.
  - Port occupied: show conflicting port and process if available.
  - API failed: retry mirror, proxy fetch, or reduce SSL strictness.

- User modes:
  - Stable mode: prefer historical success rate.
  - Fast mode: prefer lowest latency.
  - Explore mode: test more countries and new nodes.

## Docker UX

The recommended Docker experience is the full panel mode in `AIMILI_SERVICE_MODE=full`.

In this mode:

- The Web panel, Xray, OpenVPN, VPNGate node manager, WARP/custom outbound support, and local proxy run in one container.
- The container exposes:
  - `8787` for the management panel.
  - `7928` for HTTP/SOCKS proxy.
- Runtime data is stored in `/data`, mounted from `./vpngate_data`.
- Xray inbound ports must be explicitly published in `docker-compose.yml` when users create public nodes.

VPNGate can still run as an independent Docker service in `AIMILI_SERVICE_MODE=vpngate` for advanced gateway deployments. In that mode, Xray startup is skipped and the main gateway can consume the service as a plain upstream proxy.
