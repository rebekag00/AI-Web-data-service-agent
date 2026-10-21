from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit


class PolicyViolation(ValueError):
    """Raised when a requested destination violates collection policy."""


Resolver = Callable[[str, int, int, int], list[tuple]]


@dataclass(frozen=True)
class ApprovedTarget:
    url: str
    scheme: str
    hostname: str
    port: int
    ip: str


class URLPolicy:
    def __init__(
        self,
        allowed_domains: tuple[str, ...],
        resolver: Resolver = socket.getaddrinfo,
        allowed_ports: tuple[int, ...] = (80, 443),
    ) -> None:
        self.allowed_domains = tuple(d.lower().strip(".") for d in allowed_domains)
        self.resolver = resolver
        self.allowed_ports = allowed_ports

    def approve(self, url: str) -> ApprovedTarget:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise PolicyViolation("only http and https are allowed")
        if parsed.username is not None or parsed.password is not None:
            raise PolicyViolation("credentials embedded in URLs are forbidden")
        if not parsed.hostname:
            raise PolicyViolation("URL hostname is required")

        hostname = parsed.hostname.lower().rstrip(".")
        if not any(hostname == d or hostname.endswith("." + d) for d in self.allowed_domains):
            raise PolicyViolation(f"domain is not allowlisted: {hostname}")

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in self.allowed_ports:
            raise PolicyViolation(f"port is not allowed: {port}")

        addresses: set[str] = set()
        try:
            for item in self.resolver(hostname, port, type=socket.SOCK_STREAM):
                addresses.add(item[4][0])
        except socket.gaierror as exc:
            raise PolicyViolation(f"DNS resolution failed for {hostname}") from exc
        if not addresses:
            raise PolicyViolation(f"DNS returned no addresses for {hostname}")

        for address in sorted(addresses):
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise PolicyViolation(f"destination resolves to a non-public IP: {address}")

        return ApprovedTarget(
            url=url,
            scheme=parsed.scheme,
            hostname=hostname,
            port=port,
            ip=sorted(addresses)[0],
        )

