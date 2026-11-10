"""Download receipt attachments without stopping after one error."""

from utils.content import validate_content
from utils.errors import ContentError, error_details


def fetch_attachments(api_client, document_store, receipt, attachments):
    issues, saved_parts, seen = [], [], set()
    if not isinstance(attachments, list):
        return [{"reason": "missing_content"}], []
    for index, attachment in enumerate(attachments):
        key = attachment.get("id") if isinstance(attachment, dict) else None
        if not isinstance(key, str) or not key.strip():
            issues.append({"reason": "missing_content", "part": index})
            continue
        if key in seen:
            issues.append({"reason": "duplicate_attachment", "part": index})
            continue
        seen.add(key)
        metadata = {**receipt.get_metadata(content_type="application/pdf"),
                    "part_index": index, "attachment_id": key}
        try:
            data = api_client.get_receipt_attachment(key)
            validate_content(data, "application/pdf")
            if not document_store.store(data, metadata):
                raise ContentError("storage_rejected")
            saved_parts.append(metadata)
        except Exception as error:
            issues.append({"reason": "attachment_error", "part": index,
                           **error_details(error)})
    return issues, saved_parts
