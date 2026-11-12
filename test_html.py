"""HTML conversion must not record document data or read external resources."""

import logging
import unittest
from unittest.mock import patch

from utils.pdf import html_to_pdf


class HtmlTests(unittest.TestCase):
    def test_inline_html_produces_a_pdf(self):
        pdf = html_to_pdf("<p>Synthetic text</p>")
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))

    def test_font_logs_do_not_reach_console_during_conversion(self):
        font_logger = logging.getLogger("fontTools.subset")
        previous_level = font_logger.level
        self.addCleanup(font_logger.setLevel, previous_level)
        font_logger.setLevel(logging.DEBUG)
        with self.assertLogs(level=logging.INFO) as logs:
            font_logger.debug("SYNTHETIC_PRIVATE_CANARY")
            pdf = html_to_pdf("<p>Synthetic text</p>")
            logging.error("Synthetic application error")
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertEqual(logs.output, ["ERROR:root:Synthetic application error"])

    def test_external_resources_cannot_send_requests_or_read_files(self):
        with patch("utils.pdf.default_url_fetcher") as fetch:
            for url in ("file:///private-test-file", "https://example.invalid/private"):
                self.assertIsNone(html_to_pdf(f'<img src="{url}">'))
            fetch.assert_not_called()

    def test_invalid_embedded_resource_is_not_silently_omitted(self):
        with patch("utils.pdf.default_url_fetcher", side_effect=ValueError("synthetic")):
            self.assertIsNone(html_to_pdf('<img src="data:image/png;base64,invalid">'))

    def test_renderer_exception_does_not_expose_private_data(self):
        with patch("utils.pdf.HTML", side_effect=RuntimeError("SYNTHETIC_PRIVATE_CANARY")):
            with self.assertLogs(level="ERROR") as logs:
                self.assertIsNone(html_to_pdf("<p>Synthetic</p>"))
        self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
