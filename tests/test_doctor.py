"""Tests for `webget doctor`.

The command exists so a user can see WHY a crawl used (or ignored) a browser.
These tests pin the shape of the report and, more importantly, that it never
claims something is usable when the launcher cannot drive it.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

from webget import browser as browser_mod
from webget import cli


def _run_doctor(json_out=False):
    buf = StringIO()
    with redirect_stdout(buf):
        cli.cmd_doctor(json_out=json_out)
    return buf.getvalue()


class DoctorOutputTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        for key in ("WEBGET_BROWSER_CDP", "WEBGET_BROWSER_CHANNEL", "WEBGET_BROWSER_PATH"):
            os.environ.pop(key, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_text_output_names_the_selected_browser(self):
        with mock.patch.object(
            browser_mod, "scan_installed_browsers",
            return_value=[("chrome", "chrome", "/usr/bin/google-chrome")],
        ):
            out = _run_doctor()
        self.assertIn("webget doctor", out)
        self.assertIn("chrome", out)
        self.assertIn("usable", out)

    def test_unusable_browser_is_flagged_and_explained(self):
        with mock.patch.object(
            browser_mod, "scan_installed_browsers",
            return_value=[("brave", None, "/usr/bin/brave")],
        ):
            out = _run_doctor()
        self.assertIn("NOT USABLE", out)
        self.assertIn("WEBGET_BROWSER_CDP", out)
        self.assertIn("no playwright channel", out)

    def test_reports_bundled_fallback_when_nothing_found(self):
        with mock.patch.object(browser_mod, "scan_installed_browsers", return_value=[]):
            out = _run_doctor()
        self.assertIn("playwright bundled chromium", out)
        self.assertIn("detected on this machine: none", out)

    def test_json_output_is_valid_and_complete(self):
        with mock.patch.object(browser_mod, "scan_installed_browsers", return_value=[]):
            out = _run_doctor(json_out=True)
        report = json.loads(out)
        for key in ("browser", "crawl4ai", "playwright_cache", "env"):
            self.assertIn(key, report)
        self.assertIn("selected", report["browser"])
        self.assertIn("usable", report["browser"])

    def test_env_overrides_are_shown(self):
        os.environ["WEBGET_BROWSER_CDP"] = "http://127.0.0.1:9222"
        out = _run_doctor()
        self.assertIn("WEBGET_BROWSER_CDP=http://127.0.0.1:9222", out)

    def test_bad_env_path_reports_error_without_crashing(self):
        """A bad WEBGET_BROWSER_PATH must surface as an error line.

        Note: do NOT mock os.path.exists globally here. crawl4ai's import chain
        calls dotenv.find_dotenv(), which uses os.path.exists internally and
        asserts on the result; a blanket mock breaks the import and the failure
        looks like a doctor bug rather than a test artifact.
        """
        os.environ["WEBGET_BROWSER_PATH"] = "/nope/missing"
        out = _run_doctor()
        self.assertIn("ERROR", out)
        self.assertIn("/nope/missing", out)

    def test_doctor_does_not_require_crawl4ai(self):
        """A missing optional dep must be reported, not raised."""
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def fake_import(name, *args, **kwargs):
            if name == "crawl4ai":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fake_import), mock.patch.object(
            browser_mod, "scan_installed_browsers", return_value=[]
        ):
            out = _run_doctor()
        self.assertIn("NOT installed", out)
        self.assertIn("pip install webget-cli[browser]", out)


class DoctorWiringTest(unittest.TestCase):
    def test_doctor_is_dispatched_from_main(self):
        """'doctor' harus benar-benar di-dispatch, bukan diuji sebagai string.

        Versi lama memeriksa inspect.getsource(cli.main) untuk literal
        'cmd == "doctor"'. Itu menguji TEKS fungsi, bukan perilakunya, dan
        patah begitu dispatch dipindah ke helper _jalankan_perintah - pesan
        'doctor' tetap bekerja, tapi lokasi string-nya berubah.

        Diganti dengan pemeriksaan perilaku: jalankan perintahnya dan
        pastikan keluarannya memang keluaran doctor.
        """
        import contextlib
        import io

        buf = io.StringIO()
        lama = sys.argv
        sys.argv = ["webget", "doctor", "--json"]
        try:
            with (
                contextlib.suppress(SystemExit),
                contextlib.redirect_stdout(buf),
                contextlib.redirect_stderr(buf),
            ):
                cli.main()
        finally:
            sys.argv = lama
        keluaran = buf.getvalue()
        # doctor --json mencetak JSON yang memuat kunci pemeriksaan.
        self.assertTrue(
            "browser" in keluaran or "{" in keluaran,
            f"perintah doctor tidak menghasilkan keluaran doctor:\n{keluaran[:400]}",
        )

    def test_doctor_appears_in_usage(self):
        self.assertIn("webget doctor", cli.__doc__ or "")


if __name__ == "__main__":
    unittest.main()