"""Synthetic tests for receipt attachments, recovery, and private errors."""

import errno
import json
import logging
import unittest
from unittest.mock import Mock, patch

from kivra.api import KivraApiClient
from storage.filesystem import FileSystemStoreProvider
from test_downloads import PDF, RECEIPT, SyntheticExportCase, receipt_page
from utils.atomic import atomic_write
from utils.errors import ApiError


class AttachmentApiTests(unittest.TestCase):
    def setUp(self):
        self.api = KivraApiClient("SYNTHETIC_TOKEN", "SYNTHETIC_ACTOR")
        self.addCleanup(self.api.session.close)

    def test_request_matches_public_web_client_and_quotes_attachment_id(self):
        self.api.session.request = Mock(return_value=Mock(
            status_code=200, content=PDF, headers={},
        ))
        self.assertEqual(self.api.get_receipt_attachment("synthetic/id ?"), PDF)
        self.api.session.request.assert_called_once_with(
            "GET", "https://app.api.kivra.com/v1/attachments/synthetic%2Fid%20%3F",
            timeout=(15, 120), allow_redirects=True,
            headers={"Authorization": "token SYNTHETIC_TOKEN", "Accept": "application/pdf"},
        )

    def test_invalid_attachment_id_never_sends_a_request(self):
        self.api.session.request = Mock()
        for key in (None, "", " ", 1, [], ".", ".."):
            with self.subTest(key=key), self.assertRaises(ApiError):
                self.api.get_receipt_attachment(key)
        self.api.session.request.assert_not_called()

    def test_attachment_response_must_be_a_complete_pdf(self):
        for body, headers in ((b"<html>Error</html>", {}),
                              (b"%PDF-1.4\ntruncated", {}),
                              (PDF, {"Content-Length": "1"})):
            self.api.session.request = Mock(return_value=Mock(
                status_code=200, content=body, headers=headers,
            ))
            with self.assertRaises((ValueError, ApiError)):
                self.api.get_receipt_attachment("synthetic")

    def test_http_errors_do_not_include_private_values(self):
        canary = "SYNTHETIC_PRIVATE_CANARY"
        self.api.session.request = Mock(return_value=Mock(status_code=403, text=canary))
        with self.assertRaises(ApiError) as caught:
            self.api.get_receipt_attachment(canary)
        self.assertEqual(caught.exception.status, 403)
        self.assertNotIn(canary, str(caught.exception))
        self.assertNotIn("SYNTHETIC_TOKEN", str(caught.exception))


class ReceiptAttachmentTests(SyntheticExportCase):
    def setUp(self):
        super().setUp()
        self.attachments = [
            {"id": "synthetic-first", "type": "prescription_specification"},
            {"id": "synthetic-second", "type": "prescription_specification"},
        ]
        self.api.graphql_query.side_effect = self.attachment_query

    def attachment_query(self, operation, query, variables):
        if operation == "Receipts":
            return receipt_page([{**RECEIPT, "attachments": self.attachments}])
        return self.query(operation, query, variables)

    def export(self):
        stats = self.fetch("receipts")
        return stats, stats["receipts_results"][0]

    def test_all_pdfs_and_references_are_saved_before_completion(self):
        stats, result = self.export()
        self.assertEqual(stats["receipts_stored"], 1)
        self.assertEqual(stats["receipts_unsupported"], 0)
        self.assertTrue(self.store.is_complete(result["metadata"]))
        paths = list(self.root.rglob("*.pdf"))
        self.assertEqual(len(paths), 3)
        self.assertEqual(len(set(paths)), 3)
        self.assertTrue(all(path.read_bytes() == PDF for path in paths))
        saved = json.loads(self.store._paths(result["metadata"])[0].read_text())
        self.assertEqual(saved["attachments"], self.attachments)
        manifest = json.loads(self.store._manifest_path(result["metadata"]).read_text())
        self.assertEqual(len(manifest["files"]), 4)
        self.assertEqual(self.export()[0]["receipts_skipped"], 1)
        self.assertEqual(self.api.get_receipt_attachment.call_count, 2)
        self.api.get_pdf.assert_called_once()

    def test_primary_only_manifest_cannot_skip_new_attachments(self):
        self.attachments = []
        self.export()
        self.attachments = [{"id": "synthetic-new", "type": "synthetic"}]
        stats, result = self.export()
        self.assertEqual(stats["receipts_stored"], 1)
        self.assertTrue(self.store.is_complete(result["metadata"]))
        self.api.get_receipt_attachment.assert_called_once_with("synthetic-new")
        self.assertEqual(len(list(self.root.rglob("*.pdf"))), 2)

    def test_attachment_failure_keeps_primary_and_attempts_later_attachments(self):
        self.api.get_receipt_attachment.side_effect = [ApiError("http_error", 403), PDF]
        stats, result = self.export()
        self.assertEqual(stats["receipts_failed"], 1)
        self.assertEqual(result["issues"], [
            {"reason": "attachment_error", "part": 0, "detail": "http_error", "http_status": 403},
        ])
        self.assertEqual(len(list(self.root.rglob("*.pdf"))), 2)
        self.assertFalse(self.store.is_complete(result["metadata"]))
        self.assertEqual(self.api.get_receipt_attachment.call_count, 2)
        self.api.get_receipt_attachment.side_effect = None
        self.assertEqual(self.export()[0]["receipts_stored"], 1)
        self.assertTrue(self.store.is_complete(result["metadata"]))

    def test_primary_failure_does_not_stop_attachment_downloads(self):
        self.api.get_pdf.side_effect = ApiError("http_error", 503)
        stats, result = self.export()
        self.assertEqual(stats["receipts_failed"], 1)
        self.assertEqual(self.api.get_receipt_attachment.call_count, 2)
        self.assertEqual(len(list(self.root.rglob("*.pdf"))), 2)
        self.assertFalse(self.store.is_complete(result["metadata"]))

    def test_attachment_write_failure_keeps_other_pdfs_and_reports_errno(self):
        def write(path, data):
            if path.name.endswith("_part0.pdf"):
                raise OSError(errno.ENOSPC, "SYNTHETIC_PRIVATE_CANARY", str(path))
            return atomic_write(path, data)
        with patch("storage.filesystem.atomic_write", side_effect=write):
            with self.assertLogs(level=logging.ERROR) as logs:
                stats, result = self.export()
        self.assertEqual(stats["receipts_failed"], 1)
        self.assertEqual(result["issues"][0]["errno"], errno.ENOSPC)
        self.assertEqual(len(list(self.root.rglob("*.pdf"))), 2)
        self.assertFalse(self.store.is_complete(result["metadata"]))
        self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", "\n".join(logs.output))
        self.assertNotIn(str(self.root), "\n".join(logs.output))

    def test_storage_false_prevents_completion_but_keeps_other_parts(self):
        store = self.store.store
        with patch.object(self.store, "store", side_effect=lambda data, metadata: (
            False if metadata.get("part_index") == 0 else store(data, metadata)
        )):
            stats, result = self.export()
        self.assertEqual(stats["receipts_failed"], 1)
        self.assertEqual(result["issues"][0]["detail"], "storage_rejected")
        self.assertEqual(len(list(self.root.rglob("*.pdf"))), 2)
        self.assertFalse(self.store.is_complete(result["metadata"]))

    def test_changed_attachment_reference_invalidates_completion(self):
        self.export()
        self.attachments[0] = {"id": "synthetic-new", "type": "synthetic"}
        stats, result = self.export()
        self.assertEqual(stats["receipts_stored"], 1)
        self.assertEqual(self.api.get_receipt_attachment.call_count, 4)
        self.assertTrue(self.store.is_complete(result["metadata"]))

    def test_deleted_and_changed_attachments_trigger_download(self):
        _, result = self.export()
        path = next(self.root.rglob("*_part0.pdf"))
        path.unlink()
        self.assertFalse(self.store.is_complete(result["metadata"]))
        self.assertEqual(self.export()[0]["receipts_stored"], 1)
        path.write_bytes(b"changed")
        self.assertFalse(self.store.is_complete(result["metadata"]))
        self.assertEqual(self.export()[0]["receipts_stored"], 1)
        self.assertEqual(path.read_bytes(), PDF)

    def test_invalid_references_and_duplicates_never_establish_completion(self):
        for attachments in ([{}], [None], [{"id": ""}], [{"id": "  "}],
                            [{"id": 1}], "invalid", {}, False,
                            [{"id": "same"}, {"id": "same"}]):
            with self.subTest(attachments=attachments):
                self.attachments = attachments
                stats, result = self.export()
                self.assertEqual(stats["receipts_failed"], 1)
                self.assertFalse(self.store.is_complete(result["metadata"]))
        self.assertTrue(self.store._paths(self.receipt.get_metadata("application/pdf"))[0].is_file())

    def test_missing_id_does_not_stop_later_valid_attachment(self):
        self.attachments = [{}, {"id": "synthetic-valid"}]
        stats, _ = self.export()
        self.assertEqual(stats["receipts_failed"], 1)
        self.api.get_receipt_attachment.assert_called_once_with("synthetic-valid")
        self.assertTrue(list(self.root.rglob("*_part1.pdf")))

    def test_invalid_attachment_pdf_keeps_receipt_but_not_completion(self):
        self.api.get_receipt_attachment.return_value = b"<html>Error</html>"
        stats, result = self.export()
        self.assertEqual(stats["receipts_failed"], 1)
        self.assertEqual(len(list(self.root.rglob("*.pdf"))), 1)
        self.assertFalse(self.store.is_complete(result["metadata"]))

    def test_no_convert_downloads_attachment_pdfs_without_rendering(self):
        self.store = FileSystemStoreProvider(self.root, convert_html=False)
        with patch("storage.filesystem.html_to_pdf") as render:
            self.assertEqual(self.export()[0]["receipts_stored"], 1)
            render.assert_not_called()
        self.assertEqual(self.api.get_receipt_attachment.call_count, 2)
        self.assertEqual(len(list(self.root.rglob("*.pdf"))), 3)

    def test_error_logs_do_not_disclose_attachment_ids_or_messages(self):
        canary = "SYNTHETIC_PRIVATE_CANARY"
        self.attachments = [{"id": canary}]
        self.api.get_receipt_attachment.side_effect = RuntimeError(canary)
        with self.assertLogs(level=logging.ERROR) as logs:
            self.export()
        self.assertNotIn(canary, "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
