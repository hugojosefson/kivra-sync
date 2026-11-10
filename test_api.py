"""Synthetic tests for PDF response validation."""

import unittest
from unittest.mock import Mock

import requests

from kivra.api import KivraApiClient

PDF = b"%PDF-1.4\n% synthetic transport fixture\n%%EOF\n"


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.api = KivraApiClient("SYNTHETIC_TOKEN", "SYNTHETIC_ACTOR")
        self.addCleanup(self.api.session.close)

    def test_http_error_redacts_headers_body_and_url(self):
        response = Mock(status_code=403, text="SYNTHETIC_BODY",
                        headers={"Authorization": "SYNTHETIC_HEADER"})
        self.api.session.request = Mock(return_value=response)
        with self.assertRaisesRegex(RuntimeError, "^Kivra HTTP status: 403$"):
            self.api.get_pdf("https://example.invalid/SYNTHETIC_PRIVATE_URL")

    def test_connection_error_redacts_exception(self):
        self.api.session.request = Mock(side_effect=requests.ConnectionError("SYNTHETIC_TOKEN"))
        with self.assertRaisesRegex(RuntimeError, "^Kivra request failure$"):
            self.api.get_pdf("https://example.invalid")

    def test_graphql_errors_do_not_include_response_data(self):
        self.api.session.request = Mock(return_value=Mock(
            status_code=200, json=lambda: {"errors": [{"message": "SYNTHETIC_TOKEN"}]}
        ))
        with self.assertRaisesRegex(RuntimeError, "^Kivra GraphQL error$"):
            self.api.graphql_query("test", "query test {}", {})

    def test_pdf_validation_rejects_wrong_length_and_incomplete_content(self):
        for data, headers in ((PDF, {"Content-Length": "999"}),
                              (PDF, {"Content-Length": "invalid"}),
                              (b"<html>Error</html>", {}),
                              (b"%PDF-truncated", {})):
            with self.subTest(data=data):
                response = Mock(status_code=200, content=data, headers=headers)
                self.api.session.request = Mock(return_value=response)
                with self.assertRaises((ValueError, RuntimeError)):
                    self.api.get_pdf("https://example.invalid")

    def test_content_files_accept_raw_responses_and_still_validate_pdf(self):
        def request(method, url, **kwargs):
            status = 200 if kwargs["headers"]["Accept"] == "*/*" else 406
            return Mock(status_code=status, content=PDF,
                        headers={"Content-Type": "application/octet-stream"})
        self.api.session.request = Mock(side_effect=request)
        self.assertEqual(self.api.get_content_file("synthetic", "file"), PDF)
        self.api.session.request.return_value = Mock(
            status_code=200, content=b"<html>Error</html>", headers={}
        )
        self.api.session.request.side_effect = None
        with self.assertRaises(ValueError):
            self.api.get_content_file("synthetic", "file")

    def test_receipt_pdf_requests_retain_the_pdf_accept_header(self):
        self.api.session.request = Mock(return_value=Mock(
            status_code=200, content=PDF, headers={}
        ))
        self.api.get_pdf("https://example.invalid/receipt")
        self.assertEqual(self.api.session.request.call_args.kwargs["headers"]["Accept"],
                         "application/pdf")

    def test_requests_have_bounded_timeouts_and_follow_redirects(self):
        response = Mock(status_code=200, content=PDF, headers={})
        self.api.session.request = Mock(return_value=response)
        self.assertEqual(self.api.get_pdf("https://example.invalid"), PDF)
        kwargs = self.api.session.request.call_args.kwargs
        self.assertEqual(kwargs["timeout"], (15, 120))
        self.assertTrue(kwargs["allow_redirects"])


if __name__ == "__main__":
    unittest.main()
