"""Use synthetic canaries to find private data in error messages."""

import logging
import tempfile
import unittest
from unittest.mock import Mock, patch

from kivra.api import KivraApiClient
from kivra.auth import KivraAuth


CANARY = "SYNTHETIC_PRIVATE_CANARY"


class RedactionTests(unittest.TestCase):
    def assert_redacted(self, action):
        with self.assertLogs(level=logging.DEBUG) as captured:
            logging.debug("Test error path")
            try:
                action()
            except (Exception, SystemExit) as error:
                self.assertNotIn(CANARY, str(error))
            else:
                self.fail("Expected an error")
        self.assertNotIn(CANARY, "\n".join(captured.output))

    def test_pdf_error_has_no_credentials_headers_body_or_url(self):
        api = KivraApiClient(CANARY, CANARY)
        response = Mock(status_code=403, text=CANARY, headers={"private": CANARY})
        api.session = Mock()
        api.session.get.return_value = response
        api.session.request.return_value = response
        self.assert_redacted(lambda: api.get_pdf("https://example.invalid/" + CANARY))

    def test_graphql_error_has_no_server_message_or_variables(self):
        api = KivraApiClient(CANARY, CANARY)
        for status in (200, 403):
            with self.subTest(status=status):
                response = Mock(status_code=status, text=CANARY,
                                json=lambda: {"errors": [{"message": CANARY}]})
                api.session = Mock()
                api.session.post.return_value = response
                api.session.request.return_value = response
                self.assert_redacted(lambda: api.graphql_query("Test", "query {}", {"key": CANARY}))

    def test_content_errors_have_no_private_data(self):
        api = KivraApiClient(CANARY, CANARY)
        response = Mock(status_code=403, text=CANARY)
        api.session = Mock()
        api.session.get.return_value = response
        api.session.request.return_value = response
        self.assert_redacted(lambda: api.get_content_details(CANARY))
        self.assert_redacted(lambda: api.get_content_file(CANARY, CANARY))

    def test_auth_errors_have_no_server_data(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = KivraAuth(directory, Mock())
            auth.session = Mock()
            auth.session.post.return_value = Mock(status_code=403, text=CANARY)
            self.assert_redacted(lambda: auth._initialize_oauth2("synthetic"))
            auth.session.get.return_value = Mock(
                json=lambda: {"status": CANARY, "private": CANARY}
            )
            with patch("kivra.auth.time.sleep"):
                self.assert_redacted(lambda: auth._poll_for_auth("/poll", "code", "verifier"))


if __name__ == "__main__":
    unittest.main()
