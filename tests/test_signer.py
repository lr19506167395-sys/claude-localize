from __future__ import annotations

import unittest
from unittest import mock

from helpers import ROOT  # noqa: F401

from claude_localize import backup, signer


class TeamIdentifierTests(unittest.TestCase):
    """ad-hoc 签名的 TeamIdentifier 是字符串 "not set"，不能当布尔值用。"""

    def _check(self, raw: str, expected: str):
        with mock.patch.object(signer, "signed_info", return_value={"TeamIdentifier": raw}):
            self.assertEqual(signer.team_identifier("x"), expected)

    def test_not_set_means_adhoc(self):
        self._check("not set", "")

    def test_empty(self):
        self._check("", "")

    def test_real_team(self):
        self._check("Q6L2SF6YDW", "Q6L2SF6YDW")

    def test_officially_signed_uses_normalized_value(self):
        with mock.patch.object(signer, "signed_info", return_value={"TeamIdentifier": "not set"}):
            self.assertFalse(backup.is_officially_signed("x"))
        with mock.patch.object(signer, "signed_info", return_value={"TeamIdentifier": "Q6L2SF6YDW"}):
            self.assertTrue(backup.is_officially_signed("x"))


if __name__ == "__main__":
    unittest.main()