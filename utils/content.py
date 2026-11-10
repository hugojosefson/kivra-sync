"""Validate content before storage."""

from utils.errors import ContentError


def validate_content(data, content_type):
    if content_type == "application/pdf":
        if not isinstance(data, bytes) or not data.startswith(b"%PDF-"):
            raise ContentError("pdf_header")
        if not data.rstrip().endswith(b"%%EOF"):
            raise ContentError("pdf_eof")
    elif content_type in ("text/plain", "text/html"):
        if not isinstance(data, str) or not data.strip():
            raise ContentError("missing_text")
    else:
        raise ContentError("unsupported_type")
