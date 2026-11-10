#!/usr/bin/python3
# -*- coding: utf-8 -*-

import hashlib
import json
from pathlib import Path

from storage.base import DocumentStoreProvider
from utils.atomic import atomic_write
from utils.content import validate_content
from utils.errors import ContentError
from utils.helpers import clean_filename
from utils.pdf import html_to_pdf


class FileSystemStoreProvider(DocumentStoreProvider):
    """Store files with a SHA-256 manifest for each document."""

    def __init__(self, base_dir, dry_run=False):
        self.base_dir = Path(base_dir)
        self.dry_run = dry_run

    def _get_filepath(self, metadata):
        kind = metadata["type"]
        if kind not in ("letter", "receipt"):
            raise ValueError("Invalid document type")
        key, date = metadata["key"], metadata["date"]
        for value in (key, date):
            if not isinstance(value, str) or not value or value in (".", ".."):
                raise ValueError("Invalid filename component")
            if "/" in value or "\\" in value or "\x00" in value:
                raise ValueError("Invalid filename component")
        name = metadata["sender_name" if kind == "letter" else "store_name"]
        safe_name = clean_filename(name)[:80]
        if not safe_name or safe_name in (".", ".."):
            safe_name = "unknown"
        directory = self.base_dir / ("Letters" if kind == "letter" else "Receipts")
        filename = f"{date}_{safe_name}_{key}"
        part = metadata.get("part_index")
        if part is not None:
            if type(part) is not int or part < 0:
                raise ValueError("Invalid part index")
            filename += f"_part{part}"
        return directory / safe_name, directory / "json" / safe_name, filename

    def _paths(self, metadata):
        directory, json_directory, name = self._get_filepath(metadata)
        content_type = metadata["content_type"]
        if content_type == "application/json":
            return [json_directory / f"{name}.json"]
        suffixes = {
            "application/pdf": [".pdf"],
            "text/plain": [".txt"],
            "text/html": ["_html.html", "_html.pdf"],
        }
        return [directory / f"{name}{suffix}" for suffix in suffixes[content_type]]

    def _manifest_path(self, metadata):
        return self._paths(metadata)[0].with_suffix(".complete.json")

    def _write_json(self, path, data):
        if not self.dry_run:
            atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        return True

    def report_listing(self, doc_type, listing):
        directories = {"letters": "Letters", "receipts": "Receipts"}
        return self._write_json(
            self.base_dir / directories[doc_type] / "json" / f"{doc_type}.json", listing
        )

    def report_metadata(self, data, metadata):
        return self._write_json(self._paths(metadata)[0], data)

    def report_run(self, stats):
        return self._write_json(self.base_dir / "export-report.json", stats)

    def exists(self, metadata):
        # A JSON file does not prove that its document is complete.
        return self.is_complete(metadata)

    def is_complete(self, metadata):
        if metadata.get("content_type") != "application/json":
            return False
        try:
            manifest = json.loads(self._manifest_path(metadata).read_text(encoding="utf-8"))
            if manifest["version"] != 1 or manifest["metadata"] != metadata:
                return False
            files = manifest["files"]
            if len(files) < 2:
                return False
            metadata_path = self._paths(metadata)[0].relative_to(self.base_dir).as_posix()
            if metadata_path not in [entry["path"] for entry in files]:
                return False
            for entry in files:
                path = self.base_dir / entry["path"]
                path.resolve().relative_to(self.base_dir.resolve())
                data = path.read_bytes()
                if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    return False
            return True
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def complete_document(self, metadata, parts):
        if not parts:
            return False
        if self.dry_run:
            return True
        paths = self._paths(metadata)
        for part in parts:
            paths.extend(self._paths(part))
        files = []
        for path in paths:
            data = path.read_bytes()
            files.append({"path": path.relative_to(self.base_dir).as_posix(),
                          "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        return self._write_json(self._manifest_path(metadata), {
            "version": 1, "metadata": metadata, "files": files,
        })

    def store(self, data, metadata):
        content_type = metadata["content_type"]
        validate_content(data, content_type)
        paths = self._paths(metadata)
        if self.dry_run:
            return True
        if content_type == "application/pdf":
            atomic_write(paths[0], data)
        elif content_type == "text/plain":
            atomic_write(paths[0], data.encode("utf-8"))
        elif content_type == "text/html":
            # Keep the source if PDF conversion fails.
            atomic_write(paths[0], data.encode("utf-8"))
            pdf = html_to_pdf(data, raise_errors=True)
            if pdf is None:
                raise ContentError("html_conversion")
            validate_content(pdf, "application/pdf")
            atomic_write(paths[1], pdf)
        return True
