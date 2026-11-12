"""Synthetic tests for error codes without private data."""

import errno
import json
import logging
import unittest
from unittest.mock import Mock, patch

import requests

from kivra.api import KivraApiClient
from test_downloads import PDF, SyntheticExportCase
from utils.content import validate_content
from utils.errors import ApiError, ContentError, error_details, issue_description
from utils.pdf import html_to_pdf


CANARY = "SYNTHETIC_PRIVATE_CANARY"


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.api = KivraApiClient(CANARY, CANARY)
        self.addCleanup(self.api.session.close)

    def test_http_status_survives_without_response_data(self):
        for status in (403, 406, 429, 503):
            self.api.session.request = Mock(return_value=Mock(
                status_code=status, content=CANARY.encode(), headers={"private": CANARY}
            ))
            with self.assertRaises(ApiError) as caught:
                self.api.get_content_file(CANARY, CANARY)
            self.assertEqual(error_details(caught.exception), {
                "detail": "http_error", "http_status": status,
            })
            self.assertNotIn(CANARY, str(caught.exception))
        retries = self.api.session.get_adapter("https://").max_retries
        self.assertFalse(retries.raise_on_status)

    def test_network_errors_and_timeouts_have_distinct_codes(self):
        for error, code in ((requests.Timeout(CANARY), "request_timeout"),
                            (requests.ConnectionError(CANARY), "network_error")):
            self.assertEqual(error_details(error), {"detail": code})
            self.api.session.request = Mock(side_effect=error)
            with self.assertRaises(ApiError) as caught:
                self.api.get_content_file(CANARY, CANARY)
            self.assertEqual(error_details(caught.exception), {"detail": code})
            self.assertNotIn(CANARY, str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)

    def test_pdf_checks_have_distinct_codes(self):
        for data, headers, code in (
            (CANARY.encode(), {}, "pdf_header"),
            (b"%PDF-1.4\n" + CANARY.encode(), {}, "pdf_eof"),
            (PDF, {"Content-Length": "1"}, "pdf_length"),
        ):
            self.api.session.request = Mock(return_value=Mock(
                status_code=200, content=data, headers=headers,
            ))
            with self.assertRaises((ApiError, ContentError)) as caught:
                self.api.get_pdf("https://example.invalid/" + CANARY)
            self.assertEqual(error_details(caught.exception), {"detail": code})
            self.assertNotIn(CANARY, str(caught.exception))

    def test_content_validation_retains_value_error_contract(self):
        with self.assertRaises(ValueError) as caught:
            validate_content(b"", "application/pdf")
        self.assertEqual(error_details(caught.exception), {"detail": "pdf_header"})

    def test_json_and_graphql_errors_have_distinct_codes(self):
        response = Mock(status_code=200)
        self.api.session.request = Mock(return_value=response)
        response.json.side_effect = ValueError(CANARY)
        with self.assertRaises(ApiError) as caught:
            self.api.get_content_details(CANARY)
        self.assertEqual(error_details(caught.exception), {"detail": "invalid_json"})
        response.json.side_effect = None
        response.json.return_value = {"errors": [{"message": CANARY}]}
        with self.assertRaises(ApiError) as caught:
            self.api.graphql_query("Synthetic", "query {}", {})
        self.assertEqual(error_details(caught.exception), {"detail": "graphql_error"})

    def test_html_resources_and_renderer_errors_have_distinct_codes(self):
        with patch("utils.pdf.default_url_fetcher") as fetch:
            with self.assertRaises(ContentError) as caught:
                html_to_pdf(f'<img src="https://example.invalid/{CANARY}">', raise_errors=True)
            fetch.assert_not_called()
        self.assertEqual(error_details(caught.exception), {"detail": "html_resource"})
        with patch("utils.pdf.HTML", side_effect=RuntimeError(CANARY)):
            with self.assertRaises(ContentError) as caught:
                html_to_pdf("<p>Synthetic</p>", raise_errors=True)
        self.assertEqual(error_details(caught.exception), {"detail": "html_conversion"})
        self.assertNotIn(CANARY, str(caught.exception))

    def test_filesystem_error_keeps_only_errno(self):
        error = OSError(errno.ENOSPC, CANARY, "/private/" + CANARY)
        details = error_details(error)
        self.assertEqual(details, {"detail": "filesystem_error", "errno": errno.ENOSPC})
        self.assertEqual(issue_description({"reason": "content_error", **details}),
                         f"content_error [filesystem_error errno={errno.ENOSPC}]")
        self.assertEqual(error_details(OSError(CANARY)), {"detail": "filesystem_error"})

    def test_unknown_errors_and_values_cannot_disclose_messages(self):
        for error in (RuntimeError(CANARY), ApiError(CANARY), ContentError(CANARY)):
            self.assertEqual(error_details(error), {"detail": "unexpected_error"})
        for fields in ({"detail": CANARY}, {"detail": "http_error", "http_status": CANARY},
                       {"detail": "filesystem_error", "errno": CANARY}):
            self.assertNotIn(CANARY, issue_description({"reason": "content_error", **fields}))
        self.assertEqual(error_details(ValueError(CANARY)), {"detail": "invalid_data"})


class DiagnosticExportTests(SyntheticExportCase):
    def test_letter_status_reaches_report_and_console(self):
        self.api.get_content_file.side_effect = ApiError("http_error", 406)
        with self.assertLogs(level=logging.ERROR) as captured:
            stats = self.fetch()
        issue = stats["letters_results"][0]["issues"][0]
        self.assertEqual(issue, {"reason": "content_error", "part": 0,
                                 "detail": "http_error", "http_status": 406})
        self.assertIn("content_error [http_error status=406]", captured.output[0])
        self.store.report_run(stats)
        report = json.loads((self.root / "export-report.json").read_text())
        self.assertEqual(report["letters_results"][0]["issues"][0], issue)

    def test_receipt_status_reaches_report_and_console(self):
        self.api.get_pdf.side_effect = ApiError("http_error", 403)
        with self.assertLogs(level=logging.ERROR) as captured:
            stats = self.fetch("receipts")
        self.assertEqual(stats["receipts_results"][0]["issues"], [
            {"reason": "document_error", "detail": "http_error", "http_status": 403},
        ])
        self.assertIn("document_error [http_error status=403]", captured.output[0])

    def test_storage_errno_reaches_console_without_path_or_message(self):
        error = OSError(errno.EACCES, CANARY, CANARY)
        with patch.object(self.store, "store", side_effect=error):
            with self.assertLogs(level=logging.ERROR) as captured:
                stats = self.fetch()
        self.assertEqual(stats["letters_results"][0]["issues"][0]["errno"], errno.EACCES)
        self.assertNotIn(CANARY, json.dumps(stats) + "\n".join(captured.output))

    def test_html_conversion_failure_is_not_a_pdf_header_error(self):
        self.api.get_content_details.return_value = {
            "parts": [{"content_type": "text/html", "body": "<p>Synthetic</p>"}],
        }
        with patch("utils.pdf.HTML", side_effect=RuntimeError(CANARY)):
            stats = self.fetch()
        self.assertEqual(stats["letters_results"][0]["issues"][0]["detail"], "html_conversion")
        self.assertEqual(len(list(self.root.rglob("*.html"))), 1)
        self.assertFalse(self.store.is_complete(self.letter.get_metadata()))


if __name__ == "__main__":
    unittest.main()
