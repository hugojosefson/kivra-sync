"""Synthetic receipt pages include caps, duplicates, and empty responses."""

import unittest
from unittest.mock import Mock

from kivra.pagination import list_receipts


def page(keys, total, offset):
    return {"data": {"receiptsV2": {
        "list": [{"key": key} for key in keys],
        "total": total, "offset": offset, "limit": 100,
    }}}


class ReceiptPaginationTests(unittest.TestCase):
    def test_server_cap_does_not_end_pagination(self):
        api = Mock()
        api.graphql_query.side_effect = [
            page(["a", "b"], 5, 0), page(["c", "d"], 5, 2), page(["e"], 5, 4)
        ]
        items, total, errors = list_receipts(api)
        self.assertEqual(len(items), 5)
        self.assertEqual(total, 5)
        self.assertEqual(errors, [])
        self.assertEqual([call.args[2]["offset"] for call in api.graphql_query.call_args_list],
                         [0, 2, 4])

    def test_valid_empty_listing(self):
        api = Mock()
        api.graphql_query.return_value = page([], 0, 0)
        self.assertEqual(list_receipts(api), ([], 0, []))

    def test_partial_pages_report_failure(self):
        for last in (page([], 2, 1), page(["a"], 2, 1), page(["b"], 3, 1),
                     page(["b"], 2, 0), RuntimeError("synthetic"),
                     {"data": {"receiptsV2": None}}):
            with self.subTest(last=last):
                api = Mock()
                api.graphql_query.side_effect = [page(["a"], 2, 0), last]
                items, total, errors = list_receipts(api)
                self.assertEqual(items, [{"key": "a"}])
                self.assertEqual(total, 2)
                self.assertEqual(errors, ["incomplete_listing"])

    def test_malformed_first_page_is_not_an_empty_success(self):
        for response in ({}, {"errors": [{"message": "synthetic"}]},
                         {"data": {"receiptsV2": {}}}):
            with self.subTest(response=response):
                api = Mock()
                api.graphql_query.return_value = response
                self.assertTrue(list_receipts(api)[2])


if __name__ == "__main__":
    unittest.main()
