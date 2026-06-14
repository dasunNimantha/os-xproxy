#!/usr/local/bin/python3

"""
Parse proxy URI strings (vless://, vmess://, ss://, trojan://) into
structured server definitions for the Coretun OPNsense plugin.

Usage: import_uris.py <file_with_uris>
Output: JSON on stdout with {"servers": [...], "errors": [...]}

The actual parsing rules live in uri_parser.py so they can be shared
with the subscription updater.
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Re-export the parser helpers so existing callers/tests that historically
# imported them from import_uris keep working after the move to uri_parser.
from uri_parser import (  # noqa: E402,F401
    parse_lines,
    parse_uri,
    parse_vless,
    parse_vmess,
    parse_shadowsocks,
    parse_trojan,
)

MAX_INPUT_BYTES = 2 * 1024 * 1024


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"servers": [], "errors": ["No input file specified."]}))
        sys.exit(0)

    filepath = sys.argv[1]
    try:
        with open(filepath, 'r', errors='replace') as f:
            content = f.read(MAX_INPUT_BYTES + 1)
    except IOError as e:
        print(json.dumps({"servers": [], "errors": [str(e)]}))
        sys.exit(0)

    if len(content) > MAX_INPUT_BYTES:
        print(json.dumps({"servers": [], "errors": ["Input file too large (max 2 MiB)."]}))
        sys.exit(0)

    print(json.dumps(parse_lines(content)))


if __name__ == '__main__':
    main()
