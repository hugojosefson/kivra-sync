"""Read receipt pages and report listing errors."""

from kivra.models import RECEIPTS_QUERY

def page_data(response, name):
    if not isinstance(response, dict) or response.get("errors"):
        raise ValueError("Invalid listing")
    page = response["data"][name]
    if not isinstance(page["list"], list):
        raise ValueError("Invalid listing")
    if type(page["total"]) is not int or page["total"] < 0:
        raise ValueError("Invalid total")
    return page


def list_receipts(api_client, page_size=100):
    receipts, seen, errors = [], set(), []
    total = None
    try:
        while True:
            offset = len(receipts)
            page = page_data(api_client.graphql_query(
                "Receipts", RECEIPTS_QUERY,
                {"limit": page_size, "offset": offset, "search": None}
            ), "receiptsV2")
            if page["offset"] != offset:
                raise ValueError("Invalid page offset")
            if total is not None and page["total"] != total:
                raise ValueError("Listing changed")
            total = page["total"]
            batch = page["list"]
            if not batch and offset < total:
                raise ValueError("Listing is not complete")
            for item in batch:
                key = item.get("key")
                if key and key in seen:
                    raise ValueError("Duplicate item")
                if key:
                    seen.add(key)
                receipts.append(item)
            if len(receipts) >= total:
                if len(receipts) != total:
                    raise ValueError("Invalid total")
                break
    except Exception:
        errors.append("incomplete_listing")
    return receipts, total or 0, errors
