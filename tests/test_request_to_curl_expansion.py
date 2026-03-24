import re

from equinox.core.request import Request


def test_to_curl_expands_placeholders_and_appends_params():
    req = Request(
        method="GET",
        url="https://api.example.com/users/{{userId}}/posts",
        params={"q": "1"},
        path_params={"userId": "42"},
    )

    curl = req.to_curl()
    assert "users/42/posts" in curl
    assert "q=1" in curl


def test_to_curl_handles_template_without_scheme_by_concat():
    req = Request(
        method="GET",
        url="/api/{{id}}",
        params={"page": "2"},
        path_params={"id": "123"},
    )

    curl = req.to_curl()
    # Should have performed simple concatenation for non-absolute/template URL
    assert "/api/123" in curl
    assert "page=2" in curl

