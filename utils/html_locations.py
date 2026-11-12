"""Find source locations without source text in reports."""

from html.parser import HTMLParser
from urllib.parse import urljoin

import tinycss2
from weasyprint.urls import iri_to_uri


def css_urls(tokens):
    previous = None
    for token in tokens:
        if token.type in ("whitespace", "comment"):
            continue
        if token.type == "url":
            yield token.value, token
        elif token.type == "function" and token.lower_name == "url":
            arguments = [item for item in token.arguments
                         if item.type not in ("whitespace", "comment")]
            if len(arguments) == 1 and arguments[0].type == "string":
                yield arguments[0].value, token
        elif (token.type == "string" and previous is not None
              and previous.type == "at-keyword" and previous.lower_value == "import"):
            yield token.value, token
        else:
            yield from css_urls(getattr(token, "content", getattr(token, "arguments", [])))
        previous = token


class SourceLocations(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.references = []
        self.in_style = False
        self.feed(source)
        self.close()

    def add(self, url, line, column, kind):
        self.references.append((url.strip(), {"line": line, "column": column, "kind": kind}))

    def handle_starttag(self, tag, attrs):
        line, column = self.getpos()
        self.in_style = tag == "style"
        for name, value in attrs:
            if not value:
                continue
            if name in ("src", "href", "data", "poster", "background"):
                self.add(value, line, column + 1, name)
            elif name == "style":
                for url, _ in css_urls(tinycss2.parse_component_value_list(value)):
                    # Entity decoding can change character offsets in attributes.
                    self.add(url, line, column + 1, "style_attribute")

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False

    def handle_data(self, data):
        if not self.in_style:
            return
        line, column = self.getpos()
        for url, token in css_urls(tinycss2.parse_component_value_list(data)):
            self.add(url, line + token.source_line - 1,
                     token.source_column + (column if token.source_line == 1 else 0), "css")

    def find(self, url, base_url=None):
        found = []
        for reference, location in self.references:
            try:
                resolved = iri_to_uri(urljoin(base_url or "", reference))
            except (ValueError, UnicodeError):
                continue
            if (reference == url or resolved == url) and location not in found:
                found.append(location)
        return found
