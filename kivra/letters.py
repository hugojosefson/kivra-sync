#!/usr/bin/python3
# -*- coding: utf-8 -*-

from kivra.export import export_items, result
from kivra.pagination import page_data
from kivra.models import LETTERS_QUERY, KivraLetter
from utils.content import validate_content
from utils.errors import ContentError, error_details
from utils.helpers import format_date


class LetterFetcher:
    def __init__(self, api_client, document_store):
        self.api_client = api_client
        self.document_store = document_store

    def fetch_letters(self, max_count=None):
        letters, seen, errors = [], set(), []
        after, total = None, None
        try:
            while True:
                page = page_data(self.api_client.graphql_query(
                    "ContentList", LETTERS_QUERY,
                    {"after": after, "filter": "inbox", "senderKey": None, "take": 100}
                ), "contents")
                if total is not None and page["total"] != total:
                    raise ValueError("Listing changed")
                total = page["total"]
                if type(page["existsMore"]) is not bool:
                    raise ValueError("Missing pagination state")
                batch = page["list"]
                for item in batch:
                    key = item.get("key")
                    if key and key in seen:
                        raise ValueError("Duplicate item")
                    if key:
                        seen.add(key)
                    letters.append(item)
                if not page["existsMore"]:
                    if len(letters) != total:
                        raise ValueError("Listing is not complete")
                    break
                if len(letters) >= total or not batch or not batch[-1].get("key"):
                    raise ValueError("Pagination did not advance")
                after = batch[-1]["key"]
        except Exception:
            errors.append("incomplete_listing")
        try:
            if self.document_store.report_listing("letters", letters) is False:
                errors.append("listing_storage_error")
        except Exception:
            errors.append("listing_storage_error")
        return export_items("letters", letters, total or 0, errors,
                            max_count, self._process_letter)

    def _process_letter(self, item):
        letter = KivraLetter(
            item["key"], format_date(item.get("receivedAt")),
            (item.get("sender") or {}).get("name") or "unknown_sender"
        )
        metadata = letter.get_metadata()
        if not letter.key:
            return result(metadata, [{"reason": "missing_key"}])
        if self.document_store.is_complete(metadata):
            return result(metadata, [], skipped=True)
        issues, saved_parts = [], []
        try:
            content = self.api_client.get_content_details(letter.key)
            if not self.document_store.report_metadata({**item, "content": content}, metadata):
                issues.append({"reason": "metadata_storage_error"})
            parts = content.get("parts")
            if not isinstance(parts, list) or not parts:
                return result(metadata, issues + [{"reason": "missing_content"}])
            for index, part in enumerate(parts):
                part_metadata = KivraLetter(
                    letter.key, letter.date, letter.sender_name,
                    part_index=index if len(parts) > 1 else None
                ).get_metadata(content_type=part.get("content_type"))
                content_type = part.get("content_type")
                if content_type not in ("application/pdf", "text/plain", "text/html"):
                    issues.append({"reason": "unsupported_content", "part": index})
                    continue
                try:
                    if content_type == "application/pdf":
                        if not part.get("key"):
                            issues.append({"reason": "missing_content", "part": index})
                            continue
                        data = self.api_client.get_content_file(letter.key, part["key"])
                    else:
                        data = part.get("body")
                        if not isinstance(data, str) or not data.strip():
                            issues.append({"reason": "missing_content", "part": index})
                            continue
                    validate_content(data, content_type)
                    if not self.document_store.store(data, part_metadata):
                        raise ContentError("storage_rejected")
                    saved_parts.append(part_metadata)
                except Exception as error:
                    issues.append({"reason": "content_error", "part": index,
                                   **error_details(error)})
            if not issues and not self.document_store.complete_document(metadata, saved_parts):
                issues.append({"reason": "completion_storage_error"})
        except Exception as error:
            issues.append({"reason": "document_error", **error_details(error)})
        return result(metadata, issues)
