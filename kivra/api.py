#!/usr/bin/python3
# -*- coding: utf-8 -*-

from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils.content import validate_content
from utils.errors import ApiError


class KivraApiClient:
    """Read Kivra data without credentials in errors."""

    def __init__(self, access_token, actor_key):
        self.access_token = access_token
        self.actor_key = actor_key
        self.graphql_url = "https://bff.kivra.com/graphql"
        self.session = requests.Session()
        # These endpoints only read data. GraphQL POST queries also read data.
        retries = Retry(total=3, backoff_factor=0.5, backoff_max=10,
                        respect_retry_after_header=False, raise_on_status=False,
                        status_forcelist=(429, 500, 502, 503, 504),
                        allowed_methods=("GET", "POST"))
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def get_headers(self):
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://inbox.kivra.com",
            "Referer": "https://inbox.kivra.com/",
            "Authorization": f"Bearer {self.access_token}",
            "X-Actor-Key": self.actor_key,
            "X-Actor-Type": "user",
            "X-Session-Actor": f"user_{self.actor_key}",
            "X-Kivra-Environment": "production",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
            "Accept-Language": "sv",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

    def _request(self, method, url, **kwargs):
        try:
            response = self.session.request(
                method, url, timeout=(15, 120), allow_redirects=True, **kwargs
            )
        except requests.Timeout:
            raise ApiError("request_timeout") from None
        except requests.RequestException:
            raise ApiError("network_error") from None
        if response.status_code != 200:
            raise ApiError("http_error", response.status_code)
        return response

    @staticmethod
    def _json(response):
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError
            return data
        except ValueError:
            raise ApiError("invalid_json") from None

    def graphql_query(self, operation_name, query, variables):
        response = self._request("POST", self.graphql_url, headers=self.get_headers(),
                                 json={"operationName": operation_name,
                                       "query": query, "variables": variables})
        data = self._json(response)
        if data.get("errors"):
            raise ApiError("graphql_error")
        return data

    def _get(self, url, accept):
        return self._request("GET", url, headers={
            "Authorization": f"token {self.access_token}", "Accept": accept
        })

    @staticmethod
    def _pdf(response):
        data = response.content
        # Requests decodes compressed responses, so compare only unencoded sizes.
        length = response.headers.get("Content-Length")
        if length is not None and not response.headers.get("Content-Encoding"):
            try:
                if int(length) != len(data):
                    raise ValueError
            except ValueError:
                raise ApiError("pdf_length") from None
        validate_content(data, "application/pdf")
        return data

    def get_pdf(self, url):
        return self._pdf(self._get(url, "application/pdf"))

    def get_receipt_attachment(self, attachment_id):
        if (not isinstance(attachment_id, str) or not attachment_id.strip()
                or attachment_id in (".", "..")):
            raise ApiError("invalid_data")
        # Kivra web client 1.319.1 uses this PDF endpoint for receipt attachments.
        url = f"https://app.api.kivra.com/v1/attachments/{quote(attachment_id, safe='')}"
        return self._pdf(self._get(url, "application/pdf"))

    def get_content_details(self, content_key):
        url = f"https://app.api.kivra.com/v1/content/{quote(content_key, safe='')}"
        return self._json(self._get(url, "application/json"))

    def get_content_file(self, content_key, file_key):
        url = (f"https://app.api.kivra.com/v1/content/{quote(content_key, safe='')}"
               f"/file/{quote(file_key, safe='')}/raw")
        # Keep the upstream Accept header. Validate the PDF bytes.
        return self._pdf(self._get(url, "*/*"))
