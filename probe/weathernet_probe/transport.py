import logging

import requests

logger = logging.getLogger(__name__)


class TransportError(Exception):
    """Raised on any send failure: network error, timeout, or non-2xx.

    Callers treat all of these the same way -- spool the reading and
    retry next cycle -- so this error type deliberately doesn't
    distinguish between them.
    """


class Transport:
    """mTLS HTTPS client for the ingestion endpoint.

    Certificate verification is never disabled, in either direction:
    the probe verifies the server's certificate against the CA (via
    `verify=ca_cert_path`) exactly as the server verifies the probe's.
    """

    def __init__(
        self,
        server_url: str,
        client_cert_path: str,
        client_key_path: str,
        ca_cert_path: str,
        timeout_seconds: float = 10.0,
    ):
        self.server_url = server_url
        self.client_cert_path = client_cert_path
        self.client_key_path = client_key_path
        self.ca_cert_path = ca_cert_path
        self.timeout_seconds = timeout_seconds

    def send(self, payload: dict) -> None:
        try:
            response = requests.post(
                self.server_url,
                json=payload,
                cert=(self.client_cert_path, self.client_key_path),
                verify=self.ca_cert_path,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise TransportError(f"request failed: {exc}") from exc

        if not response.ok:
            raise TransportError(
                f"server returned {response.status_code}: {response.text[:200]}"
            )
