"""Offline tests for the native API configuration window's data handling."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from credential_dialog import (
    credentials_complete,
    normalize_credential_values,
    validate_credential_values,
)
from credential_store import load_credentials, save_credentials


class CredentialFormTests(unittest.TestCase):
    def test_normalizes_supported_paste_forms(self):
        values = normalize_credential_values({
            "mineru_token": "  Bearer mineru-token  ",
            "minimax_key": r"mini\_max",
            "siliconflow_key": "  silicon-key  ",
        })
        self.assertEqual(values, {
            "mineru_token": "mineru-token",
            "minimax_key": "mini_max",
            "siliconflow_key": "silicon-key",
        })

    def test_optional_key_is_removed_when_blank(self):
        values = normalize_credential_values({
            "mineru_token": "m",
            "minimax_key": "k",
            "siliconflow_key": "   ",
        })
        self.assertNotIn("siliconflow_key", values)
        self.assertIsNone(validate_credential_values(values))

    def test_both_required_services_must_be_nonempty(self):
        self.assertFalse(credentials_complete({"mineru_token": "", "minimax_key": ""}))
        self.assertFalse(credentials_complete({"mineru_token": "m"}))
        self.assertFalse(credentials_complete({"minimax_key": "k"}))
        self.assertTrue(credentials_complete({"mineru_token": "m", "minimax_key": "k"}))
        self.assertIn("MinerU", validate_credential_values({"mineru_token": "", "minimax_key": "k"}))
        self.assertIn("MiniMax", validate_credential_values({"mineru_token": "m", "minimax_key": ""}))

    def test_rejects_embedded_whitespace_and_control_characters(self):
        self.assertIn("空格或换行", validate_credential_values({
            "mineru_token": "m token", "minimax_key": "k",
        }))
        self.assertIn("空格或换行", validate_credential_values({
            "mineru_token": "m", "minimax_key": "key\nnext",
        }))


@unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
class DpapiRoundTripTests(unittest.TestCase):
    def test_dialog_values_are_encrypted_and_round_trip(self):
        values = {
            "mineru_token": "test-mineru-secret",
            "minimax_key": "test-minimax-secret",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "credentials.dat"
            save_credentials(values, path)
            payload = path.read_bytes()
            self.assertNotIn(values["mineru_token"].encode(), payload)
            self.assertNotIn(values["minimax_key"].encode(), payload)
            self.assertEqual(load_credentials(path), values)


if __name__ == "__main__":
    unittest.main()
