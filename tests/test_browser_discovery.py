"""Tests for browser discovery and the ladder's use of it.

No Chrome needed: everything here stubs the filesystem probe or checks the
precedence rules directly. The point is that the resolution ORDER and the
usability verdict are the product, not the individual detections.

The usability rules encode a hard constraint discovered by running the real
crawl4ai 0.9.2:

    browser_manager._build_browser_args() returns
        {"headless": ..., "args": ..., "channel": ...}
    and then calls playwright.chromium.launch(**browser_args).

There is no executable_path in that dict, so a browser without a Playwright
channel (Brave, Vivaldi, Opera) cannot be launched at all, even though
Playwright itself supports executable_path. BrowserConfig(...) even raises
TypeError on executable_path. These tests pin that reality down so a future
crawl4ai upgrade that fixes it will fail loudly here instead of silently.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from webget import browser as browser_mod


class ScanTest(unittest.TestCase):
    def test_brave_has_no_playwright_channel(self):
        with mock.patch("shutil.which") as which, mock.patch.object(
            browser_mod, "_macos_bundle", return_value=None
        ):
            which.side_effect = lambda b: "/usr/bin/brave" if b == "brave-browser" else None
            found = browser_mod.scan_installed_browsers()
        self.assertEqual(found, [("brave", None, "/usr/bin/brave")])

    def test_chrome_maps_to_channel(self):
        with mock.patch("shutil.which") as which, mock.patch.object(
            browser_mod, "_macos_bundle", return_value=None
        ):
            which.side_effect = lambda b: "/usr/bin/google-chrome" if b == "google-chrome" else None
            found = browser_mod.scan_installed_browsers()
        self.assertEqual(found, [("chrome", "chrome", "/usr/bin/google-chrome")])

    def test_chrome_preferred_over_chromium(self):
        with mock.patch("shutil.which") as which, mock.patch.object(
            browser_mod, "_macos_bundle", return_value=None
        ):
            which.side_effect = lambda b: {
                "google-chrome": "/usr/bin/google-chrome",
                "chromium": "/usr/bin/chromium",
            }.get(b)
            found = browser_mod.scan_installed_browsers()
        self.assertEqual([f[0] for f in found], ["chrome", "chromium"])

    def test_firefox_is_never_selected(self):
        """System Firefox cannot be driven by Playwright; it must not appear."""
        with mock.patch("shutil.which") as which, mock.patch.object(
            browser_mod, "_macos_bundle", return_value=None
        ):
            which.side_effect = lambda b: "/usr/bin/firefox" if b == "firefox" else None
            self.assertEqual(browser_mod.scan_installed_browsers(), [])

    def test_nothing_found_is_empty(self):
        with mock.patch("shutil.which", return_value=None), mock.patch.object(
            browser_mod, "_macos_bundle", return_value=None
        ):
            self.assertEqual(browser_mod.scan_installed_browsers(), [])


class UsabilityTest(unittest.TestCase):
    """Detected != usable. These pin the crawl4ai constraint."""

    def test_brave_detected_but_reported_unusable(self):
        with mock.patch.object(
            browser_mod, "scan_installed_browsers",
            return_value=[("brave", None, "/usr/bin/brave")],
        ):
            choice = browser_mod.resolve_browser()
        self.assertFalse(choice.usable)
        self.assertEqual(choice.to_browser_config_kwargs(), {})
        self.assertIn("brave", choice.describe().lower())
        self.assertIn("NOT usable", choice.describe())

    def test_unusable_caveat_names_the_way_out(self):
        with mock.patch.object(
            browser_mod, "scan_installed_browsers",
            return_value=[("brave", None, "/usr/bin/brave")],
        ):
            choice = browser_mod.resolve_browser()
        self.assertIn("WEBGET_BROWSER_CDP", choice.caveat)
        self.assertIn("remote-debugging-port", choice.caveat)

    def test_falls_back_to_bundled_when_nothing_found(self):
        with mock.patch.object(browser_mod, "scan_installed_browsers", return_value=[]):
            choice = browser_mod.resolve_browser()
        self.assertEqual(choice.source, "bundled")
        self.assertTrue(choice.usable)
        self.assertEqual(choice.to_browser_config_kwargs(), {})
        self.assertEqual(choice.describe(), "playwright bundled chromium")

    def test_usable_browser_wins_over_unusable_one(self):
        """If both Brave and Chrome exist, pick Chrome, do not stop at Brave."""
        with mock.patch.object(
            browser_mod, "scan_installed_browsers",
            return_value=[("brave", None, "/usr/bin/brave"), ("chrome", "chrome", "/usr/bin/google-chrome")],
        ):
            choice = browser_mod.resolve_browser()
        self.assertTrue(choice.usable)
        self.assertEqual(choice.channel, "chrome")


class ConfigKwargsTest(unittest.TestCase):
    """crawl4ai forwards chrome_channel and cdp_url, nothing else."""

    def test_channel_emits_chrome_channel(self):
        choice = browser_mod.BrowserChoice(name="chrome", channel="chrome")
        self.assertEqual(choice.to_browser_config_kwargs(), {"chrome_channel": "chrome"})

    def test_default_chromium_emits_nothing(self):
        """crawl4ai skips the default value because it breaks Windows."""
        choice = browser_mod.BrowserChoice(name="chromium", channel="chromium")
        self.assertEqual(choice.to_browser_config_kwargs(), {})

    def test_path_never_emits_executable_path(self):
        """BrowserConfig raises TypeError on executable_path in 0.9.2."""
        choice = browser_mod.BrowserChoice(name="brave", path="/usr/bin/brave")
        self.assertNotIn("executable_path", choice.to_browser_config_kwargs())
        self.assertEqual(choice.to_browser_config_kwargs(), {})

    def test_cdp_emits_cdp_url(self):
        choice = browser_mod.BrowserChoice(name="cdp", cdp_url="http://127.0.0.1:9222")
        self.assertEqual(choice.to_browser_config_kwargs(), {"cdp_url": "http://127.0.0.1:9222"})

    def test_cdp_wins_over_channel(self):
        choice = browser_mod.BrowserChoice(name="cdp", channel="chrome", cdp_url="http://x:1")
        self.assertEqual(choice.to_browser_config_kwargs(), {"cdp_url": "http://x:1"})

    def test_empty_when_bundled(self):
        self.assertEqual(browser_mod.BrowserChoice(name="chromium").to_browser_config_kwargs(), {})


class ResolvePrecedenceTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        for key in ("WEBGET_BROWSER_PATH", "WEBGET_BROWSER_CHANNEL", "WEBGET_BROWSER_CDP"):
            os.environ.pop(key, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_cdp_wins_over_everything(self):
        os.environ["WEBGET_BROWSER_CDP"] = "http://127.0.0.1:9222"
        os.environ["WEBGET_BROWSER_CHANNEL"] = "chrome"
        choice = browser_mod.resolve_browser()
        self.assertEqual(choice.cdp_url, "http://127.0.0.1:9222")
        self.assertEqual(choice.source, "env:WEBGET_BROWSER_CDP")

    def test_env_channel_wins_over_detection(self):
        os.environ["WEBGET_BROWSER_CHANNEL"] = "msedge"
        with mock.patch.object(
            browser_mod, "scan_installed_browsers",
            return_value=[("brave", None, "/usr/bin/brave")],
        ):
            choice = browser_mod.resolve_browser()
        self.assertEqual(choice.channel, "msedge")
        self.assertEqual(choice.source, "env:WEBGET_BROWSER_CHANNEL")

    def test_unsupported_env_channel_is_reported_not_ignored(self):
        """Asking for a channel Playwright does not have must not be silent."""
        os.environ["WEBGET_BROWSER_CHANNEL"] = "brave"
        choice = browser_mod.resolve_browser()
        self.assertFalse(choice.usable)
        self.assertIn("brave", choice.caveat.lower())
        self.assertIn("WEBGET_BROWSER_CDP", choice.caveat)

    def test_missing_env_path_raises_instead_of_falling_through(self):
        """A typo must not silently become 'use the bundled browser'."""
        os.environ["WEBGET_BROWSER_PATH"] = "/nope/does-not-exist"
        with mock.patch("os.path.exists", return_value=False), self.assertRaises(
            ValueError
        ) as ctx:
            browser_mod.resolve_browser()
        self.assertIn("/nope/does-not-exist", str(ctx.exception))

    def test_env_path_is_honest_about_being_unsupported(self):
        """We accept the var but must say it cannot be honoured by crawl4ai."""
        os.environ["WEBGET_BROWSER_PATH"] = "/opt/my/brave"
        with mock.patch("os.path.exists", return_value=True):
            choice = browser_mod.resolve_browser()
        self.assertFalse(choice.usable)
        self.assertIn("executable_path", choice.caveat)

    def test_detected_channel_browser_beats_bundled(self):
        with mock.patch.object(
            browser_mod, "scan_installed_browsers",
            return_value=[("chrome", "chrome", "/usr/bin/google-chrome")],
        ):
            choice = browser_mod.resolve_browser()
        self.assertTrue(choice.is_external)
        self.assertTrue(choice.usable)


class CdpTest(unittest.TestCase):
    def test_cdp_is_opt_in_only(self):
        """Attaching to a logged-in browser leaks the user's session; never auto."""
        saved = os.environ.pop("WEBGET_BROWSER_CDP", None)
        try:
            self.assertIsNone(browser_mod.cdp_endpoint())
            os.environ["WEBGET_BROWSER_CDP"] = "http://127.0.0.1:9222"
            self.assertEqual(browser_mod.cdp_endpoint(), "http://127.0.0.1:9222")
        finally:
            os.environ.pop("WEBGET_BROWSER_CDP", None)
            if saved is not None:
                os.environ["WEBGET_BROWSER_CDP"] = saved

    def test_resolve_does_not_auto_detect_cdp(self):
        saved = os.environ.pop("WEBGET_BROWSER_CDP", None)
        try:
            with mock.patch.object(browser_mod, "scan_installed_browsers", return_value=[]):
                choice = browser_mod.resolve_browser()
            self.assertEqual(choice.source, "bundled")
            self.assertIsNone(choice.cdp_url)
        finally:
            if saved is not None:
                os.environ["WEBGET_BROWSER_CDP"] = saved


class LadderWiringTest(unittest.TestCase):
    """The ladder must pass the resolved kwargs into BrowserConfig."""

    def test_ladder_passes_resolved_kwargs(self):
        import inspect

        from webget import ladder

        src = inspect.getsource(ladder)
        self.assertIn("_browser_module.resolve_browser()", src)
        self.assertIn("**bc_kwargs", src)

    def test_ladder_does_not_pass_executable_path(self):
        """BrowserConfig rejects it, so it must never reach the constructor."""
        import inspect

        from webget import ladder

        src = inspect.getsource(ladder)
        self.assertNotIn("executable_path=", src)

    def test_ladder_reports_a_bad_env_path_as_a_reason(self):
        import inspect

        from webget import ladder

        src = inspect.getsource(ladder)
        self.assertIn("except ValueError as exc:", src)

    def test_ladder_warns_when_browser_is_unusable(self):
        import inspect

        from webget import ladder

        src = inspect.getsource(ladder)
        self.assertIn("choice.usable", src)


if __name__ == "__main__":
    unittest.main()