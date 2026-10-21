import unittest

from ai_web_agent.security import detect_mime, mime_matches


class SecurityTests(unittest.TestCase):
    def test_detects_pdf_magic_bytes(self):
        self.assertEqual(detect_mime(b"%PDF-1.7 data", "file.txt"), "application/pdf")

    def test_detects_html(self):
        self.assertEqual(detect_mime(b"<!doctype html><title>x</title>", "x"), "text/html")

    def test_flags_mime_mismatch(self):
        self.assertFalse(mime_matches("image/jpeg", "application/pdf"))


if __name__ == "__main__":
    unittest.main()
