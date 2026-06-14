#!/usr/local/bin/python3

"""
Probe Coretun servers and report which ones actually pass traffic, ranked by
latency.

For every enabled server a throwaway xray-core instance is started on a free
local port, a generate_204 request is sent through its SOCKS inbound and the
round-trip time is measured. This tests the *real* proxy chain (reality/TLS
handshake, auth, egress) — unlike a plain TCP ping to the endpoint, which for
CDN/reality-fronted servers succeeds even when the tunnel itself is dead.

The xray config builders are reused from service_control.py so the probe and
the live service stay in sync.

Usage:
  probe_servers.py [--exclude <uuid>] [--max <n>]
Output (stdout, JSON):
  {"results":[{"uuid","description","ok","latency_ms"}...], "best":"<uuid|null>"}
"""

import sys
import os
import json
import socket
import subprocess
import time
import tempfile
import ipaddress
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import service_control as sc  # noqa: E402  (reuse config builders + paths)

PROBE_URLS = [
    'http://www.gstatic.com/generate_204',
    'http://cp.cloudflare.com/generate_204',
]
START_WAIT = 2.0       # max seconds to wait for the throwaway xray to bind
PROBE_TIMEOUT = 4      # curl --max-time per probe URL
MAX_WORKERS = 6        # probed in parallel to keep total runtime bounded
STATE_FILE = os.path.join(sc.CONFIG_DIR, 'probe_state.json')  # per-server last result


def save_state(results):
    """Persist each tested server's last result so the UI grid can show it.
    Existing entries for servers not tested in this run are kept (merge)."""
    state = {}
    try:
        with open(STATE_FILE) as f:
            old = json.load(f)
            if isinstance(old, dict):
                state = old
    except (OSError, ValueError):
        pass
    now = int(time.time())
    for r in results:
        state[r['uuid']] = {'ok': r['ok'], 'latency_ms': r['latency_ms'], 'ts': now}
    try:
        os.makedirs(sc.CONFIG_DIR, exist_ok=True)
        tmp = STATE_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


def free_port():
    """Ask the kernel for an unused local TCP port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]
    finally:
        s.close()


def build_probe_config(server, socks_port):
    """Minimal xray config: one SOCKS inbound -> the server's outbound."""
    outbound = sc.build_outbound(server)  # tag "proxy", protocol-specific
    addr = (server.get('address') or '').strip()

    # Same loop-avoidance as the live config: the proxy server's own address
    # must be reached directly, and its name resolved via a direct DNS server.
    dns_servers = ['1.1.1.1', '8.8.8.8', 'localhost']
    rules = []
    if addr:
        try:
            ipaddress.ip_address(addr)
            rules.append({'type': 'field', 'ip': [addr], 'outboundTag': 'direct'})
        except ValueError:
            rules.append({'type': 'field', 'domain': ['full:' + addr], 'outboundTag': 'direct'})
            dns_servers.insert(0, {'address': '1.1.1.1', 'domains': ['full:' + addr]})

    return {
        'log': {'loglevel': 'error'},
        'dns': {'servers': dns_servers, 'queryStrategy': 'UseIPv4'},
        'inbounds': [{
            'tag': 'in',
            'protocol': 'socks',
            'listen': '127.0.0.1',
            'port': socks_port,
            'settings': {'udp': False},
        }],
        'outbounds': [
            outbound,
            {'tag': 'direct', 'protocol': 'freedom'},
            {'tag': 'block', 'protocol': 'blackhole'},
        ],
        'routing': {'domainStrategy': 'AsIs', 'rules': rules},
    }


def _curl_latency(socks_port, url):
    """Return latency in ms if the request succeeds through the SOCKS port, else None."""
    try:
        r = subprocess.run(
            ['/usr/local/bin/curl', '-s', '-o', '/dev/null',
             '--socks5-hostname', '127.0.0.1:%d' % socks_port,
             '--max-time', str(PROBE_TIMEOUT),
             '-w', '%{http_code} %{time_total}', url],
            capture_output=True, timeout=PROBE_TIMEOUT + 3, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    parts = r.stdout.decode('utf-8', errors='replace').strip().split()
    if len(parts) == 2 and parts[0] in ('200', '204'):
        try:
            return int(float(parts[1]) * 1000)
        except ValueError:
            return None
    return None


def probe_one(server):
    """Return (ok, latency_ms) for a single server."""
    if not (server.get('address') or '').strip():
        return (False, None)
    if server.get('protocol') not in sc.SUPPORTED_PROTOCOLS:
        return (False, None)

    port = free_port()
    cfg = build_probe_config(server, port)
    fd, path = tempfile.mkstemp(prefix='coretun_probe_', suffix='.json')
    proc = None
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(cfg, f)

        if not os.path.isfile(sc.XRAY_BIN):
            return (False, None)

        proc = subprocess.Popen(
            [sc.XRAY_BIN, 'run', '-c', path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, env=sc._xray_env(),
        )

        # Wait until the SOCKS port accepts connections (or give up).
        deadline = time.time() + START_WAIT
        up = False
        while time.time() < deadline:
            try:
                c = socket.create_connection(('127.0.0.1', port), 0.3)
                c.close()
                up = True
                break
            except OSError:
                time.sleep(0.1)
        if not up:
            return (False, None)

        best = None
        for url in PROBE_URLS:
            ms = _curl_latency(port, url)
            if ms is not None:
                best = ms if best is None else min(best, ms)
                break  # one success is enough to mark the server working
        return (best is not None, best)
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    exclude = set()
    max_n = 0
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--exclude' and i + 1 < len(args):
            exclude.add(args[i + 1])
            i += 2
        elif args[i] == '--max' and i + 1 < len(args):
            try:
                max_n = int(args[i + 1])
            except ValueError:
                max_n = 0
            i += 2
        else:
            i += 1

    cfg = sc.read_config()
    candidates = []
    if cfg:
        for srv in cfg['servers']:
            if srv.get('enabled') != '1':
                continue
            if srv['uuid'] in exclude:
                continue
            candidates.append(srv)
            if max_n and len(candidates) >= max_n:
                break

    def _probe(srv):
        ok, ms = probe_one(srv)
        return {
            'uuid': srv['uuid'],
            'description': srv.get('description', ''),
            'ok': ok,
            'latency_ms': ms,
        }

    results = []
    if candidates:
        workers = min(MAX_WORKERS, len(candidates))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_probe, candidates))

    if results:
        save_state(results)

    working = sorted([r for r in results if r['ok']], key=lambda r: r['latency_ms'])
    best = working[0]['uuid'] if working else None
    print(json.dumps({'results': results, 'best': best}))


if __name__ == '__main__':
    main()
