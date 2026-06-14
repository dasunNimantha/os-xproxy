# coretun

OPNsense plugin for Xray-core with transparent LAN routing.

When enabled, LAN traffic is routed through a VLESS, VMess, Shadowsocks, or Trojan tunnel — no configuration needed on individual devices.

## Features

- Transparent proxying via TUN interface (hev-socks5-tunnel) — phones, IoT, smart TVs, and guest devices are covered automatically
- VLESS (with XTLS-Vision / Reality), VMess, Shadowsocks, and Trojan protocols
- Import server profiles from standard proxy URIs (`vless://`, `vmess://`, `ss://`, `trojan://`)
- **Subscriptions** — add subscription links (plain or Base64 server lists), auto-update on a per-subscription schedule, with full sync (new servers added, removed ones deleted, manual servers untouched)
- **Keyword exclusion** — skip unwanted servers from a subscription by name (e.g. `LTE, TORRENT`); matches are never imported and are removed on the next sync
- **Connectivity watchdog with latency-based failover** — on outage, every server is probed through a throwaway xray instance and the lowest-latency working one is selected; if none work, the subscription is refreshed and retried
- **On-demand latency measurement** — a "Measure latency" button on the Servers tab tests every server and stores/shows the last result per server
- Per-interface routing — choose which LANs (e.g. Guest, IoT) route through the tunnel, or route all
- Policy-based routing with dynamic firewall rules — rules are only active while the service is running
- Context-aware server dialog — fields shown/hidden based on selected protocol, security, and transport
- Multiple server profiles with quick switching and auto-select on add/import/delete
- Near-zero downtime reconfigure — Apply restarts only xray-core while the tunnel stays alive (<1s vs ~10s)
- Hardened process lifecycle — file locking, PID verification, orphan cleanup, crash recovery
- Optimized xray config — sniffing with routeOnly, connection policy tuning, TCP Fast Open, DNS caching
- TCP buffer and congestion control tuning via `sysctl.d` for high-throughput proxy workloads
- Optional Prometheus metrics exporter (port 9101) for Grafana monitoring
- Service log viewer with smart auto-scroll

## How it works

1. **Xray-core** connects to the remote proxy server and exposes a local SOCKS5 endpoint
2. **hev-socks5-tunnel** creates a TUN interface (`tun9`) that routes traffic through the SOCKS5 endpoint
3. The plugin registers a virtual interface (`coretun`) and gateway (`CORETUN_TUN`) in OPNsense
4. Firewall rules route selected LAN interface traffic through the TUN gateway using OPNsense's `_firewall()` plugin hook
5. When "Route LAN through tunnel" is disabled, only local SOCKS5/HTTP proxy is available — TUN and firewall rules are skipped

## Subscriptions & auto-update

Add one or more subscription links on the **Subscriptions** tab. A subscription returns a list of proxy URIs (plain text or Base64-encoded); the plugin fetches it on the router (via `fetch`/`curl`, using the system CA bundle) and synchronises the servers into the model:

- **Full sync** — servers present in the subscription are added, servers that disappeared are removed, and servers you added manually (not tied to a subscription) are left untouched.
- **Per-subscription schedule** — each subscription has its own update interval in hours (default 4). An hourly cron tick refreshes only the subscriptions whose interval has elapsed.
- **Base64 mode** — `Auto-detect` (default), `Always Base64`, or `Plain text`.
- **Exclude keywords** — a comma-separated list (e.g. `LTE, TORRENT`); any server whose name contains one of them (case-insensitive) is skipped on add and removed on the next sync. Useful to keep mobile/throttled exit nodes off the router.
- **Manual control** — `Update now` (per row) and `Update all now` re-fetch immediately.

## Connectivity watchdog & failover

A watchdog runs every few minutes and probes the local SOCKS proxy. On a sustained outage it recovers automatically:

1. **Latency probe** — every other enabled server is tested by spinning up a throwaway xray instance and sending a real request through it (this verifies the full proxy chain — reality/TLS handshake, auth, egress — not just TCP reachability of the endpoint). Probes run in parallel.
2. **Failover** — the active server is switched to the lowest-latency server that actually passes traffic, then the tunnel is hot-reloaded.
3. **Subscription refresh** — if no server works, the relevant subscription(s) are refreshed to pull a fresh server list, and the watchdog retries on the next tick (rate-limited to avoid hammering providers).

The same probe powers the **Measure latency** button on the Servers tab, and the last measured latency is stored per server and shown in the grid.

## Dependencies

| Package | Source | Status |
|---|---|---|
| xray-core | [security/xray-core](https://www.freshports.org/security/xray-core/) | Installed by `install.sh` or manual setup |
| hev-socks5-tunnel | [heiher/hev-socks5-tunnel](https://github.com/heiher/hev-socks5-tunnel) | Downloaded by `install.sh` / `coretun setup` |

## Installation

SSH into your OPNsense firewall and run:

```bash
fetch -o - https://raw.githubusercontent.com/dasunNimantha/coretun/main/install.sh | sh
```

The installer copies the plugin files, installs `xray-core`, downloads the `hev-socks5-tunnel` binary, and restarts `configd`.

Then navigate to **VPN > Coretun** in the web UI to configure.

### Manual installation

```bash
pkg install -y xray-core

cd /tmp
fetch -o coretun.tar.gz https://github.com/dasunNimantha/coretun/archive/refs/heads/main.tar.gz
tar xzf coretun.tar.gz
cd coretun-main/src
find . -type f | while read FILE; do
  mkdir -p "$(dirname /usr/local/$FILE)"
  cp "$FILE" "/usr/local/$FILE"
done
chmod +x /usr/local/opnsense/scripts/coretun/*.py /usr/local/opnsense/scripts/coretun/*.sh /usr/local/opnsense/scripts/coretun/*.php 2>/dev/null || true
/usr/local/opnsense/scripts/coretun/setup.sh
service configd restart
```

### Uninstall

```bash
fetch -o - https://raw.githubusercontent.com/dasunNimantha/coretun/main/uninstall.sh | sh
```

## UI

The plugin adds **VPN > Coretun** to the OPNsense sidebar with five tabs:

- **General** — Enable/disable the service, select active server, toggle transparent routing, choose which interfaces to tunnel, optional Prometheus exporter
- **Servers** — View and manage server profiles (shows protocol, address, port, security at a glance). Includes a per-server **Latency** column and a **Measure latency** button that probes every server and stores the last result
- **Subscriptions** — Add subscription URLs and manage them: per-subscription auto-update interval, Base64 decoding mode, keyword exclusion, plus **Update now** / **Update all now** buttons
- **Import** — Paste proxy URIs to batch-import server configurations (with parse error reporting)
- **Log** — Live service log viewer with smart auto-scroll (won't jump to bottom while you're reading)

The Active Server dropdown refreshes automatically when servers are added, deleted, edited, or imported. When no active server is set, the plugin auto-selects the first available server.

TUN-related fields (Tunnel Interfaces, TUN Device, TUN Address, TUN Gateway, Bypass IPs) are hidden when "Route LAN through tunnel" is unchecked, keeping the UI clean for SOCKS-only setups.

## Prometheus metrics

When the "Prometheus Exporter" toggle is enabled, a metrics endpoint is available at `http://<firewall-ip>:9101/metrics`. All metrics use the `coretun_` prefix.

### Process metrics (per xray-core and hev-socks5-tunnel)

| Metric | Type | Description |
|---|---|---|
| `coretun_up{process="xray\|tunnel"}` | gauge | Whether the process is running (1=up, 0=down) |
| `coretun_rss_bytes` | gauge | Resident set size in bytes |
| `coretun_vsz_bytes` | gauge | Virtual memory size in bytes |
| `coretun_cpu_percent` | gauge | Snapshot CPU usage percent |
| `coretun_cpu_seconds_total` | counter | Cumulative CPU time in seconds |
| `coretun_uptime_seconds` | gauge | Process uptime in seconds |

### System memory

| Metric | Type | Description |
|---|---|---|
| `coretun_system_memory_total_bytes` | gauge | Total physical memory |
| `coretun_system_memory_free_bytes` | gauge | Free memory |
| `coretun_system_memory_available_bytes` | gauge | Free + inactive (available for use) |
| `coretun_system_memory_active_bytes` | gauge | Active memory |
| `coretun_system_memory_inactive_bytes` | gauge | Inactive/cached pages |
| `coretun_system_memory_wired_bytes` | gauge | Wired (non-pageable) memory |

### Tunnel traffic

| Metric | Type | Description |
|---|---|---|
| `coretun_tunnel_bytes_total{direction="rx\|tx"}` | counter | Bytes through TUN interface |
| `coretun_tunnel_packets_total{direction="rx\|tx"}` | counter | Packets through TUN interface |

## Server compatibility

The OPNsense `xray-core` package tracks FreeBSD ports, which can lag behind upstream releases. If your server runs a newer Xray version (e.g. 26.x via 3x-ui), features like `testseed` may silently break the Reality handshake with the older client. Keep the server-side Xray version aligned with the OPNsense package, or use a 3x-ui release that bundles the same version (e.g. 3x-ui v2.8.7 ships Xray 25.12.8).

## Performance tuning

The plugin ships TCP tuning via `/usr/local/etc/sysctl.d/coretun.conf`:

- Large socket buffers (`maxsockbuf=16M`, send/recv max `8M`) sized for ~500 Mbps at 50ms RTT
- Initial congestion window of 44 segments for faster ramp-up on new connections
- CDG (CAIA Delay Gradient) congestion control — performs better than CUBIC on paths with bufferbloat or variable latency

On the VPS/server side, enable **BBR** congestion control for matched performance:

```bash
# Enable BBR (Linux)
modprobe tcp_bbr
sysctl -w net.ipv4.tcp_congestion_control=bbr
sysctl -w net.core.default_qdisc=fq

# Persist
echo tcp_bbr >> /etc/modules-load.d/bbr.conf
echo 'net.ipv4.tcp_congestion_control=bbr' >> /etc/sysctl.d/99-xray-tuning.conf
echo 'net.core.default_qdisc=fq' >> /etc/sysctl.d/99-xray-tuning.conf
```

## License

BSD 2-Clause. See [LICENSE](https://github.com/opnsense/plugins/blob/master/LICENSE).
