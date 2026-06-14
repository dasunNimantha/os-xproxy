#!/usr/local/bin/python3

"""
Fetch a Coretun subscription URL, base64-decode the body if needed, and
parse the contained proxy URIs.

Usage:   subscription_fetch.py <base64_mode> <url>
         base64_mode = auto | yes | no
Output:  JSON {"servers": [...], "errors": [...], "fetch_error": "..."}

Networking is done with the system `fetch`/`curl` binaries (not Python's
ssl module) because OPNsense's bundled PHP/Python have no openssl.cafile
configured, while fetch/curl use the FreeBSD system CA bundle. The URL is
never fetched here on the model's behalf without an explicit request — the
caller (subscription_sync.php / the UI) decides when to run this.
"""

import sys
import os
import json
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uri_parser import decode_subscription_body, parse_lines  # noqa: E402

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 25


def fetch_url(url):
    """Return (body_text, error). Tries fetch(1) first, then curl."""
    # FreeBSD fetch: -q quiet, -T total timeout, -o - to stdout.
    attempts = [
        ['/usr/bin/fetch', '-q', '-T', str(TIMEOUT), '--user-agent=coretun/1.0', '-o', '-', url],
        ['/usr/local/bin/curl', '-fsSL', '--max-time', str(TIMEOUT),
         '-A', 'coretun/1.0', url],
    ]
    last_err = 'no downloader available'
    for cmd in attempts:
        if not os.path.isfile(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT + 5, check=False)
        except subprocess.TimeoutExpired:
            last_err = 'timeout fetching URL'
            continue
        except OSError as e:
            last_err = str(e)
            continue
        if r.returncode == 0 and r.stdout:
            body = r.stdout[:MAX_BYTES].decode('utf-8', errors='replace')
            return body, None
        last_err = (r.stderr.decode('utf-8', errors='replace').strip()
                    or ('exit code %d' % r.returncode))
    return None, last_err


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"servers": [], "errors": [], "fetch_error": "usage: subscription_fetch.py <mode> <url>"}))
        sys.exit(0)

    mode = sys.argv[1]
    if mode not in ('auto', 'yes', 'no'):
        mode = 'auto'
    url = sys.argv[2]

    body, err = fetch_url(url)
    if err is not None:
        print(json.dumps({"servers": [], "errors": [], "fetch_error": err}))
        sys.exit(0)

    try:
        plain = decode_subscription_body(body, mode)
    except Exception as e:
        print(json.dumps({"servers": [], "errors": [], "fetch_error": "decode failed: %s" % e}))
        sys.exit(0)

    result = parse_lines(plain)
    result["fetch_error"] = None
    print(json.dumps(result))


if __name__ == '__main__':
    main()
