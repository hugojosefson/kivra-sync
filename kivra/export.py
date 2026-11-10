"""Count document results without printing personal data."""

import logging


def export_items(kind, items, total, listing_errors, max_count, process):
    selected = items if max_count is None else items[:max_count]
    stats = {
        f"{kind}_total": total,
        f"{kind}_fetched": len(selected),
        f"{kind}_stored": 0,
        f"{kind}_skipped": 0,
        f"{kind}_failed": 0,
        f"{kind}_unsupported": 0,
        f"{kind}_missing": 0,
        f"{kind}_listing_errors": listing_errors,
        f"{kind}_results": [],
    }
    for index, item in enumerate(selected):
        try:
            result = process(item)
        except Exception:
            # Exception messages can contain URLs, credentials, or document data.
            result = {"status": "failed", "metadata": None,
                      "issues": [{"reason": "document_error"}]}
        stats[f"{kind}_{result['status']}"] += 1
        for issue in result["issues"]:
            if issue["reason"] in ("unsupported_content", "unsupported_attachment"):
                stats[f"{kind}_unsupported"] += 1
            if issue["reason"] == "missing_content":
                stats[f"{kind}_missing"] += 1
        stats[f"{kind}_results"].append(result)
        if (index + 1) % 100 == 0 or index + 1 == len(selected):
            print(f"{kind}: {index + 1} of {len(selected)} processed.")
        if result["issues"]:
            logging.error("%s item %d: %s", kind, index + 1,
                          ", ".join(issue["reason"] for issue in result["issues"]))
    return stats


def result(metadata, issues, skipped=False):
    return {"metadata": metadata, "issues": issues,
            "status": "failed" if issues else ("skipped" if skipped else "stored")}
