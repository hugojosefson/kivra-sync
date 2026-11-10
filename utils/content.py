"""Validate content before storage."""


def validate_content(data, content_type):
    if content_type == "application/pdf":
        if not isinstance(data, bytes) or not data.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF header")
        if not data.rstrip().endswith(b"%%EOF"):
            raise ValueError("Missing PDF end marker")
    elif content_type in ("text/plain", "text/html"):
        if not isinstance(data, str) or not data.strip():
            raise ValueError("Missing text content")
    else:
        raise ValueError("Unsupported content type")
