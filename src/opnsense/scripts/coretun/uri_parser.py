#!/usr/local/bin/python3

"""
Shared proxy-URI parser for the Coretun OPNsense plugin.

Parses proxy URI strings (vless://, vmess://, ss://, trojan://) into
structured server definitions. Used by both the manual Import action
(import_uris.py) and the subscription updater (subscription_fetch.py),
so the parsing rules live in exactly one place.
"""

import json
import base64
from urllib.parse import parse_qs, unquote

# Recognised URI schemes — also used to sniff whether a (possibly
# base64-encoded) subscription body actually contains proxy links.
SCHEMES = ('vless://', 'vmess://', 'ss://', 'trojan://')


def pad_b64(s):
    # base64 input length must be a multiple of 4; pad with '=' as needed.
    return s + '=' * (-len(s) % 4)


def _b64decode_loose(text):
    """Decode standard or URL-safe base64, tolerating stray whitespace.

    Subscription endpoints typically return the whole link list as one
    base64 blob (sometimes URL-safe, sometimes wrapped over several
    lines). We strip whitespace, normalise the URL-safe alphabet ('-_')
    to the standard one ('+/'), then decode with validation. Validation
    matters: the non-validating decoder silently *drops* characters that
    aren't in the alphabet, which both corrupts URL-safe input and lets
    plainly non-base64 text decode to garbage instead of being rejected.
    """
    compact = ''.join(text.split())
    if not compact:
        raise ValueError("empty base64 input")
    normalised = compact.replace('-', '+').replace('_', '/')
    try:
        return base64.b64decode(pad_b64(normalised), validate=True).decode('utf-8', errors='replace')
    except Exception:
        raise ValueError("not valid base64")


def decode_subscription_body(text, mode='auto'):
    """Return the plain newline-separated URI list from a subscription body.

    mode:
      'no'   -> body is already plain text, return unchanged
      'yes'  -> body is always base64, decode it
      'auto' -> if the body already contains a known scheme, use as-is;
                otherwise attempt a base64 decode and keep it only when
                the result contains a known scheme.
    """
    if mode == 'no':
        return text
    if mode == 'yes':
        return _b64decode_loose(text)

    # auto
    if any(scheme in text for scheme in SCHEMES):
        return text
    try:
        decoded = _b64decode_loose(text)
    except ValueError:
        return text
    if any(scheme in decoded for scheme in SCHEMES):
        return decoded
    return text


def parse_vless(uri):
    """Parse vless://uuid@host:port?params#description"""
    rest = uri[len('vless://'):]
    fragment = ''
    if '#' in rest:
        rest, fragment = rest.rsplit('#', 1)
        fragment = unquote(fragment)

    if '@' not in rest:
        raise ValueError("vless URI missing '@'")

    userinfo, hostport = rest.split('@', 1)
    query = ''
    if '?' in hostport:
        hostport, query = hostport.split('?', 1)

    if ':' in hostport:
        host, port = hostport.rsplit(':', 1)
    else:
        host, port = hostport, '443'

    params = parse_qs(query)

    def p(k, d=''):
        return params.get(k, [d])[0]

    flow_raw = p('flow', '')

    return {
        'enabled': '1',
        'protocol': 'vless',
        'description': fragment or host,
        'address': host,
        'port': port,
        'user_id': userinfo,
        'encryption': p('encryption', 'none'),
        'flow': flow_raw.replace('-', '_') if flow_raw else '',
        'transport': p('type', 'tcp'),
        'transport_host': p('host'),
        'transport_path': p('path'),
        'security': p('security', 'none'),
        'sni': p('sni'),
        'fingerprint': p('fp', 'chrome'),
        'alpn': p('alpn'),
        'reality_pubkey': p('pbk'),
        'reality_short_id': p('sid'),
        'raw_uri': uri,
    }


def parse_vmess(uri):
    """Parse vmess://base64json"""
    encoded = uri[len('vmess://'):]
    try:
        decoded = base64.b64decode(pad_b64(encoded)).decode('utf-8')
        cfg = json.loads(decoded)
    except Exception:
        raise ValueError("Invalid vmess base64 payload")

    transport = cfg.get('net', 'tcp')
    security = 'tls' if cfg.get('tls') == 'tls' else 'none'

    return {
        'enabled': '1',
        'protocol': 'vmess',
        'description': cfg.get('ps', cfg.get('add', '')),
        'address': cfg.get('add', ''),
        'port': str(cfg.get('port', 443)),
        'user_id': cfg.get('id', ''),
        'encryption': cfg.get('scy', 'auto'),
        'flow': '',
        'transport': transport,
        'transport_host': cfg.get('host', ''),
        'transport_path': cfg.get('path', ''),
        'security': security,
        'sni': cfg.get('sni', cfg.get('host', '')),
        'fingerprint': cfg.get('fp', 'chrome'),
        'alpn': cfg.get('alpn', ''),
        'reality_pubkey': '',
        'reality_short_id': '',
        'raw_uri': uri,
    }


def parse_shadowsocks(uri):
    """Parse ss://base64(method:password)@host:port#description or ss://base64(...)#desc"""
    rest = uri[len('ss://'):]
    fragment = ''
    if '#' in rest:
        rest, fragment = rest.rsplit('#', 1)
        fragment = unquote(fragment)

    if '@' in rest:
        userinfo, hostport = rest.split('@', 1)
        try:
            decoded = base64.b64decode(pad_b64(userinfo)).decode('utf-8')
        except Exception:
            decoded = userinfo
        if ':' in decoded:
            method, password = decoded.split(':', 1)
        else:
            method, password = 'aes-256-gcm', decoded
        if ':' in hostport:
            host, port = hostport.rsplit(':', 1)
        else:
            host, port = hostport, '443'
    else:
        try:
            decoded = base64.b64decode(pad_b64(rest)).decode('utf-8')
        except Exception:
            raise ValueError("Invalid ss base64 payload")
        if '@' in decoded:
            cred, hostport = decoded.split('@', 1)
            method, password = cred.split(':', 1) if ':' in cred else ('aes-256-gcm', cred)
            host, port = hostport.rsplit(':', 1) if ':' in hostport else (hostport, '443')
        else:
            raise ValueError("Cannot parse ss URI")

    return {
        'enabled': '1',
        'protocol': 'shadowsocks',
        'description': fragment or host,
        'address': host,
        'port': port,
        'user_id': '',
        'password': password,
        'encryption': method,
        'flow': '',
        'transport': 'tcp',
        'transport_host': '',
        'transport_path': '',
        'security': 'none',
        'sni': '',
        'fingerprint': '',
        'alpn': '',
        'reality_pubkey': '',
        'reality_short_id': '',
        'raw_uri': uri,
    }


def parse_trojan(uri):
    """Parse trojan://password@host:port?params#description"""
    rest = uri[len('trojan://'):]
    fragment = ''
    if '#' in rest:
        rest, fragment = rest.rsplit('#', 1)
        fragment = unquote(fragment)

    if '@' not in rest:
        raise ValueError("trojan URI missing '@'")

    password, hostport = rest.split('@', 1)
    query = ''
    if '?' in hostport:
        hostport, query = hostport.split('?', 1)

    if ':' in hostport:
        host, port = hostport.rsplit(':', 1)
    else:
        host, port = hostport, '443'

    params = parse_qs(query)

    def p(k, d=''):
        return params.get(k, [d])[0]

    security = p('security', 'tls')
    if security not in ('tls', 'reality', 'none'):
        security = 'tls'

    return {
        'enabled': '1',
        'protocol': 'trojan',
        'description': fragment or host,
        'address': host,
        'port': port,
        'user_id': '',
        'password': password,
        'encryption': '',
        'flow': '',
        'transport': p('type', 'tcp'),
        'transport_host': p('host'),
        'transport_path': p('path'),
        'security': security,
        'sni': p('sni', host),
        'fingerprint': p('fp', 'chrome'),
        'alpn': p('alpn'),
        'reality_pubkey': p('pbk'),
        'reality_short_id': p('sid'),
        'raw_uri': uri,
    }


PARSERS = {
    'vless://': parse_vless,
    'vmess://': parse_vmess,
    'ss://': parse_shadowsocks,
    'trojan://': parse_trojan,
}


def parse_uri(line):
    line = line.strip()
    if not line:
        return None
    for prefix, parser in PARSERS.items():
        if line.startswith(prefix):
            return parser(line)
    raise ValueError("Unknown URI scheme: " + line[:20])


def parse_lines(content):
    """Parse a newline-separated list of proxy URIs.

    Returns {"servers": [...], "errors": [...]} so callers never have to
    worry about a single malformed line aborting the whole batch.
    """
    servers = []
    errors = []
    for line in content.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            srv = parse_uri(line)
            if srv:
                servers.append(srv)
        except Exception as e:
            errors.append("Failed to parse: %s (%s)" % (line[:60], str(e)))
    return {"servers": servers, "errors": errors}
