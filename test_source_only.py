"""Synthetic tests for source files without conversion."""

import contextlib
import io
import os
import unittest
from unittest.mock import patch

from kivra_sync import main
from storage.filesystem import FileSystemStoreProvider
from test_downloads import PDF, RECEIPT, SyntheticExportCase, receipt_page


HTML = '<p>Synthetic text</p><img src="https://example.invalid/image">'


class SourceOnlyTests(SyntheticExportCase):
    def setUp(self):
        super().setUp()
        self.store = FileSystemStoreProvider(self.root, convert_html=False)
        self.api.get_content_details.return_value = {
            "parts": [{"content_type": "text/html", "body": HTML}],
        }

    def test_source_bytes_and_rerun_without_renderer(self):
        self.api.get_content_details.return_value["parts"].extend([
            {"content_type": "text/plain", "body": "Synthetic text\n"},
            {"content_type": "application/pdf", "key": "synthetic"},
        ])
        with patch("storage.filesystem.html_to_pdf") as render:
            self.assertEqual(self.fetch()["letters_stored"], 1)
            self.assertEqual(self.fetch()["letters_skipped"], 1)
            render.assert_not_called()
        self.assertEqual(next(self.root.rglob("*.html")).read_bytes(), HTML.encode("utf-8"))
        self.assertEqual(next(self.root.rglob("*.txt")).read_bytes(), b"Synthetic text\n")
        self.assertEqual(next(self.root.rglob("*.pdf")).read_bytes(), PDF)
        self.assertEqual(len(list(self.root.rglob("*.pdf"))), 1)
        self.assertTrue(self.store.is_complete(self.letter.get_metadata()))

    def test_conversion_failure_can_recover_in_source_mode(self):
        self.store = FileSystemStoreProvider(self.root)
        self.assertEqual(self.fetch()["letters_failed"], 1)
        html_path = next(self.root.rglob("*.html"))
        self.assertEqual(html_path.read_bytes(), HTML.encode("utf-8"))
        self.store = FileSystemStoreProvider(self.root, convert_html=False)
        with patch("storage.filesystem.html_to_pdf") as render:
            self.assertEqual(self.fetch()["letters_stored"], 1)
            render.assert_not_called()
        self.assertTrue(self.store.is_complete(self.letter.get_metadata()))
        self.assertFalse(list(self.root.rglob("*.pdf")))

    def test_enabling_conversion_requires_verified_conversion_not_a_stale_pdf(self):
        self.fetch()
        html_path = next(self.root.rglob("*.html"))
        html_path.with_suffix(".pdf").write_bytes(PDF)
        self.store = FileSystemStoreProvider(self.root)
        self.assertFalse(self.store.is_complete(self.letter.get_metadata()))
        with patch("storage.filesystem.html_to_pdf", return_value=PDF) as render:
            self.assertEqual(self.fetch()["letters_stored"], 1)
            render.assert_called_once_with(HTML, raise_errors=True)
        self.assertTrue(self.store.is_complete(self.letter.get_metadata()))

    def test_source_mode_accepts_older_converted_manifest_without_deleting_pdf(self):
        self.store = FileSystemStoreProvider(self.root)
        with patch("storage.filesystem.html_to_pdf", return_value=PDF):
            self.fetch()
        pdf_path = next(self.root.rglob("*.pdf"))
        self.store = FileSystemStoreProvider(self.root, convert_html=False)
        with patch("storage.filesystem.html_to_pdf") as render:
            self.assertEqual(self.fetch()["letters_skipped"], 1)
            render.assert_not_called()
        self.assertEqual(pdf_path.read_bytes(), PDF)

    def test_source_mode_ignores_only_generated_pdfs(self):
        self.api.get_content_details.return_value["parts"].append(
            {"content_type": "application/pdf", "key": "synthetic"}
        )
        self.store = FileSystemStoreProvider(self.root)
        with patch("storage.filesystem.html_to_pdf", return_value=PDF):
            self.fetch()
        converted = next(self.root.rglob("*_html.pdf"))
        downloaded = next(path for path in self.root.rglob("*.pdf") if path != converted)
        converted.write_bytes(b"changed")
        self.assertFalse(self.store.is_complete(self.letter.get_metadata()))
        self.store = FileSystemStoreProvider(self.root, convert_html=False)
        self.assertTrue(self.store.is_complete(self.letter.get_metadata()))
        converted.unlink()
        self.assertTrue(self.store.is_complete(self.letter.get_metadata()))
        downloaded.write_bytes(b"changed")
        self.assertFalse(self.store.is_complete(self.letter.get_metadata()))

    def test_missing_or_changed_source_requires_download(self):
        self.fetch()
        html_path = next(self.root.rglob("*.html"))
        html_path.write_text("changed")
        self.assertFalse(self.store.is_complete(self.letter.get_metadata()))
        self.assertEqual(self.fetch()["letters_stored"], 1)
        html_path.unlink()
        self.assertFalse(self.store.is_complete(self.letter.get_metadata()))
        self.assertEqual(self.fetch()["letters_stored"], 1)

    def test_source_write_does_not_delete_an_existing_conversion(self):
        self.fetch()
        pdf_path = next(self.root.rglob("*.html")).with_suffix(".pdf")
        pdf_path.write_bytes(PDF)
        self.store._manifest_path(self.letter.get_metadata()).unlink()
        self.assertEqual(self.fetch()["letters_stored"], 1)
        self.assertEqual(pdf_path.read_bytes(), PDF)

    def test_source_mode_still_reports_pdf_errors(self):
        self.api.get_content_details.return_value["parts"].append(
            {"content_type": "application/pdf", "key": "synthetic"}
        )
        self.api.get_content_file.return_value = b"invalid"
        self.assertEqual(self.fetch()["letters_failed"], 1)
        self.assertFalse(self.store.is_complete(self.letter.get_metadata()))
        self.assertEqual(next(self.root.rglob("*.html")).read_bytes(), HTML.encode("utf-8"))

    def test_source_mode_still_reports_unsupported_parts_and_attachments(self):
        self.api.get_content_details.return_value["parts"].append(
            {"content_type": "image/png", "key": "synthetic"}
        )
        stats = self.fetch()
        self.assertEqual(stats["letters_failed"], 1)
        self.assertEqual(stats["letters_unsupported"], 1)
        self.api.graphql_query.side_effect = lambda op, q, v: (
            receipt_page([{**RECEIPT, "attachments": [{"id": "synthetic"}]}])
            if op == "Receipts" else self.query(op, q, v)
        )
        stats = self.fetch("receipts")
        self.assertEqual(stats["receipts_failed"], 1)
        self.assertEqual(stats["receipts_unsupported"], 1)

    def test_dry_run_does_not_write_or_convert(self):
        self.store = FileSystemStoreProvider(self.root / "dry", dry_run=True, convert_html=False)
        with patch("storage.filesystem.html_to_pdf") as render:
            self.fetch()
            render.assert_not_called()
        self.assertFalse(self.store.base_dir.exists())


class ConversionCliTests(unittest.TestCase):
    def invoke(self, *args):
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)
        with patch("kivra_sync.logging.basicConfig"), patch("sys.argv", ["kivra-sync", *args]), patch(
            "kivra_sync.TerminalInteractionProvider"
        ) as terminal, patch("kivra_sync.fetch_documents", return_value=0) as fetch:
            terminal.return_value.can_listen = False
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit:
                main()
        return exit.exception.code, fetch

    def test_cli_passes_conversion_setting_to_filesystem(self):
        for args, expected in (((), True), (("--no-convert",), False)):
            code, fetch = self.invoke(*args)
            self.assertEqual(code, 0)
            self.assertEqual(fetch.call_args.args[2].convert_html, expected)

    def test_cli_rejects_no_convert_for_paperless_before_authentication(self):
        code, fetch = self.invoke("--no-convert", "--storage-provider", "paperless")
        self.assertEqual(code, 2)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
