"""Tests for AWS SigV4 authentication strategy."""

import pytest
from unittest.mock import MagicMock

from equinox.auth.aws_sigv4 import AWSSigV4Auth


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_request(url="https://s3.amazonaws.com/bucket/key", method="GET", body=None):
    req = MagicMock()
    req.url = url
    req.method = method
    req.body = body
    return req


# ── apply() — header injection ────────────────────────────────────────────────

class TestApply:
    def test_authorization_header_present(self):
        auth = AWSSigV4Auth("AKID", "secret", "us-east-1", "s3")
        headers = {}
        auth.apply(_make_request(), headers)
        assert "Authorization" in headers

    def test_authorization_starts_with_aws4(self):
        auth = AWSSigV4Auth("AKID", "secret", "us-east-1", "s3")
        headers = {}
        auth.apply(_make_request(), headers)
        assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 ")

    def test_x_amz_date_present(self):
        auth = AWSSigV4Auth("AKID", "secret", "us-east-1", "s3")
        headers = {}
        auth.apply(_make_request(), headers)
        assert "x-amz-date" in headers

    def test_x_amz_date_format(self):
        auth = AWSSigV4Auth("AKID", "secret", "us-east-1", "s3")
        headers = {}
        auth.apply(_make_request(), headers)
        date_val = headers["x-amz-date"]
        # Format: YYYYMMDDTHHMMSSz
        assert len(date_val) == 16
        assert date_val.endswith("Z")

    def test_no_session_token_when_not_set(self):
        auth = AWSSigV4Auth("AKID", "secret", "us-east-1", "s3")
        headers = {}
        auth.apply(_make_request(), headers)
        assert "x-amz-security-token" not in headers

    def test_session_token_included_when_set(self):
        auth = AWSSigV4Auth("AKID", "secret", "us-east-1", "s3", session_token="STOK")
        headers = {}
        auth.apply(_make_request(), headers)
        assert headers.get("x-amz-security-token") == "STOK"

    def test_credential_scope_in_authorization(self):
        auth = AWSSigV4Auth("AKID", "secret", "eu-west-1", "execute-api")
        headers = {}
        auth.apply(_make_request("https://api.example.com/v1/test"), headers)
        assert "eu-west-1/execute-api/aws4_request" in headers["Authorization"]

    def test_access_key_in_authorization(self):
        auth = AWSSigV4Auth("MYKEY", "secret", "us-east-1", "s3")
        headers = {}
        auth.apply(_make_request(), headers)
        assert "MYKEY" in headers["Authorization"]

    def test_signed_headers_field_present(self):
        auth = AWSSigV4Auth("AKID", "secret", "us-east-1", "s3")
        headers = {}
        auth.apply(_make_request(), headers)
        assert "SignedHeaders=" in headers["Authorization"]

    def test_signature_field_present(self):
        auth = AWSSigV4Auth("AKID", "secret", "us-east-1", "s3")
        headers = {}
        auth.apply(_make_request(), headers)
        assert "Signature=" in headers["Authorization"]

    def test_post_with_body(self):
        auth = AWSSigV4Auth("AKID", "secret", "us-east-1", "s3")
        headers = {}
        auth.apply(_make_request(method="POST", body=b'{"key": "val"}'), headers)
        assert "Authorization" in headers

    def test_query_string_canonicalized(self):
        auth = AWSSigV4Auth("AKID", "secret", "us-east-1", "s3")
        headers = {}
        auth.apply(
            _make_request("https://s3.amazonaws.com/bucket?b=2&a=1"),
            headers
        )
        # Should not raise, and auth header should be present
        assert "Authorization" in headers

    def test_different_regions_produce_different_signatures(self):
        req = _make_request()
        h1, h2 = {}, {}
        AWSSigV4Auth("AKID", "secret", "us-east-1", "s3").apply(req, h1)
        AWSSigV4Auth("AKID", "secret", "eu-west-1", "s3").apply(req, h2)
        assert h1["Authorization"] != h2["Authorization"]

    def test_different_services_produce_different_signatures(self):
        req = _make_request()
        h1, h2 = {}, {}
        AWSSigV4Auth("AKID", "secret", "us-east-1", "s3").apply(req, h1)
        AWSSigV4Auth("AKID", "secret", "us-east-1", "iam").apply(req, h2)
        assert h1["Authorization"] != h2["Authorization"]


# ── Serialization ─────────────────────────────────────────────────────────────

class TestSerialization:
    def test_to_dict_round_trip(self):
        auth = AWSSigV4Auth("K", "S", "ap-southeast-1", "lambda", session_token="T")
        d = auth.to_dict()
        restored = AWSSigV4Auth.from_dict(d)
        assert restored.access_key == "K"
        assert restored.secret_key == "S"
        assert restored.region == "ap-southeast-1"
        assert restored.service == "lambda"
        assert restored.session_token == "T"

    def test_to_dict_type_field(self):
        auth = AWSSigV4Auth("K", "S", "us-east-1", "s3")
        assert auth.to_dict()["type"] == "aws_sigv4"

    def test_from_dict_no_session_token(self):
        d = {"type": "aws_sigv4", "access_key": "K", "secret_key": "S",
             "region": "us-east-1", "service": "s3"}
        auth = AWSSigV4Auth.from_dict(d)
        assert auth.session_token is None

    def test_auth_type_constant(self):
        assert AWSSigV4Auth.AUTH_TYPE == "aws_sigv4"
