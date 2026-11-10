"""Synthetic regression tests for export recovery and local verification."""

import contextlib
import io
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from kivra.letters import LetterFetcher
from kivra.models import KivraLetter, KivraReceipt
from kivra.receipts import ReceiptFetcher
from storage.filesystem import FileSystemStoreProvider
from utils.atomic import atomic_write


PDF = b"%PDF-1.4\n% synthetic transport fixture\n%%EOF\n"
LETTER = {"key": "letter-test", "receivedAt": "2025-01-01",
          "sender": {"name": "Synthetic Sender"}}
RECEIPT = {"key": "receipt-test", "purchaseDate": "2025-01-01",
           "store": {"name": "Synthetic Store"}, "attachments": []}


def letter_page(items, total=None, more=False):
    return {"data": {"contents": {"total": len(items) if total is None else total,
                                 "list": items, "existsMore": more}}}


def receipt_page(items, total=None, offset=0):
    return {"data": {"receiptsV2": {
        "total": len(items) if total is None else total,
        "list": items, "offset": offset, "limit": 100,
    }}}


class SyntheticExportCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = FileSystemStoreProvider(self.root)
        self.api = Mock(actor_key="synthetic-actor")
        self.api.get_content_file.return_value = PDF
        self.api.get_pdf.return_value = PDF
        self.api.get_content_details.return_value = {
            "parts": [{"content_type": "application/pdf", "key": "file-test"}]
        }
        self.letter = KivraLetter("letter-test", "2025-01-01", "Synthetic Sender")
        self.receipt = KivraReceipt("receipt-test", "2025-01-01", "Synthetic Store")
        self.api.graphql_query.side_effect = self.query

    @staticmethod
    def query(operation, query, variables):
        if operation == "ContentList":
            return letter_page([LETTER])
        if operation == "Receipts":
            return receipt_page([RECEIPT])
        return {"data": {"receiptV2": {"key": variables["key"]}}}

    def fetch(self, kind="letters", **kwargs):
        fetcher = LetterFetcher if kind == "letters" else ReceiptFetcher
        return getattr(fetcher(self.api, self.store), "fetch_" + kind)(**kwargs)


class DownloadTests(SyntheticExportCase):
    def test_legacy_json_does_not_skip_missing_content(self):
        for kind, document in (("letters", self.letter), ("receipts", self.receipt)):
            with self.subTest(kind=kind):
                self.store.report_metadata({"legacy": True}, document.get_metadata())
                stats = self.fetch(kind)
                self.assertEqual(stats[kind + "_stored"], 1)
                self.assertTrue(self.store.is_complete(document.get_metadata()))

    def test_download_failure_then_rerun_recovers_both_categories(self):
        for kind, method, document in (
            ("letters", self.api.get_content_file, self.letter),
            ("receipts", self.api.get_pdf, self.receipt),
        ):
            with self.subTest(kind=kind):
                method.side_effect = [requests.ConnectionError("synthetic-private-url"), PDF]
                first = self.fetch(kind)
                self.assertEqual(first[kind + "_failed"], 1)
                self.assertFalse(self.store.is_complete(document.get_metadata()))
                second = self.fetch(kind)
                self.assertEqual(second[kind + "_stored"], 1)
                self.assertTrue(self.store.is_complete(document.get_metadata()))
                third = self.fetch(kind)
                self.assertEqual(third[kind + "_skipped"], 1)
                self.assertEqual(method.call_count, 2)

    def test_truncated_legacy_pdf_is_replaced(self):
        metadata = self.letter.get_metadata()
        self.store.report_metadata({}, metadata)
        path = self.store._paths(self.letter.get_metadata("application/pdf"))[0]
        atomic_write(path, b"%PDF-truncated")
        self.assertEqual(self.fetch()["letters_stored"], 1)
        self.assertEqual(path.read_bytes(), PDF)

    def test_deleted_or_changed_files_trigger_download(self):
        self.fetch()
        path = self.store._paths(self.letter.get_metadata("application/pdf"))[0]
        path.unlink()
        self.assertEqual(self.fetch()["letters_stored"], 1)
        path.write_bytes(PDF.replace(b"synthetic", b"corrupted"))
        self.assertEqual(self.fetch()["letters_stored"], 1)
        metadata_path = self.store._paths(self.letter.get_metadata())[0]
        metadata_path.write_text("{}")
        self.assertEqual(self.fetch()["letters_stored"], 1)

    def test_all_parts_required_and_later_parts_continue_after_failure(self):
        self.api.get_content_details.return_value = {"parts": [
            {"content_type": "application/pdf", "key": "first"},
            {"content_type": "application/pdf", "key": "second"},
            {"content_type": "text/plain", "body": "Synthetic text"},
        ]}
        self.api.get_content_file.side_effect = [RuntimeError("private"), PDF]
        stats = self.fetch()
        self.assertEqual(stats["letters_failed"], 1)
        self.assertEqual(stats["letters_stored"], 0)
        self.assertEqual(self.api.get_content_file.call_count, 2)
        self.assertEqual(len(list(self.root.rglob("*.txt"))), 1)
        self.api.get_content_file.side_effect = None
        self.assertEqual(self.fetch()["letters_stored"], 1)
        self.assertEqual(len(list(self.root.rglob("*_part*.pdf"))), 2)

    def test_unsupported_part_does_not_establish_completion(self):
        self.api.get_content_details.return_value["parts"].append(
            {"content_type": "image/png", "key": "unsupported"}
        )
        stats = self.fetch()
        self.assertEqual(stats["letters_unsupported"], 1)
        self.assertEqual(stats["letters_failed"], 1)
        self.assertFalse(self.store.is_complete(self.letter.get_metadata()))
        self.assertEqual(len(list(self.root.rglob("*.pdf"))), 1)

    def test_missing_parts_and_missing_body_are_failures(self):
        for parts in ([], [{"content_type": "text/plain"}],
                      [{"content_type": "application/pdf"}]):
            with self.subTest(parts=parts):
                self.api.get_content_details.return_value = {"parts": parts}
                stats = self.fetch()
                self.assertEqual(stats["letters_missing"], 1)
                self.assertEqual(stats["letters_failed"], 1)

    def test_receipt_attachments_are_reported(self):
        def query(operation, query, variables):
            if operation == "Receipts":
                return receipt_page([{**RECEIPT, "attachments": [{"id": "test", "type": "test"}]}])
            return self.query(operation, query, variables)
        self.api.graphql_query.side_effect = query
        stats = self.fetch("receipts")
        self.assertEqual(stats["receipts_failed"], 1)
        self.assertEqual(stats["receipts_unsupported"], 1)
        self.assertEqual(len(list(self.root.rglob("*.pdf"))), 1)

    def test_storage_false_and_metadata_false_are_failures(self):
        for method in ("store", "report_metadata", "complete_document"):
            with self.subTest(method=method), patch.object(self.store, method, return_value=False):
                self.assertEqual(self.fetch()["letters_failed"], 1)
                self.assertFalse(self.store.is_complete(self.letter.get_metadata()))

    def test_empty_and_invalid_pdf_do_not_establish_completion(self):
        for data in (b"", b"<html>Error</html>", b"%PDF-1.4\ntruncated"):
            with self.subTest(data=data):
                self.api.get_pdf.return_value = data
                self.assertEqual(self.fetch("receipts")["receipts_failed"], 1)
                self.assertFalse(self.store.is_complete(self.receipt.get_metadata()))

    def test_html_source_and_pdf_are_both_required(self):
        self.api.get_content_details.return_value = {
            "parts": [{"content_type": "text/html", "body": "<p>Synthetic text</p>"}]
        }
        self.assertEqual(self.fetch()["letters_stored"], 1)
        html_path = next(self.root.rglob("*.html"))
        html_path.unlink()
        self.assertFalse(self.store.is_complete(self.letter.get_metadata()))
        self.assertEqual(self.fetch()["letters_stored"], 1)

    def test_html_failure_preserves_source_without_completion(self):
        self.api.get_content_details.return_value = {
            "parts": [{"content_type": "text/html", "body": "<p>Synthetic text</p>"}]
        }
        with patch("storage.filesystem.html_to_pdf", return_value=None):
            self.assertEqual(self.fetch()["letters_failed"], 1)
        self.assertEqual(len(list(self.root.rglob("*.html"))), 1)
        self.assertFalse(self.store.is_complete(self.letter.get_metadata()))

    def test_receipts_paginate_even_when_server_caps_page_size(self):
        items = [{**RECEIPT, "key": f"receipt-{n}"} for n in range(5)]
        offsets = []
        def query(operation, query, variables):
            if operation != "Receipts":
                return self.query(operation, query, variables)
            offset = variables["offset"]
            offsets.append(offset)
            return receipt_page(items[offset:offset + 2], total=5, offset=offset)
        self.api.graphql_query.side_effect = query
        stats = self.fetch("receipts")
        self.assertEqual(offsets, [0, 2, 4])
        self.assertEqual(stats["receipts_stored"], 5)
        self.assertEqual(stats["receipts_listing_errors"], [])
        listing = json.loads((self.root / "Receipts/json/receipts.json").read_text())
        self.assertEqual(len(listing["list"]), 5)

    def test_receipt_pagination_fails_closed(self):
        for pages in (
            [receipt_page([RECEIPT], total=2), receipt_page([], total=2, offset=1)],
            [receipt_page([RECEIPT], total=2), receipt_page([RECEIPT], total=2, offset=1)],
            [receipt_page([RECEIPT], total=2), receipt_page([], total=3, offset=1)],
            [receipt_page([RECEIPT], total=2), receipt_page([], total=2, offset=0)],
            [{"data": {"receiptsV2": {}}}],
            [{"data": {"receiptsV2": None}}],
            [{"errors": [{"message": "private"}]}],
        ):
            with self.subTest(pages=pages):
                iterator = iter(pages)
                self.api.graphql_query.side_effect = lambda op, q, v: (
                    next(iterator) if op == "Receipts" else self.query(op, q, v)
                )
                self.assertTrue(self.fetch("receipts")["receipts_listing_errors"])

    def test_letters_paginate_with_known_filter_only(self):
        self.api.graphql_query.side_effect = [
            letter_page([LETTER], 2, True),
            letter_page([{**LETTER, "key": "letter-other"}], 2),
        ]
        stats = self.fetch()
        self.assertEqual(stats["letters_stored"], 2)
        calls = self.api.graphql_query.call_args_list
        self.assertEqual(calls[1].args[2]["after"], LETTER["key"])
        self.assertEqual([c.args[2]["filter"] for c in calls], ["inbox", "inbox"])

    def test_letter_pagination_fails_closed(self):
        for pages in (
            [letter_page([LETTER], 2, True), letter_page([LETTER], 2, True)],
            [letter_page([LETTER], 2, True), letter_page([], 2, True)],
            [letter_page([LETTER], 2)],
            [letter_page([LETTER], 2, True), letter_page([], 3)],
            [{"data": {"contents": {"list": []}}}],
        ):
            with self.subTest(pages=pages):
                self.api.graphql_query.side_effect = pages
                self.assertTrue(self.fetch()["letters_listing_errors"])

    def test_missing_key_does_not_break_counters_or_next_item(self):
        self.api.graphql_query.side_effect = [letter_page([{}, LETTER])]
        stats = self.fetch()
        self.assertEqual(stats["letters_failed"], 1)
        self.assertEqual(stats["letters_stored"], 1)

    def test_listing_storage_failure_is_reported(self):
        with patch.object(self.store, "report_listing", side_effect=OSError("private path")):
            self.assertEqual(self.fetch()["letters_listing_errors"], ["listing_storage_error"])

    def test_max_count_is_explicit_in_counts(self):
        self.api.graphql_query.side_effect = [
            letter_page([LETTER, {**LETTER, "key": "letter-other"}])
        ]
        stats = self.fetch(max_count=1)
        self.assertEqual(stats["letters_total"], 2)
        self.assertEqual(stats["letters_fetched"], 1)

    def test_path_components_cannot_escape_export_directory(self):
        for key in ("../escape", "/escape", "..", "test/other"):
            metadata = {**self.letter.get_metadata(), "key": key}
            with self.assertRaises(ValueError):
                self.store.report_metadata({}, metadata)

    def test_private_errors_do_not_reach_logs(self):
        secret = "SYNTHETIC_PRIVATE_CANARY"
        self.api.get_content_file.side_effect = RuntimeError(secret)
        output = io.StringIO()
        with self.assertLogs(level=logging.ERROR) as logs, contextlib.redirect_stdout(output):
            self.fetch()
        self.assertNotIn(secret, "\n".join(logs.output) + output.getvalue())



if __name__ == "__main__":
    unittest.main()
