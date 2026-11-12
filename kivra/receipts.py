#!/usr/bin/python3
# -*- coding: utf-8 -*-

from kivra.attachments import fetch_attachments
from kivra.export import export_items, result
from kivra.models import RECEIPT_DETAILS_QUERY, KivraReceipt
from kivra.pagination import list_receipts
from utils.content import validate_content
from utils.errors import error_details
from utils.helpers import format_date


class ReceiptFetcher:
    PAGE_SIZE = 100

    def __init__(self, api_client, document_store):
        self.api_client = api_client
        self.document_store = document_store

    def fetch_receipts(self, max_count=None):
        receipts, total, errors = list_receipts(self.api_client, self.PAGE_SIZE)
        try:
            if self.document_store.report_listing(
                "receipts", {"total": total, "list": receipts}
            ) is False:
                errors.append("listing_storage_error")
        except Exception:
            errors.append("listing_storage_error")
        return export_items("receipts", receipts, total or 0, errors,
                            max_count, self._process_receipt)

    def _process_receipt(self, item):
        receipt = KivraReceipt(
            item["key"], format_date(item.get("purchaseDate")),
            (item.get("store") or {}).get("name") or "unknown_store"
        )
        metadata = receipt.get_metadata()
        attachments = item.get("attachments")
        if attachments is None:
            attachments = []
        if attachments != []:
            metadata["attachments"] = attachments
        if not receipt.key:
            return result(metadata, [{"reason": "missing_key"}])
        if self.document_store.is_complete(metadata):
            return result(metadata, [], skipped=True)
        issues, saved_parts = [], []
        try:
            response = self.api_client.graphql_query(
                "ReceiptDetails", RECEIPT_DETAILS_QUERY, {"key": receipt.key}
            )
            if response.get("errors"):
                raise ValueError("Invalid receipt details")
            details = response["data"]["receiptV2"]
            if not isinstance(details, dict) or details.get("key") != receipt.key:
                raise ValueError("Missing receipt details")
            if not self.document_store.report_metadata(
                {**details, "attachments": attachments}, metadata
            ):
                issues.append({"reason": "metadata_storage_error"})
            url = f"https://app.api.kivra.com/v1/user/{self.api_client.actor_key}/receipts/{receipt.key}"
            data = self.api_client.get_pdf(url)
            validate_content(data, "application/pdf")
            pdf_metadata = receipt.get_metadata(content_type="application/pdf")
            if not self.document_store.store(data, pdf_metadata):
                issues.append({"reason": "content_storage_error"})
            else:
                saved_parts.append(pdf_metadata)
        except Exception as error:
            issues.append({"reason": "document_error", **error_details(error)})
        attachment_issues, attachment_parts = fetch_attachments(
            self.api_client, self.document_store, receipt, attachments
        )
        issues.extend(attachment_issues)
        saved_parts.extend(attachment_parts)
        if issues:
            return result(metadata, issues)
        try:
            if not self.document_store.complete_document(metadata, saved_parts):
                issues.append({"reason": "completion_storage_error"})
        except Exception as error:
            issues.append({"reason": "completion_storage_error", **error_details(error)})
        return result(metadata, issues)
