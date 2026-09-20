"""Discover a browser already installed on the user's machine.

Reasoning: Playwright bundles a Chromium (~150MB) and `webget-cli[browser]`
pulls it in. Almost every machine already has a Chromium-family browser, so
downloading another one is pure waste. This module finds what exists and
reports what is actually USABLE, which is a narrower set.

Design constraints:

* Detection happens at RUNTIME, not at install time. A browser can be installed
  or removed after `pip install`, so an install-time probe is stale by the time
  it matters. Nothing here runs from a setup hook.
* Explicit user intent always wins. Env vars are checked before any scanning.
* Detected does not mean usable. crawl4ai 0.9.2 only forwards `channel` to
  Playwright: `_build_browser_args()` emits `{"headless", "args", "channel"}`
  and calls `playwright.chromium.launch(**browser_args)`. There is no path for
  `executable_path`, even though Playwright itself supports it. So Brave,
  Vivaldi and Opera cannot be launched by crawl4ai at all unless the user goes
  through CDP. Reporting them as "found" without that caveat would be a lie.
* Firefox is intentionally NOT auto-selected. Playwright drives it over
  WebDriver BiDi with its own patched build; a distro Firefox fails in ways
  that look like network errors.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

# Channels crawl4ai will actually forward to Playwright. Anything else needs CDP.
CHANNEL_CAPABLE = {"chrome", "msedge", "chromium"}

# Chromium-family browsers, in preference order. The order encodes intent: a
# real Chrome is a better bet than a distro Chromium build.
#   (name, channel_or_None, [posix binaries], [macOS app bundle names])
_KNOWN = (
    ("chrome", "chrome", ("google-chrome", "google-chrome-stable"), ("Google Chrome",)),
    ("edge", "msedge", ("microsoft-edge", "microsoft-edge-stable"), ("Microsoft Edge",)),
    ("chromium", "chromium", ("chromium", "chromium-browser"), ("Chromium",)),
    ("brave", None, ("brave-browser", "brave"), ("Brave Browser",)),
    ("vivaldi", None, ("vivaldi", "vivaldi-stable"), ("Vivaldi",)),
    ("opera", None, ("opera",), ("Opera",)),
)

_MAC_APP_DIRS = ("/Applications", os.path.expanduser("~/Applications"))

_WIN_CANDIDATES = (
    ("chrome", "chrome", (r"Google\Chrome\Application\chrome.exe",)),
    ("edge", "msedge", (r"Microsoft\Edge\Application\msedge.exe",)),
    ("brave", None, (r"BraveSoftware\Brave-Browser\Application\brave.exe",)),
)


@dataclass(frozen=True)
class BrowserChoice:
    """A resolved browser plus why it was chosen and whether it will work."""

    name: str
    path: str | None = None
    channel: str | None = None
    cdp_url: str | None = None
    source: str = "bundled"
    reason: str = ""
    usable: bool = True
    caveat: str = ""

    @property
    def is_external(self) -> bool:
        return self.source != "bundled"

    def to_browser_config_kwargs(self) -> dict:
        """The BrowserConfig kwargs this choice implies.

        crawl4ai forwards only `chrome_channel` and `cdp_url` to the launcher,
        so those are the only two things we may emit. Never `executable_path`:
        it raises TypeError on BrowserConfig in 0.9.2.
        """
        if self.cdp_url:
            return {"cdp_url": self.cdp_url}
        # crawl4ai skips the default "chromium" value on Windows, so only pass a
        # channel when it is a real one.
        if self.channel and self.channel != "chromium":
            return {"chrome_channel": self.channel}
        return {}

    def describe(self) -> str:
        if not self.is_external:
            return "playwright bundled chromium"
        if self.cdp_url:
            return f"{self.name} via CDP ({self.cdp_url}) [{self.source}]"
        if self.channel:
            return f"{self.name} (chrome_channel={self.channel}) [{self.source}]"
        return f"{self.name} at {self.path} [{self.source}] (NOT usable by crawl4ai)"


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _macos_bundle(bundle: str) -> str | None:
    """Return the executable inside a macOS .app bundle, if present."""
    for base in _MAC_APP_DIRS:
        candidate = os.path.join(base, f"{bundle}.app", "Contents", "MacOS", bundle)
        if os.path.isfile(candidate):
            return candidate
    return None


def _windows_candidates() -> list[tuple[str, str | None, str]]:
    found = []
    roots = []
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        value = os.environ.get(var)
        if value:
            roots.append(value)
    for name, channel, relatives in _WIN_CANDIDATES:
        for root in roots:
            for rel in relatives:
                candidate = os.path.join(root, rel)
                if os.path.isfile(candidate):
                    found.append((name, channel, candidate))
    return found


def scan_installed_browsers() -> list[tuple[str, str | None, str]]:
    """Return every Chromium-family browser found on this machine.

    Each entry is (name, playwright_channel_or_None, executable_path).
    Order follows _KNOWN, so the first entry is the preferred one.
    """
    if os.name == "nt":
        return _windows_candidates()

    found: list[tuple[str, str | None, str]] = []
    for name, channel, binaries, bundles in _KNOWN:
        resolved = None
        for binary in binaries:
            resolved = shutil.which(binary)
            if resolved:
                break
        if not resolved:
            for bundle in bundles:
                resolved = _macos_bundle(bundle)
                if resolved:
                    break
        if resolved:
            found.append((name, channel, resolved))
    return found


def _no_channel_caveat(name: str, path: str) -> str:
    """Explain why a detected browser cannot be driven, with the way out."""
    return (
        f"found {path} but crawl4ai 0.9.2 cannot launch it: Playwright has no "
        f"{name} channel and crawl4ai does not forward executable_path. Start it "
        "with --remote-debugging-port=9222 and set WEBGET_BROWSER_CDP="
        "http://127.0.0.1:9222 to use it."
    )


def resolve_browser() -> BrowserChoice:
    """Pick a browser for the crawl4ai pass.

    Precedence, highest first:

    1. ``WEBGET_BROWSER_CDP`` - attach to a running browser. The only route that
       works for browsers without a Playwright channel.
    2. ``WEBGET_BROWSER_PATH`` - an explicit binary. Only usable if the binary
       sits on a Chromium channel; otherwise it is reported unusable.
    3. ``WEBGET_BROWSER_CHANNEL`` - an explicit Playwright channel.
    4. A channel-capable browser already installed on this machine.
    5. Playwright's bundled Chromium.

    Installed browsers beat the bundled download on purpose: downloading a
    second Chromium when one is already present is the waste this module exists
    to avoid.
    """
    # 1. CDP wins outright: it is the only mechanism that covers every browser.
    cdp = cdp_endpoint()
    if cdp:
        return BrowserChoice(
            name="cdp",
            cdp_url=cdp,
            source="env:WEBGET_BROWSER_CDP",
            reason="attaching to an existing browser over CDP",
        )

    explicit_channel = _env("WEBGET_BROWSER_CHANNEL")
    if explicit_channel:
        if explicit_channel not in CHANNEL_CAPABLE:
            return BrowserChoice(
                name=explicit_channel,
                channel=None,
                source="env:WEBGET_BROWSER_CHANNEL",
                usable=False,
                reason=f"channel {explicit_channel!r} is not supported by Playwright",
                caveat=(
                    f"Playwright has no {explicit_channel!r} channel. Supported: "
                    f"{', '.join(sorted(CHANNEL_CAPABLE))}. Use WEBGET_BROWSER_CDP."
                ),
            )
        return BrowserChoice(
            name=explicit_channel,
            channel=explicit_channel,
            source="env:WEBGET_BROWSER_CHANNEL",
            reason="explicit channel from the environment",
        )

    explicit_path = _env("WEBGET_BROWSER_PATH")
    if explicit_path:
        if not os.path.exists(explicit_path):
            # Fail loudly rather than silently falling through: a typo'd path
            # that quietly becomes "use the bundled browser" is a debugging trap.
            raise ValueError(
                f"WEBGET_BROWSER_PATH points at {explicit_path!r} but no such file exists"
            )
        # We cannot launch by path through crawl4ai. Report it honestly.
        return BrowserChoice(
            name=os.path.basename(explicit_path),
            path=explicit_path,
            source="env:WEBGET_BROWSER_PATH",
            usable=False,
            reason="crawl4ai cannot launch a browser by path",
            caveat=(
                "crawl4ai 0.9.2 does not forward executable_path to Playwright, "
                "so WEBGET_BROWSER_PATH cannot be honoured. Use "
                "WEBGET_BROWSER_CHANNEL for chrome/msedge/chromium, or "
                "WEBGET_BROWSER_CDP for anything else."
            ),
        )

    found = scan_installed_browsers()
    # Prefer anything with a channel: those are the only ones we can launch.
    for name, channel, path in found:
        if channel:
            return BrowserChoice(
                name=name,
                channel=channel,
                path=path,
                source="detected",
                reason=f"found {path} with a supported channel",
            )
    if found:
        # Nothing launchable, but something was found. Report it with the reason
        # rather than silently falling back and pretending nothing was there.
        name, _channel, path = found[0]
        return BrowserChoice(
            name=name,
            path=path,
            source="detected",
            usable=False,
            reason=f"found {path} but it has no Playwright channel",
            caveat=_no_channel_caveat(name, path),
        )

    return BrowserChoice(
        name="chromium",
        source="bundled",
        reason="no usable Chromium-family browser found; using the Playwright download",
    )


def cdp_endpoint() -> str | None:
    """Return the CDP endpoint the user opted into, if any.

    Deliberately opt-in and never auto-detected. Attaching to a browser the
    user is already logged into mixes their personal session cookies into crawl
    output, so it must be an explicit decision, not something we discover and
    silently use.
    """
    return _env("WEBGET_BROWSER_CDP")