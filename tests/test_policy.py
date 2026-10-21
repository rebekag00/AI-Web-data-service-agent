import socket
import unittest

from ai_web_agent.policy import PolicyViolation, URLPolicy


def resolver_for(address: str):
    def resolve(host: str, port: int, type: int = socket.SOCK_STREAM):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    return resolve


class URLPolicyTests(unittest.TestCase):
    def test_approves_public_allowlisted_host(self):
        target = URLPolicy(("example.com",), resolver_for("93.184.216.34")).approve(
            "https://www.example.com/report"
        )
        self.assertEqual(target.hostname, "www.example.com")
        self.assertEqual(target.ip, "93.184.216.34")

    def test_rejects_private_ip(self):
        policy = URLPolicy(("example.com",), resolver_for("127.0.0.1"))
        with self.assertRaisesRegex(PolicyViolation, "non-public"):
            policy.approve("https://example.com/")

    def test_rejects_unlisted_domain(self):
        policy = URLPolicy(("example.com",), resolver_for("93.184.216.34"))
        with self.assertRaisesRegex(PolicyViolation, "not allowlisted"):
            policy.approve("https://evil.example.net/")

    def test_rejects_url_credentials(self):
        policy = URLPolicy(("example.com",), resolver_for("93.184.216.34"))
        with self.assertRaisesRegex(PolicyViolation, "credentials"):
            policy.approve("https://user:password@example.com/")


if __name__ == "__main__":
    unittest.main()

