"""Tests for probe_servers.py — throwaway-instance probe config builder.

Only the pure config-building logic is unit-tested here (no xray process,
no network): build_probe_config must produce a valid single-server xray
config with a SOCKS inbound on the requested port and a direct route for
the server's own address (to avoid a resolution loop).
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                '..', 'src', 'opnsense', 'scripts', 'coretun'))

import probe_servers  # noqa: E402


def _reality_server(address='nl.example.com'):
    return {
        'uuid': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        'enabled': '1',
        'description': 'NL test',
        'protocol': 'vless',
        'address': address,
        'port': 40443,
        'user_id': '11111111-2222-3333-4444-555555555555',
        'password': '',
        'encryption': 'none',
        'flow': 'xtls-rprx-vision',
        'transport': 'tcp',
        'transport_host': '',
        'transport_path': '',
        'security': 'reality',
        'sni': 'www.spotify.com',
        'fingerprint': 'chrome',
        'alpn': '',
        'reality_pubkey': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
        'reality_short_id': 'abcdef0123456789',
    }


class TestBuildProbeConfig(unittest.TestCase):

    def test_socks_inbound_on_requested_port(self):
        cfg = probe_servers.build_probe_config(_reality_server(), 21080)
        inb = cfg['inbounds']
        self.assertEqual(len(inb), 1)
        self.assertEqual(inb[0]['protocol'], 'socks')
        self.assertEqual(inb[0]['port'], 21080)
        self.assertEqual(inb[0]['listen'], '127.0.0.1')

    def test_outbound_tags_present(self):
        cfg = probe_servers.build_probe_config(_reality_server(), 21080)
        tags = [o.get('tag') for o in cfg['outbounds']]
        self.assertIn('proxy', tags)
        self.assertIn('direct', tags)
        self.assertIn('block', tags)

    def test_reality_stream_settings(self):
        cfg = probe_servers.build_probe_config(_reality_server(), 21080)
        proxy = next(o for o in cfg['outbounds'] if o.get('tag') == 'proxy')
        stream = proxy['streamSettings']
        self.assertEqual(stream['security'], 'reality')
        self.assertEqual(stream['realitySettings']['publicKey'],
                         'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA')
        self.assertEqual(stream['realitySettings']['shortId'], 'abcdef0123456789')

    def test_hostname_routed_direct(self):
        # The server's own hostname must egress directly, not via the proxy,
        # otherwise resolving it would loop through the tunnel under test.
        cfg = probe_servers.build_probe_config(_reality_server('nl.example.com'), 21080)
        direct_domains = []
        for rule in cfg['routing']['rules']:
            if rule.get('outboundTag') == 'direct':
                direct_domains += rule.get('domain', [])
        self.assertIn('full:nl.example.com', direct_domains)

    def test_ip_address_routed_direct(self):
        cfg = probe_servers.build_probe_config(_reality_server('203.0.113.7'), 21080)
        direct_ips = []
        for rule in cfg['routing']['rules']:
            if rule.get('outboundTag') == 'direct':
                direct_ips += rule.get('ip', [])
        self.assertIn('203.0.113.7', direct_ips)


if __name__ == '__main__':
    unittest.main()
