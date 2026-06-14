"""Tests for uri_parser.py — subscription body decoding and batch parsing.

The individual scheme parsers (parse_vless/vmess/ss/trojan) are exercised by
test_import_uris.py; here we cover the pieces added for subscriptions:
base64 detection/decoding and the parse_lines batch wrapper.
"""

import sys
import os
import base64
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                '..', 'src', 'opnsense', 'scripts', 'coretun'))

from uri_parser import (  # noqa: E402
    pad_b64,
    _b64decode_loose,
    decode_subscription_body,
    parse_lines,
)

VLESS_A = 'vless://11111111-2222-3333-4444-555555555555@example.com:443?encryption=none#a'
VLESS_B = 'vless://66666666-7777-8888-9999-000000000000@example.org:8443?encryption=none#b'


class TestPadB64(unittest.TestCase):

    def test_pads_to_multiple_of_four(self):
        self.assertEqual(len(pad_b64('YQ')) % 4, 0)       # 2 -> 4
        self.assertEqual(len(pad_b64('YWI')) % 4, 0)      # 3 -> 4
        self.assertEqual(pad_b64('YWJj'), 'YWJj')         # already a multiple


class TestB64DecodeLoose(unittest.TestCase):

    def test_standard_base64(self):
        enc = base64.b64encode(b'hello').decode()
        self.assertEqual(_b64decode_loose(enc), 'hello')

    def test_urlsafe_base64(self):
        raw = b'\xfb\xff\xbf data'
        enc = base64.urlsafe_b64encode(raw).decode()
        # URL-safe alphabet uses -/_ ; loose decoder must handle it.
        self.assertEqual(_b64decode_loose(enc), raw.decode('utf-8', errors='replace'))

    def test_tolerates_whitespace_and_newlines(self):
        enc = base64.b64encode(b'wrapped body').decode()
        wrapped = enc[:4] + '\n' + enc[4:8] + '  ' + enc[8:]
        self.assertEqual(_b64decode_loose(wrapped), 'wrapped body')

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            _b64decode_loose('   \n  ')

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            _b64decode_loose('!!!@@@')


class TestDecodeSubscriptionBody(unittest.TestCase):

    def test_mode_no_returns_unchanged(self):
        body = base64.b64encode((VLESS_A).encode()).decode()
        self.assertEqual(decode_subscription_body(body, 'no'), body)

    def test_mode_yes_decodes(self):
        body = base64.b64encode((VLESS_A + '\n' + VLESS_B).encode()).decode()
        out = decode_subscription_body(body, 'yes')
        self.assertIn('vless://', out)
        self.assertIn('example.org', out)

    def test_auto_plain_passthrough(self):
        body = VLESS_A + '\n' + VLESS_B
        self.assertEqual(decode_subscription_body(body, 'auto'), body)

    def test_auto_detects_and_decodes_base64(self):
        plain = VLESS_A + '\n' + VLESS_B
        body = base64.b64encode(plain.encode()).decode()
        out = decode_subscription_body(body, 'auto')
        self.assertEqual(out, plain)

    def test_auto_non_base64_non_scheme_returns_original(self):
        body = 'just some random text without any proxy links'
        self.assertEqual(decode_subscription_body(body, 'auto'), body)

    def test_auto_base64_without_scheme_returns_original(self):
        # decodes fine but result has no proxy scheme -> keep original.
        body = base64.b64encode(b'plain content, no links here').decode()
        self.assertEqual(decode_subscription_body(body, 'auto'), body)


class TestParseLines(unittest.TestCase):

    def test_mixed_valid_and_invalid(self):
        content = VLESS_A + '\n' + 'http://not-a-proxy' + '\n' + VLESS_B
        res = parse_lines(content)
        self.assertEqual(len(res['servers']), 2)
        self.assertEqual(len(res['errors']), 1)
        self.assertEqual(res['servers'][0]['address'], 'example.com')

    def test_blank_lines_skipped(self):
        content = '\n\n' + VLESS_A + '\n   \n'
        res = parse_lines(content)
        self.assertEqual(len(res['servers']), 1)
        self.assertEqual(res['errors'], [])

    def test_empty_input(self):
        res = parse_lines('')
        self.assertEqual(res, {'servers': [], 'errors': []})


if __name__ == '__main__':
    unittest.main()
