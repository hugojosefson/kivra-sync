"""Replay saved letter HTML without authentication or export writes."""

import json

from utils.errors import ContentError, error_details
from utils.html_diagnostics import HtmlDiagnostics
from utils.pdf import html_to_pdf


def read_source(store, path):
    path.resolve().relative_to(store.base_dir.resolve())
    if not path.is_file():
        raise OSError("Missing regular file")
    return path.read_text(encoding="utf-8")


def diagnose_source(source):
    diagnostics = HtmlDiagnostics(source)
    try:
        html_to_pdf(source, raise_errors=True, diagnostics=diagnostics)
    except ContentError:
        # The collector records resource and conversion errors before this exception.
        if not diagnostics.issues:
            diagnostics.issues.append({"code": "html_conversion"})
    return diagnostics.issues


def diagnose_export(store):
    summary = {"version": 1, "html_sources": 0, "sources_with_errors": 0,
               "read_errors": 0, "results": []}
    try:
        report = json.loads(read_source(store, store.base_dir / "export-report.json"))
        if report["version"] != 1 or type(report["categories"]["letters"]) is not bool:
            raise ValueError
        results = report.get("letters_results", []) if report["categories"]["letters"] else []
        if not isinstance(results, list) or (report["categories"]["letters"]
                                             and len(results) != report["letters_fetched"]):
            raise ValueError
    except Exception as error:
        summary["read_errors"] += 1
        summary["results"].append({"issues": [{"code": "report_read_error", **error_details(error)}]})
        return summary

    for item, result in enumerate(results, 1):
        try:
            metadata = result["metadata"]
            if metadata["type"] != "letter" or metadata["content_type"] != "application/json":
                raise ValueError
            content = json.loads(read_source(store, store._paths(metadata)[0]))
            parts = content["content"]["parts"]
            if not isinstance(parts, list) or not all(isinstance(part, dict) for part in parts):
                raise ValueError
        except Exception as error:
            summary["read_errors"] += 1
            summary["results"].append({"item": item, "issues": [
                {"code": "metadata_read_error", **error_details(error)}]})
            continue
        for index, part in enumerate(parts):
            if part.get("content_type") != "text/html":
                continue
            entry = {"item": item, "part": index}
            try:
                source_metadata = {**metadata, "content_type": "text/html",
                                   "part_index": index if len(parts) > 1 else None}
                source = read_source(store, store._paths(source_metadata)[0])
                summary["html_sources"] += 1
                entry["issues"] = diagnose_source(source)
                if entry["issues"]:
                    summary["sources_with_errors"] += 1
            except Exception as error:
                summary["read_errors"] += 1
                entry["issues"] = [{"code": "source_diagnostic_error", **error_details(error)}]
            if entry["issues"]:
                summary["results"].append(entry)
    return summary


def run_diagnostics(store):
    report = diagnose_export(store)
    print(json.dumps(report, indent=2))
    return int(bool(report["sources_with_errors"] or report["read_errors"]))
