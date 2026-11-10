"""Do local file verification without a Kivra connection."""

import json


def verify_export(store):
    try:
        report = json.loads((store.base_dir / "export-report.json").read_text(encoding="utf-8"))
        if report["version"] != 1 or report["status"] not in ("complete_selected_scope", "incomplete"):
            raise ValueError
        if not any(report["categories"].values()):
            raise ValueError
        checked, failed = 0, 0
        for kind in ("letters", "receipts"):
            if not report["categories"][kind]:
                continue
            results = report[f"{kind}_results"]
            if len(results) != report[f"{kind}_fetched"]:
                raise ValueError
            if len(results) > report[f"{kind}_total"]:
                raise ValueError
            for status in ("stored", "skipped", "failed"):
                if sum(item["status"] == status for item in results) != report[f"{kind}_{status}"]:
                    raise ValueError
            for item in results:
                checked += 1
                if (item["status"] not in ("stored", "skipped") or item["issues"] or not item["metadata"]
                        or not store.is_complete(item["metadata"])):
                    failed += 1
        listing_errors = sum(len(report.get(f"{kind}_listing_errors", []))
                             for kind in ("letters", "receipts"))
        failed_run = report["status"] != "complete_selected_scope"
        print(f"Local verification: {checked} documents, {failed} invalid or missing.")
        print(f"Listing errors: {listing_errors}.")
        if report["limited"]:
            print("The export does not include all document categories or listed items.")
        return 1 if failed or listing_errors or failed_run else 0
    except (OSError, ValueError, KeyError, TypeError):
        print("Local verification failure: no correct export report.")
        return 1
