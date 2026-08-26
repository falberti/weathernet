import pytest
import requests

from weathernet_probe.transport import Transport, TransportError


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = text


def _make_transport():
    return Transport(
        server_url="https://example.invalid/api/v1/ingest",
        client_cert_path="/tmp/cert.pem",
        client_key_path="/tmp/key.pem",
        ca_cert_path="/tmp/ca.pem",
    )


def test_send_succeeds_on_2xx(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(200))
    _make_transport().send({"a": 1})


def test_send_raises_on_non_2xx_with_status_and_body_in_message(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(500, "server exploded"))
    with pytest.raises(TransportError, match="500.*server exploded"):
        _make_transport().send({"a": 1})


def test_send_raises_transport_error_on_request_exception(monkeypatch):
    def _raise(*a, **k):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(requests, "post", _raise)
    with pytest.raises(TransportError, match="no route to host"):
        _make_transport().send({"a": 1})


def test_send_passes_mtls_cert_ca_and_timeout(monkeypatch):
    captured = {}

    def _fake_post(url, json, cert, verify, timeout):
        captured.update(url=url, json=json, cert=cert, verify=verify, timeout=timeout)
        return _FakeResponse(200)

    monkeypatch.setattr(requests, "post", _fake_post)

    _make_transport().send({"a": 1})

    assert captured["url"] == "https://example.invalid/api/v1/ingest"
    assert captured["json"] == {"a": 1}
    assert captured["cert"] == ("/tmp/cert.pem", "/tmp/key.pem")
    assert captured["verify"] == "/tmp/ca.pem"
    assert captured["timeout"] == 10.0
