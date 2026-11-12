"""Synthetic tests for atomic storage and verified completion markers."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kivra.models import KivraLetter
from storage.filesystem import FileSystemStoreProvider
from utils.atomic import atomic_write


PDF = b"%PDF-1.4\n% synthetic transport fixture\n%%EOF\n"


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = FileSystemStoreProvider(self.root)
        self.metadata = KivraLetter("synthetic", "2025-01-01", "Test").get_metadata()
        self.pdf_metadata = {**self.metadata, "content_type": "application/pdf"}

    def test_atomic_failure_preserves_final_file_and_removes_temporary_file(self):
        target = self.root / "file"
        target.write_bytes(b"old")
        for method in ("os.replace", "os.fsync"):
            with self.subTest(method=method), patch("utils.atomic." + method, side_effect=OSError("test")):
                with self.assertRaises(OSError):
                    atomic_write(target, b"new")
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(list(self.root.iterdir()), [target])

    def test_file_sync_still_works_without_directory_open_support(self):
        with patch("utils.atomic.os.O_DIRECTORY", None), patch("utils.atomic.os.fsync") as sync:
            atomic_write(self.root / "file", b"synthetic")
        sync.assert_called_once()

    def test_files_have_private_permissions(self):
        atomic_write(self.root / "file", b"synthetic")
        self.assertEqual((self.root / "file").stat().st_mode & 0o777, 0o600)

    def test_paperless_retains_its_existing_lookup(self):
        from storage.paperless import PaperlessNgxStoreProvider
        provider = PaperlessNgxStoreProvider.__new__(PaperlessNgxStoreProvider)
        provider.exists = Mock(return_value=True)
        self.assertTrue(provider.is_complete(self.metadata))
        provider.exists.assert_called_once_with(self.metadata)

    def test_legacy_metadata_does_not_establish_completion(self):
        self.store.report_metadata({}, self.metadata)
        self.store.store(PDF, self.pdf_metadata)
        self.assertFalse(self.store.exists(self.metadata))

    def test_manifest_requires_metadata_and_all_content_files(self):
        self.store.report_metadata({}, self.metadata)
        self.store.store(PDF, self.pdf_metadata)
        self.assertTrue(self.store.complete_document(self.metadata, [self.pdf_metadata]))
        self.assertTrue(self.store.is_complete(self.metadata))
        path = self.store._paths(self.pdf_metadata)[0]
        path.write_bytes(b"%PDF-truncated")
        self.assertFalse(self.store.is_complete(self.metadata))
        path.unlink()
        self.assertFalse(self.store.is_complete(self.metadata))

    def test_marker_is_not_written_when_a_required_file_is_missing(self):
        self.store.report_metadata({}, self.metadata)
        with self.assertRaises(OSError):
            self.store.complete_document(self.metadata, [self.pdf_metadata])
        self.assertFalse(list(self.root.rglob("*.complete.json")))

    def test_metadata_and_listing_use_atomic_replacement(self):
        with patch("utils.atomic.os.replace", side_effect=OSError("test")):
            with self.assertRaises(OSError):
                self.store.report_metadata({}, self.metadata)
            with self.assertRaises(OSError):
                self.store.report_listing("letters", [])
        self.assertFalse(list(self.root.rglob("*.json")))

    def test_invalid_pdf_is_not_written(self):
        for data in (b"", b"<html>Error</html>", b"%PDF-truncated"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.store.store(data, self.pdf_metadata)
        self.assertFalse(list(self.root.rglob("*.pdf")))

    def test_html_source_survives_conversion_failure(self):
        metadata = {**self.metadata, "content_type": "text/html"}
        with patch("storage.filesystem.html_to_pdf", return_value=None):
            with self.assertRaises(ValueError):
                self.store.store("<p>Synthetic text</p>", metadata)
        self.assertEqual(len(list(self.root.rglob("*.html"))), 1)
        self.assertFalse(self.store.is_complete(self.metadata))

    def test_dry_run_has_no_filesystem_effect(self):
        store = FileSystemStoreProvider(self.root / "dry", dry_run=True)
        store.report_listing("letters", [])
        store.report_metadata({}, self.metadata)
        store.store(PDF, self.pdf_metadata)
        store.complete_document(self.metadata, [self.pdf_metadata])
        store.report_run({"status": "dry_run"})
        self.assertFalse((self.root / "dry").exists())

    def test_manifest_cannot_reference_a_path_outside_the_export(self):
        self.store.report_metadata({}, self.metadata)
        self.store.store(PDF, self.pdf_metadata)
        self.store.complete_document(self.metadata, [self.pdf_metadata])
        path = self.store._manifest_path(self.metadata)
        manifest = json.loads(path.read_text())
        manifest["files"][1]["path"] = "../outside"
        path.write_text(json.dumps(manifest))
        self.assertFalse(self.store.is_complete(self.metadata))


if __name__ == "__main__":
    unittest.main()
