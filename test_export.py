"""Synthetic tests for export status and offline verification."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from kivra_sync import fetch_documents
from storage.verify import verify_export
from storage.filesystem import FileSystemStoreProvider
from interaction.web import WebInteractionProvider
from interaction.summary import completion_message
from test_downloads import RECEIPT, SyntheticExportCase, receipt_page


class ExportTests(SyntheticExportCase):
    def run_export(self, **overrides):
        args = SimpleNamespace(ssn="personal", fetch_receipts=True, fetch_letters=True,
                               max_receipts=0, max_letters=0, dry_run=False)
        for key, value in overrides.items():
            setattr(args, key, value)
        interaction = Mock()
        with patch("kivra_sync.KivraAuth") as auth, patch(
            "kivra_sync.KivraApiClient", return_value=self.api
        ):
            auth.return_value.authenticate.return_value = {
                "access_token": "dummy", "actor_key": "synthetic-actor"
            }
            code = fetch_documents(args, interaction, self.store, str(self.root))
            if interaction.report_completion.call_args.args[0]["status"] != "failed":
                auth.return_value.authenticate.assert_called_once()
        return code, interaction.report_completion.call_args.args[0]

    def test_one_authentication_and_nonzero_exit_on_item_failure(self):
        self.api.get_pdf.side_effect = RuntimeError("private")
        code, stats = self.run_export()
        self.assertEqual(code, 1)
        self.assertEqual(stats["status"], "incomplete")
        self.assertEqual(stats["letters_stored"], 1)
        self.assertEqual(stats["receipts_failed"], 1)
        self.assertEqual(verify_export(self.store), 1)

    def test_success_report_and_offline_verification_detect_missing_file(self):
        code, stats = self.run_export()
        self.assertEqual(code, 0)
        self.assertEqual(stats["status"], "complete_selected_scope")
        self.assertEqual(verify_export(self.store), 0)
        next(self.root.rglob("*.pdf")).unlink()
        self.assertEqual(verify_export(self.store), 1)

    def test_source_only_report_verifies_without_repeating_cli_flag(self):
        self.store = FileSystemStoreProvider(self.root, convert_html=False)
        self.api.get_content_details.return_value = {
            "parts": [{"content_type": "text/html", "body": "<p>Synthetic</p>"}],
        }
        with patch("storage.filesystem.html_to_pdf") as render:
            code, stats = self.run_export()
            render.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIs(stats["convert_html"], False)
        self.assertIn("HTML conversion: disabled.", completion_message(stats))
        self.assertEqual(verify_export(FileSystemStoreProvider(self.root)), 0)
        next(self.root.rglob("*.html")).unlink()
        self.assertEqual(verify_export(FileSystemStoreProvider(self.root)), 1)

    def test_verification_uses_report_mode_not_current_store_mode(self):
        self.api.get_content_details.return_value = {
            "parts": [{"content_type": "text/html", "body": "<p>Synthetic</p>"}],
        }
        code, stats = self.run_export()
        self.assertEqual(code, 0)
        next(self.root.rglob("*_html.pdf")).unlink()
        self.assertEqual(verify_export(FileSystemStoreProvider(self.root, convert_html=False)), 1)
        del stats["convert_html"]
        self.store.report_run(stats)
        self.assertEqual(verify_export(FileSystemStoreProvider(self.root, convert_html=False)), 1)

    def test_verification_rejects_invalid_conversion_setting(self):
        _, stats = self.run_export()
        stats["convert_html"] = "false"
        self.store.report_run(stats)
        self.assertEqual(verify_export(self.store), 1)

    def test_attachment_failure_returns_nonzero_and_recovery_verifies_every_file(self):
        self.api.graphql_query.side_effect = lambda op, q, v: (
            receipt_page([{**RECEIPT, "attachments": [{"id": "synthetic"}]}])
            if op == "Receipts" else self.query(op, q, v)
        )
        self.api.get_receipt_attachment.side_effect = RuntimeError("synthetic")
        code, stats = self.run_export()
        self.assertEqual(code, 1)
        self.assertEqual(stats["receipts_failed"], 1)
        self.assertEqual(verify_export(self.store), 1)
        self.api.get_receipt_attachment.side_effect = None
        self.assertEqual(self.run_export()[0], 0)
        self.assertEqual(verify_export(self.store), 0)
        next(self.root.rglob("*_part0.pdf")).unlink()
        self.assertEqual(verify_export(self.store), 1)

    def test_report_failure_returns_nonzero(self):
        with patch.object(self.store, "report_run", return_value=False):
            code, stats = self.run_export()
        self.assertEqual(code, 1)
        self.assertEqual(stats["status"], "failed")

    def test_listing_failure_does_not_stop_other_category(self):
        def query(operation, query, variables):
            if operation == "Receipts":
                raise requests.ConnectionError("private")
            return self.query(operation, query, variables)
        self.api.graphql_query.side_effect = query
        code, stats = self.run_export()
        self.assertEqual(code, 1)
        self.assertEqual(stats["letters_stored"], 1)
        self.assertTrue(stats["receipts_listing_errors"])

    def test_dry_run_never_claims_stored_files(self):
        self.store = FileSystemStoreProvider(self.root / "dry", dry_run=True)
        code, stats = self.run_export(dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(stats["status"], "dry_run")
        self.assertEqual(stats["letters_stored"], 0)
        self.assertEqual(stats["receipts_stored"], 0)
        self.assertFalse((self.root / "dry").exists())

    def test_unsupported_content_returns_nonzero(self):
        self.api.get_content_details.return_value = {
            "parts": [{"content_type": "image/png", "key": "synthetic"}]
        }
        code, stats = self.run_export()
        self.assertEqual(code, 1)
        self.assertEqual(stats["letters_unsupported"], 1)
        self.assertIn("1 unsupported", completion_message(stats))

    def test_authentication_failure_reaches_completion_report(self):
        args = SimpleNamespace(ssn="personal", fetch_receipts=True, fetch_letters=True,
                               max_receipts=0, max_letters=0, dry_run=False)
        interaction = Mock()
        with patch("kivra_sync.KivraAuth") as auth:
            auth.return_value.authenticate.side_effect = RuntimeError("SYNTHETIC_PRIVATE_CANARY")
            with self.assertLogs(level="ERROR") as logs:
                code = fetch_documents(args, interaction, self.store, str(self.root))
        self.assertEqual(code, 1)
        self.assertEqual(interaction.report_completion.call_args.args[0]["status"], "failed")
        self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", "\n".join(logs.output))

    def test_web_reports_failure_without_private_result_data(self):
        provider = WebInteractionProvider()
        provider._send_sse_message = Mock()
        provider.report_completion({
            "status": "incomplete", "letters_failed": 1,
            "letters_results": [{"metadata": "SYNTHETIC_PRIVATE_CANARY"}],
        })
        message = provider._send_sse_message.call_args.args[0]
        self.assertEqual(message["status"], "error")
        self.assertNotIn("SYNTHETIC_PRIVATE_CANARY", str(message))
        self.assertIn("failures: 1", message["message"])

    def test_partial_selection_remains_explicit(self):
        code, stats = self.run_export(fetch_receipts=False)
        self.assertEqual(code, 0)
        self.assertTrue(stats["limited"])
        self.assertIn("does not include all", completion_message(stats))

    def test_no_report_or_interrupted_run_cannot_pass_verification(self):
        self.assertEqual(verify_export(self.store), 1)
        self.store.report_run({"status": "in_progress"})
        self.assertEqual(verify_export(self.store), 1)


if __name__ == "__main__":
    unittest.main()
