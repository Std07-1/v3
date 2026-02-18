"""Тест: hardcode_scan знаходить відомі патерни (Slice-4).

Створюємо тимчасовий fixture-файл з відомими патернами
і перевіряємо що scan_repo() їх знаходить.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.audit.hardcode_scan import scan_repo, _PATTERNS


class TestHardcodeScanPatterns(unittest.TestCase):
    """Перевіряє що патерни regex правильно матчать відомі hardcode-и."""

    def _match(self, pat_id: str, text: str) -> bool:
        """Перевіряє чи текст матчить патерн з id."""
        for pid, regex, _, _ in _PATTERNS:
            if pid == pat_id:
                return regex.search(text) is not None
        self.fail(f"Патерн {pat_id} не знайдено")
        return False

    def test_tf_allowlist_match(self) -> None:
        self.assertTrue(self._match("HARDCODED_TF_LIST", "TF_ALLOWLIST = [60, 300]"))
        self.assertTrue(self._match("HARDCODED_TF_LIST", "DEFAULT_TF = {60}"))

    def test_tf_magic_numbers(self) -> None:
        self.assertTrue(self._match("HARDCODED_TF_MAGIC", "if tf_s == 14400:"))
        self.assertTrue(self._match("HARDCODED_TF_MAGIC", "return 86400"))

    def test_base_tf_hardcode(self) -> None:
        self.assertTrue(self._match("BASE_TF_HARDCODE", "base_tf_s = 60"))

    def test_timezone_prague(self) -> None:
        self.assertTrue(self._match("TIMEZONE_HARDCODE", 'tz = "Europe/Prague"'))
        self.assertTrue(self._match("TIMEZONE_HARDCODE", "CET timezone"))

    def test_timezone_local(self) -> None:
        self.assertTrue(self._match("TIMEZONE_LOCAL", "dt.astimezone()"))
        self.assertTrue(self._match("TIMEZONE_LOCAL", "time.localtime"))

    def test_silent_except(self) -> None:
        self.assertTrue(self._match("SILENT_EXCEPT", "except: pass"))
        self.assertTrue(self._match("SILENT_EXCEPT", "except Exception: pass"))

    def test_bucket_start_dup(self) -> None:
        self.assertTrue(self._match("BUCKET_START_DUP", "def bucket_start_ms(ts, tf_ms):"))

    def test_no_false_positive_comment(self) -> None:
        """Коментарі не матчать HARDCODED_TF_LIST."""
        self.assertFalse(self._match("HARDCODED_TF_LIST", "# TF values: 14400, 86400"))


class TestHardcodeScanRealRepo(unittest.TestCase):
    """Перевіряє що scan_repo повертає хоча б кілька хітів на реальному репо."""

    def test_scan_returns_hits(self) -> None:
        hits = scan_repo()
        self.assertIsInstance(hits, list)
        # Репозиторій має хоча б кілька magic number хітів
        self.assertGreater(len(hits), 0, "scan_repo повинен знайти хоча б 1 хіт")

    def test_scan_hit_structure(self) -> None:
        hits = scan_repo()
        if not hits:
            self.skipTest("Немає хітів для перевірки структури")
        h = hits[0]
        self.assertIn("pattern_id", h)
        self.assertIn("severity", h)
        self.assertIn("file", h)
        self.assertIn("line", h)
        self.assertIn("text", h)
        self.assertIn("description", h)
        self.assertIn(h["severity"], ("warn", "info"))

    def test_no_self_hits(self) -> None:
        """hardcode_scan.py не повинен знаходити сам себе."""
        hits = scan_repo()
        # Перевіряємо лише tools/audit/hardcode_scan.py — тестовий файл може мати хіти
        scanner_hits = [h for h in hits if h["file"] == "tools/audit/hardcode_scan.py"]
        self.assertEqual(len(scanner_hits), 0, "hardcode_scan.py знаходить сам себе — треба _is_self()")


if __name__ == "__main__":
    unittest.main()
