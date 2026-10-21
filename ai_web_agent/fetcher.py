from __future__ import annotations

import http.client
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from .models import FetchResult, RunLimits
from .policy import ApprovedTarget, URLPolicy


class FetchError(RuntimeError):
    pass


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, target: ApprovedTarget, timeout: float) -> None:
        context = ssl.create_default_context()
        try:
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
        except (ImportError, OSError):
            pass
        super().__init__(target.hostname, target.port, timeout=timeout, context=context)
        self._pinned_ip = target.ip

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


@dataclass
class SafeFetcher:
    policy: URLPolicy
    limits: RunLimits
    user_agent: str = "AI-Web-Data-Service-MVP/0.1"

    def fetch(self, url: str) -> FetchResult:
        current = url
        redirects: list[str] = []
        for _ in range(self.limits.max_redirects + 1):
            target = self.policy.approve(current)
            status, headers, body = self._request(target)
            if status in {301, 302, 303, 307, 308}:
                location = headers.get("location")
                if not location:
                    raise FetchError("redirect response had no Location header")
                current = urljoin(current, location)
                redirects.append(current)
                continue
            return FetchResult(
                requested_url=url,
                final_url=current,
                status=status,
                headers=headers,
                body=body,
                resolved_ip=target.ip,
                redirects=tuple(redirects),
            )
        raise FetchError("maximum redirect count exceeded")

    def _request(self, target: ApprovedTarget) -> tuple[int, dict[str, str], bytes]:
        parsed = urlsplit(target.url)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        host_header = target.hostname
        if target.port not in {80, 443}:
            host_header += f":{target.port}"

        if target.scheme == "https":
            connection: http.client.HTTPConnection = _PinnedHTTPSConnection(
                target, self.limits.timeout_seconds
            )
        else:
            connection = http.client.HTTPConnection(
                target.ip, target.port, timeout=self.limits.timeout_seconds
            )
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Host": host_header,
                    "User-Agent": self.user_agent,
                    "Accept": "*/*",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            headers = {k.lower(): v for k, v in response.getheaders()}
            declared_length = headers.get("content-length")
            if declared_length and int(declared_length) > self.limits.max_response_bytes:
                raise FetchError("response exceeds maximum allowed size")
            body = response.read(self.limits.max_response_bytes + 1)
            if len(body) > self.limits.max_response_bytes:
                raise FetchError("response exceeds maximum allowed size")
            return response.status, headers, body
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            raise FetchError(f"network request failed: {type(exc).__name__}") from exc
        finally:
            connection.close()
