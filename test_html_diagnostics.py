"""Synthetic tests for offline HTML diagnostics and private output."""

import base64
import contextlib
import io
import json
import logging
import os
import unittest
from unittest.mock import patch

from kivra_sync import main
from storage.html_diagnostics import diagnose_export, diagnose_source, run_diagnostics
from test_downloads import PDF, SyntheticExportCase
from utils.html_diagnostics import HtmlDiagnostics
from utils.html_locations import SourceLocations
from utils.pdf import default_url_fetcher, html_to_pdf


CANARY = "SYNTHETIC_PRIVATE_CANARY"
PRIVATE_URL = f"https://user:{CANARY}@{CANARY}.invalid/{CANARY}?token={CANARY}#{CANARY}"


class HtmlDiagnosticTests(unittest.TestCase):
    def test_network_and_file_requests_are_distinct_without_access(self):
        source = f'<p>{CANARY}</p>\n<img src="{PRIVATE_URL}">\n<img src="file:///{CANARY}">'
        with patch("utils.pdf.default_url_fetcher") as fetch:
            issues = diagnose_source(source)
        fetch.assert_not_called()
        self.assertEqual([issue["code"] for issue in issues], ["blocked_network", "blocked_file"])
        self.assertEqual(issues[0]["locations"], [{"line": 2, "column": 1, "kind": "src"}])
        self.assertEqual(issues[1]["locations"], [{"line": 3, "column": 1, "kind": "src"}])
        self.assertNotIn(CANARY, json.dumps(issues))
        self.assertNotIn("https", json.dumps(issues))

    def test_other_schemes_do_not_expose_scheme_names(self):
        scheme = CANARY.replace("_", "-")
        with patch("utils.pdf.default_url_fetcher") as fetch:
            issues = diagnose_source(f'<img src="{scheme}:private">')
        fetch.assert_not_called()
        self.assertEqual(issues[0]["code"], "blocked_scheme")
        self.assertNotIn(scheme, json.dumps(issues))

    def test_embedded_fetch_error_has_a_safe_exception_type(self):
        with patch("utils.pdf.default_url_fetcher", side_effect=ValueError(CANARY)):
            issues = diagnose_source(f'<img src="data:image/png,{CANARY}">')
        self.assertEqual(issues[0]["code"], "embedded_fetch_error")
        self.assertEqual(issues[0]["exception"], "ValueError")
        self.assertNotIn(CANARY, json.dumps(issues))

    def test_invalid_base64_and_invalid_image_have_distinct_errors(self):
        issues = diagnose_source('<img src="data:image/png;base64,invalid">')
        self.assertEqual(issues[0]["code"], "embedded_fetch_error")
        self.assertEqual(issues[0]["exception"], "binascii.Error")
        issues = diagnose_source('<img src="data:image/png,not-an-image">')
        self.assertEqual(issues[0]["code"], "image_load_error")

    def test_embedded_svg_cannot_load_external_resources(self):
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><image href="{PRIVATE_URL}" width="10" height="10"/></svg>'
        encoded = base64.b64encode(svg.encode()).decode()
        with patch("utils.pdf.default_url_fetcher", wraps=default_url_fetcher) as fetch:
            issues = diagnose_source(f'<img src="data:image/svg+xml;base64,{encoded}">')
        self.assertTrue(all(call.args[0].startswith("data:") for call in fetch.call_args_list))
        self.assertIn("blocked_network", [issue["code"] for issue in issues])
        self.assertNotIn(CANARY, json.dumps(issues))
        self.assertEqual(issues[-1]["locations"], [])

    def test_relative_url_without_base_is_not_a_blocked_request(self):
        with patch("utils.pdf.default_url_fetcher") as fetch:
            issues = diagnose_source(f'<img src="{CANARY}.png">')
        fetch.assert_not_called()
        self.assertEqual(issues[0]["code"], "relative_url_without_base")
        self.assertEqual(issues[0]["locations"][0]["line"], 1)
        self.assertNotIn(CANARY, json.dumps(issues))

    def test_base_url_and_html_entities_resolve_to_source_tags(self):
        source = '<base href="https://example.invalid/">\n<img\n src="image?a=1&amp;b=2">'
        issues = diagnose_source(source)
        self.assertEqual(issues[0]["code"], "blocked_network")
        self.assertEqual(issues[0]["locations"], [{"line": 2, "column": 1, "kind": "src"}])

    def test_repeated_resource_lists_all_matching_locations(self):
        issues = diagnose_source(f'<img src="{PRIVATE_URL}">\n<img src="{PRIVATE_URL}">')
        self.assertEqual(len(issues), 1)
        self.assertEqual([location["line"] for location in issues[0]["locations"]], [1, 2])

    def test_css_import_and_background_locations(self):
        source = ('<style>\n@import "https://example.invalid/style";\n'
                  'p { background: url("https://example.invalid/image") }\n</style><p>Text</p>')
        issues = diagnose_source(source)
        self.assertEqual([issue["code"] for issue in issues], ["blocked_network", "blocked_network"])
        self.assertEqual([issue["locations"][0]["line"] for issue in issues], [2, 3])
        self.assertTrue(all(issue["locations"][0]["kind"] == "css" for issue in issues))

    def test_css_escapes_and_attribute_entities_have_locations(self):
        source = ('<style>p{background:url(https://example.invalid/ima\\67 e)}</style>\n'
                  '<p style="background:url(&quot;https://example.invalid/other&quot;)">Text</p>')
        locations = SourceLocations(source)
        self.assertEqual(locations.find("https://example.invalid/image")[0]["kind"], "css")
        self.assertEqual(locations.find("https://example.invalid/other"),
                         [{"line": 2, "column": 1, "kind": "style_attribute"}])

    def test_unicode_urls_have_locations_after_renderer_encoding(self):
        issues = diagnose_source('<img src="https://example.invalid/räksmörgås">')
        self.assertEqual(issues[0]["locations"][0]["line"], 1)

    def test_plain_html_and_hyperlinks_have_no_resource_errors(self):
        self.assertEqual(diagnose_source(f'<p>{CANARY}</p><a href="{PRIVATE_URL}">Link</a>'), [])

    def test_conversion_exception_uses_allowlisted_type_not_text_or_class_name(self):
        secret_exception = type(CANARY, (Exception,), {})
        for error, name in ((RuntimeError(CANARY), "RuntimeError"), (secret_exception(CANARY), "Exception")):
            with patch("utils.pdf.HTML", side_effect=error):
                issues = diagnose_source("<p>Text</p>")
            self.assertEqual(issues, [{"code": "html_conversion", "exception": name}])
            self.assertNotIn(CANARY, json.dumps(issues))

    def test_unrecognized_renderer_error_does_not_format_arguments(self):
        class PrivateArgument:
            def __str__(self):
                raise AssertionError("Do not format private data")
        diagnostics = HtmlDiagnostics("<p>Text</p>")
        logger = logging.getLogger("weasyprint")
        previous = list(logger.handlers)
        with diagnostics.capture():
            logger.error("%s", PrivateArgument())
        self.assertEqual(logger.handlers, previous)
        self.assertEqual(diagnostics.issues, [{"code": "renderer_error"}])

    def test_diagnostic_handler_is_removed_after_conversion_exception(self):
        logger = logging.getLogger("weasyprint")
        previous = list(logger.handlers)
        with patch("utils.pdf.HTML", side_effect=RuntimeError(CANARY)):
            diagnose_source("<p>Text</p>")
        self.assertEqual(logger.handlers, previous)

    def test_normal_conversion_does_not_add_diagnostic_output(self):
        with patch("utils.pdf.HTML") as renderer:
            renderer.return_value.write_pdf.side_effect = lambda output: output.write(PDF)
            self.assertEqual(html_to_pdf("<p>Text</p>", raise_errors=True), PDF)


class ExportHtmlDiagnosticTests(SyntheticExportCase):
    def prepare(self, parts=None):
        self.store.convert_html = False
        self.api.get_content_details.return_value = {"parts": parts or [
            {"content_type": "text/html", "body": f'<img src="{PRIVATE_URL}">'},
        ]}
        with contextlib.redirect_stdout(io.StringIO()):
            stats = self.fetch()
        self.store.report_run({"version": 1, "categories": {"letters": True, "receipts": False}, **stats})

    def snapshot(self):
        return {path.relative_to(self.root): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def test_report_uses_item_and_part_numbers_without_private_data_or_writes(self):
        self.prepare([
            {"content_type": "application/pdf", "key": "synthetic"},
            {"content_type": "text/html", "body": f'<img src="{PRIVATE_URL}">'},
            {"content_type": "text/html", "body": "<p>Text</p>"},
        ])
        before = self.snapshot()
        report = diagnose_export(self.store)
        self.assertEqual(report["html_sources"], 2)
        self.assertEqual(report["sources_with_errors"], 1)
        self.assertEqual(report["read_errors"], 0)
        self.assertEqual(report["results"][0]["item"], 1)
        self.assertEqual(report["results"][0]["part"], 1)
        self.assertNotIn(CANARY, json.dumps(report))
        self.assertNotIn("Synthetic Sender", json.dumps(report))
        self.assertEqual(self.snapshot(), before)

    def test_saved_source_not_metadata_body_controls_diagnostics(self):
        self.prepare()
        next(self.root.rglob("*.html")).write_text("<p>Text</p>")
        self.assertEqual(diagnose_export(self.store)["sources_with_errors"], 0)

    def test_missing_source_does_not_stop_later_parts(self):
        self.prepare([
            {"content_type": "text/html", "body": "<p>Text</p>"},
            {"content_type": "text/html", "body": f'<img src="{PRIVATE_URL}">'},
        ])
        next(self.root.rglob("*_part0_html.html")).unlink()
        report = diagnose_export(self.store)
        self.assertEqual(report["read_errors"], 1)
        self.assertEqual(report["sources_with_errors"], 1)
        self.assertEqual(len(report["results"]), 2)

    def test_symlink_outside_export_does_not_read_target(self):
        self.prepare()
        source = next(self.root.rglob("*.html"))
        source.unlink()
        source.symlink_to(self.root.parent / f"{CANARY}.html")
        with patch("pathlib.Path.read_text", wraps=None) as read:
            from storage.html_diagnostics import read_source
            with self.assertRaises(ValueError):
                read_source(self.store, source)
            read.assert_not_called()

    def test_missing_or_invalid_report_returns_safe_error(self):
        for content in (None, CANARY, "[]", '{"version":1}'):
            if content is not None:
                (self.root / "export-report.json").write_text(content)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(run_diagnostics(self.store), 1)
            report = json.loads(output.getvalue())
            self.assertEqual(report["results"][0]["issues"][0]["code"], "report_read_error")
            self.assertNotIn(CANARY, output.getvalue())

    def test_missing_metadata_returns_safe_error(self):
        self.prepare()
        self.store._paths(self.letter.get_metadata())[0].unlink()
        report = diagnose_export(self.store)
        self.assertEqual(report["read_errors"], 1)
        self.assertEqual(report["results"][0]["issues"][0]["code"], "metadata_read_error")

    def test_receipt_only_report_has_no_html(self):
        self.store.report_run({"version": 1, "categories": {"letters": False}})
        report = diagnose_export(self.store)
        self.assertEqual(report["html_sources"], 0)
        self.assertEqual(report["read_errors"], 0)

    def invoke_cli(self, *args):
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)
        output = io.StringIO()
        with patch("sys.argv", ["kivra-sync", *args]), patch("kivra_sync.logging.basicConfig"), patch(
            "kivra_sync.KivraAuth"
        ) as auth, patch("kivra_sync.TerminalInteractionProvider") as terminal, patch(
            "kivra_sync.fetch_documents"
        ) as fetch, contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                main()
            auth.assert_not_called()
            terminal.assert_not_called()
            fetch.assert_not_called()
        return result.exception.code, output.getvalue()

    def test_cli_uses_no_authentication_and_returns_nonzero_for_errors(self):
        self.prepare()
        code, output = self.invoke_cli("--diagnose-html", "--base-dir", str(self.root.parent), self.root.name)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["sources_with_errors"], 1)
        self.assertNotIn(CANARY, output)
        next(self.root.rglob("*.html")).write_text("<p>Text</p>")
        code, _ = self.invoke_cli("--diagnose-html", "--base-dir", str(self.root.parent), self.root.name)
        self.assertEqual(code, 0)

    def test_cli_rejects_paperless_and_conflicting_offline_modes(self):
        for args in (("--storage-provider", "paperless"), ("--verify-only",)):
            code, _ = self.invoke_cli("--diagnose-html", *args)
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
