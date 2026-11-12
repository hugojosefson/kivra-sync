"""Collect fixed error codes and source locations, not URLs or messages."""

import binascii
from contextlib import contextmanager
import logging
from urllib.parse import urlsplit

from utils.html_locations import SourceLocations


def exception_name(error):
    for kind, name in (
        (binascii.Error, "binascii.Error"), (UnicodeError, "UnicodeError"),
        (ValueError, "ValueError"), (TypeError, "TypeError"),
        (OSError, "OSError"), (RuntimeError, "RuntimeError"),
        (KeyError, "KeyError"), (MemoryError, "MemoryError"),
    ):
        if isinstance(error, kind):
            return name
    return "Exception"


class HtmlDiagnostics(logging.Handler):
    def __init__(self, source):
        super().__init__(level=logging.ERROR)
        self.locations = SourceLocations(source)
        self.base_url = None
        self.issues = []
        self.resources = {}

    def resource(self, url, code, error=None):
        if url in self.resources:
            return
        number = len(self.resources) + 1
        self.resources[url] = number
        if code == "blocked_resource":
            try:
                scheme = urlsplit(url).scheme.lower()
            except ValueError:
                scheme = ""
            code = {"http": "blocked_network", "https": "blocked_network",
                    "file": "blocked_file"}.get(scheme, "blocked_scheme")
        issue = {"code": code, "resource": number,
                 "locations": self.locations.find(url, self.base_url)}
        if error is not None:
            issue["exception"] = exception_name(error)
        self.issues.append(issue)

    def conversion(self, error):
        self.issues.append({"code": "html_conversion", "exception": exception_name(error)})

    def emit(self, record):
        # Do not format renderer messages: arguments can contain credentials.
        resource_messages = {
            "Failed to load image at %r: %s": "image_load_error",
            "Failed to load stylesheet at %s: %s": "stylesheet_load_error",
            "Failed to load stylesheet at %s : %s": "stylesheet_load_error",
            "Failed to render SVG image %s": "svg_render_error",
        }
        code = resource_messages.get(record.msg)
        if code and isinstance(record.args, tuple) and isinstance(record.args[0], str):
            self.resource(record.args[0], code)
        elif record.msg == 'Relative URI reference without a base URI: <%s %s="%s">':
            self.resource(record.args[2], "relative_url_without_base")
        else:
            self.issues.append({"code": "renderer_error"})

    @contextmanager
    def capture(self):
        logger = logging.getLogger("weasyprint")
        logger.addHandler(self)
        try:
            yield
        finally:
            logger.removeHandler(self)
            self.close()
