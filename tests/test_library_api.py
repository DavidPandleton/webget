"""Unit tests for webget high-level Python library API."""

import webget


def test_public_exports():
    assert hasattr(webget, "Client")
    assert hasattr(webget, "FetchResult")
    assert hasattr(webget, "fetch")
    assert hasattr(webget, "find_plugin")
    assert hasattr(webget, "list_plugins")
    assert hasattr(webget, "dispatch_plugin")


def test_fetch_result_properties():
    res_ok = webget.FetchResult(
        url="https://example.com",
        status="success",
        title="Example Domain",
        markdown="# Example",
    )
    assert res_ok.ok is True
    assert res_ok.title == "Example Domain"
    assert res_ok.markdown == "# Example"

    res_err = webget.FetchResult(
        url="https://example.com",
        status="login_required",
        error="Please login",
    )
    assert res_err.ok is False
    assert res_err.error == "Please login"


def test_client_init():
    c = webget.Client(profile="myprofile", timeout=15, strategy="http")
    assert c.profile == "myprofile"
    assert c.timeout == 15
    assert c.strategy == "http"
    assert c.headers == {}


def test_client_headers_dict():
    c = webget.Client(headers={"Authorization": "Bearer 123"})
    assert c.headers == {"Authorization": "Bearer 123"}


def test_client_headers_list():
    c = webget.Client(headers=["Authorization: Bearer 123", "X-Custom: Val"])
    assert c.headers.get("Authorization") == "Bearer 123"
    assert c.headers.get("X-Custom") == "Val"
