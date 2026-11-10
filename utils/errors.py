"""Report errors without response data, paths, or exception messages."""

import errno

from requests import RequestException, Timeout


MESSAGES = {
    "http_error": "Kivra HTTP error",
    "network_error": "Kivra request failure",
    "request_timeout": "Kivra request timeout",
    "invalid_json": "Invalid Kivra JSON response",
    "graphql_error": "Kivra GraphQL error",
    "pdf_length": "PDF response is not complete",
    "pdf_header": "Invalid PDF header",
    "pdf_eof": "Missing PDF end marker",
    "missing_text": "Missing text content",
    "unsupported_type": "Unsupported content type",
    "html_resource": "HTML conversion failure: missing resources",
    "html_conversion": "HTML conversion failure",
    "storage_rejected": "Storage failure",
    "filesystem_error": "Filesystem error",
    "invalid_data": "Invalid data",
    "unexpected_error": "Unexpected error",
}


class ApiError(RuntimeError):
    def __init__(self, code, status=None):
        self.code = code if code in MESSAGES else "unexpected_error"
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        message = MESSAGES[self.code]
        if self.code == "http_error" and self.status is not None:
            message = f"Kivra HTTP status: {self.status}"
        super().__init__(message)


class ContentError(ValueError):
    def __init__(self, code):
        self.code = code if code in MESSAGES else "unexpected_error"
        super().__init__(MESSAGES[self.code])


def error_details(error):
    if isinstance(error, (ApiError, ContentError)) and error.code in MESSAGES:
        details = {"detail": error.code}
        if isinstance(error, ApiError) and error.code == "http_error":
            if type(error.status) is int and 100 <= error.status <= 599:
                details["http_status"] = error.status
        return details
    if isinstance(error, Timeout):
        return {"detail": "request_timeout"}
    if isinstance(error, RequestException):
        return {"detail": "network_error"}
    if isinstance(error, OSError):
        details = {"detail": "filesystem_error"}
        if type(error.errno) is int and error.errno in errno.errorcode:
            details["errno"] = error.errno
        return details
    if isinstance(error, (ValueError, TypeError, KeyError)):
        return {"detail": "invalid_data"}
    return {"detail": "unexpected_error"}


def issue_description(issue):
    description = issue["reason"]
    detail = issue.get("detail")
    if detail not in MESSAGES:
        return description
    fields = [detail]
    status = issue.get("http_status")
    if detail == "http_error" and type(status) is int and 100 <= status <= 599:
        fields.append(f"status={status}")
    number = issue.get("errno")
    if detail == "filesystem_error" and type(number) is int and number in errno.errorcode:
        fields.append(f"errno={number}")
    return f"{description} [{' '.join(fields)}]"
