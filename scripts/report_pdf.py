#!/usr/bin/env python3
"""Render the report HTML to PDF with headless Chromium.

Print settings mirror what the CSS @page expects: A4, background graphics on
(so the KPI blocks and table rules survive), and no extra header/footer since
the document carries its own.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/webget-report.html")
DST = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/webget-0.13.0-report.pdf")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(SRC.resolve().as_uri(), wait_until="load")
    page.emulate_media(media="print")
    page.pdf(
        path=str(DST),
        format="A4",
        print_background=True,
        margin={"top": "17mm", "bottom": "16mm", "left": "16mm", "right": "16mm"},
        display_header_footer=False,
        prefer_css_page_size=True,
    )
    browser.close()

size = DST.stat().st_size
print(f"wrote {DST} ({size:,} bytes)")
