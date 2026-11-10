"""Format counts without personal data."""


COVERAGE_NOTE = (
    "Coverage: letters use the inbox filter. "
    "Coverage of archived documents, uploads, and shared items is unverified."
)


def completion_message(stats):
    lines = [f"Export status: {stats.get('status', 'unverified')}."]
    for label, key in (("Receipts", "receipts"), ("Letters", "letters")):
        lines.append(
            f"{label}: {stats.get(key + '_fetched', 0)} selected of "
            f"{stats.get(key + '_total', 0)} listed. "
            f"{stats.get(key + '_stored', 0)} stored, "
            f"{stats.get(key + '_skipped', 0)} verified, "
            f"failures: {stats.get(key + '_failed', 0)}, "
            f"{stats.get(key + '_unsupported', 0)} unsupported, "
            f"{stats.get(key + '_missing', 0)} missing."
        )
        errors = stats.get(key + "_listing_errors", [])
        if errors:
            lines.append(f"{label}: {len(errors)} listing errors.")
    if stats.get("limited"):
        lines.append("The export does not include all document categories or listed items.")
    if stats.get("local_verification") is False:
        lines.append("Storage does not support local verification of all document parts.")
    if stats.get("dry_run"):
        lines.append("Dry run: no files stored.")
    lines.append(COVERAGE_NOTE)
    return "\n".join(lines)
