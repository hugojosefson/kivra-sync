"""Synthetic tests for bounded BankID requests."""

import tempfile
import unittest
from unittest.mock import Mock, patch

from kivra.auth import KivraAuth


class AuthLimitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.auth = KivraAuth(temporary.name, Mock())
        self.auth.session = Mock()

    def test_polling_has_a_deadline(self):
        with patch("kivra.auth.time.monotonic", side_effect=[0, 301]):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                self.auth._poll_for_auth("/poll", "code", "verifier")
        self.auth.session.get.assert_not_called()

    def test_poll_request_has_a_timeout(self):
        self.auth.session.get.return_value.json.return_value = {"status": "stop"}
        with patch("kivra.auth.time.sleep"), self.assertRaises(SystemExit):
            self.auth._poll_for_auth("/poll", "code", "verifier")
        self.assertEqual(self.auth.session.get.call_args.kwargs["timeout"], (15, 30))

    def test_oauth_request_has_a_timeout(self):
        self.auth.session.post.return_value.status_code = 201
        self.auth._initialize_oauth2("challenge")
        self.assertEqual(self.auth.session.post.call_args.kwargs["timeout"], (15, 30))

    def test_poll_failure_removes_qr_file_and_closes_session(self):
        self.auth._initialize_oauth2 = Mock(return_value={
            "code": "synthetic", "qr_code": "synthetic", "next_poll_url": "/poll",
        })
        self.auth._render_and_display_qr = Mock()
        self.auth._poll_for_auth = Mock(side_effect=RuntimeError("test"))
        from pathlib import Path
        path = Path(self.auth.qr_temp_path)
        path.write_bytes(b"synthetic")
        with self.assertRaises(RuntimeError):
            self.auth.authenticate("unused")
        self.assertFalse(path.exists())
        self.auth.session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
