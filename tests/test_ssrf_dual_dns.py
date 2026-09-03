import socket
from unittest.mock import patch

from webget.ssrf import _hostname_private, _resolve_hostname_ips


def test_resolve_hostname_ips_primary_success():
    ips = _resolve_hostname_ips("localhost")
    assert any(ip.startswith("127.") or ip == "::1" for ip in ips)


def test_resolve_hostname_ips_fallback_on_primary_failure():
    orig_getaddrinfo = socket.getaddrinfo

    def mock_getaddrinfo(host, port, *args, **kwargs):
        if host == "flaky.example":
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return orig_getaddrinfo(host, port, *args, **kwargs)

    # Secondary resolver gives fallback IP
    with (
        patch("socket.getaddrinfo", side_effect=mock_getaddrinfo),
        patch("webget.ssrf._doh_resolve", return_value=["93.184.216.34"]),
    ):
        ips = _resolve_hostname_ips("flaky.example")
        assert "93.184.216.34" in ips


def test_hostname_private_uses_fallback_and_detects_private():
    with (
        patch("socket.getaddrinfo", side_effect=socket.gaierror(socket.EAI_NONAME, "Fail")),
        patch("webget.ssrf._doh_resolve", return_value=["192.168.1.1"]),
    ):
        assert _hostname_private("router.local") is True
