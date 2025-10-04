#!/usr/bin/env python3
import argparse
import base64
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

from pypdf import PdfReader, PdfWriter
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import random
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

SAFE = re.compile(r"[^a-zA-Z0-9._-]+")

def safe_name(url: str) -> str:
    base = url.strip().split("://")[-1]
    base = base[:150]
    return SAFE.sub("_", base) or "page"

def merge_pdfs(inputs: List[Path], output: Path):
    writer = PdfWriter()
    for p in inputs:
        reader = PdfReader(str(p))
        for page in reader.pages:
            writer.add_page(page)
    with open(output, "wb") as f:
        writer.write(f)

def compute_full_height_inches(driver, min_in=1.0, max_in=200.0, margin_in=0.4):
    # Chrome's printToPDF assumes ~96 CSS px per inch.
    # We’ll measure the full document height in CSS pixels and convert to inches.
    scroll_height = driver.execute_script(
        "return Math.max("
        "document.body.scrollHeight, document.documentElement.scrollHeight,"
        "document.body.offsetHeight, document.documentElement.offsetHeight,"
        "document.body.clientHeight, document.documentElement.clientHeight);"
    )
    height_inches = (scroll_height / 96.0) + (margin_in * 2.0)
    return max(min_in, min(height_inches, max_in))

def print_one_page_pdf(driver, url: str, out_path: Path, width_in=8.27, margin_in=0.4, wait_ms=1500):
    driver.get(url)
    # Let network & lazy content settle a bit
    time.sleep(wait_ms / 1000.0)

    # Ensure "screen" media and full rendering
    driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {"media": "screen"})

    # Calculate a tall single page height
    paper_height_in = compute_full_height_inches(driver, margin_in=margin_in)

    result = driver.execute_cdp_cmd("Page.printToPDF", {
        "printBackground": True,
        "preferCSSPageSize": False,
        "paperWidth": width_in,        # A4 width; change to 8.5 for Letter if you prefer
        "paperHeight": paper_height_in,
        "marginTop": margin_in,
        "marginBottom": margin_in,
        "marginLeft": margin_in,
        "marginRight": margin_in,
        "displayHeaderFooter": False,
        # "scale": 1.0,  # Optional: tweak if you need to shrink content slightly
        # "pageRanges": "1",  # Not needed; we force one physical page via tall paper height
    })
    pdf = base64.b64decode(result["data"])
    out_path.write_bytes(pdf)

def build_urls_from_range(base_url: str, start_from: int, start_to: int, step: int) -> List[str]:
    """Replace or inject 'start=' param across [start_from, start_to] inclusive."""
    urls = []
    parsed = urlparse(base_url)
    q = parse_qs(parsed.query)

    for s in range(start_from, start_to + 1, step):
        q_mod = dict(q)
        q_mod["start"] = [str(s)]
        new_query = urlencode({k: v[0] if isinstance(v, list) else v for k, v in q_mod.items()})
        new_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
        urls.append(new_url)
    return urls

def read_urls_from_file(path: Path) -> List[str]:
    out = []
    for line in path.read_text().splitlines():
        u = line.strip()
        if u:
            out.append(u)
    return out

def main():
    ap = argparse.ArgumentParser(description="Print webpages to single-page PDFs and merge.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--urls-file", type=str, help="Text file with one URL per line.")
    src.add_argument("--base-url", type=str, help="Base URL containing 'start=' param or accepts it.")
    ap.add_argument("--start-from", type=int, default=0, help="Start value for 'start=' when using --base-url.")
    ap.add_argument("--start-to", type=int, default=0, help="End value for 'start=' (inclusive) when using --base-url.")
    ap.add_argument("--step", type=int, default=10, help="Step for 'start=' when using --base-url.")
    ap.add_argument("--out-dir", type=str, default="pdf_pages", help="Directory for individual PDFs.")
    ap.add_argument("--merged", type=str, default="merged.pdf", help="Output merged PDF path.")
    ap.add_argument("--letter", action="store_true", help="Use Letter width (8.5in) instead of A4 (8.27in).")
    ap.add_argument("--margin", type=float, default=0.4, help="Margins in inches on all sides.")
    ap.add_argument("--wait-ms", type=int, default=1500, help="Extra wait after load before printing.")
    ap.add_argument("--headful", action="store_true", help="Run Chrome with UI (debugging).")
    ap.add_argument("--user-data-dir", type=str,
                help="Path to a Chrome user data dir to reuse (keeps cookies, login, etc.).")
    ap.add_argument("--rest-every", type=int, default=10,
                    help="After this many pages, rest for a short cooldown.")
    ap.add_argument("--cooldown-sec", type=int, default=1,
                    help="Cooldown seconds after each burst.")
    ap.add_argument("--min-wait", type=float, default=2.0,
                    help="Minimum random wait between pages (seconds).")
    ap.add_argument("--max-wait", type=float, default=5.0,
                    help="Maximum random wait between pages (seconds).")
    ap.add_argument("--captcha-timeout", type=int, default=600,
                    help="Max seconds to wait for you to manually solve a CAPTCHA in headful mode.")
    args = ap.parse_args()

    if args.urls_file:
        urls = read_urls_from_file(Path(args.urls_file))
    else:
        urls = build_urls_from_range(args.base_url, args.start_from, args.start_to, args.step)

    if not urls:
        print("No URLs to process.", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Set up Chrome
    chrome_options = Options()
    if not args.headful:
        chrome_options.add_argument("--headless=new")
    if args.user_data_dir:
        chrome_options.add_argument(f"--user-data-dir={args.user_data_dir}")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1280,2000")  # large viewport to reduce reflow surprises
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(ChromeDriverManager().install(), options=chrome_options)

    def maybe_wait_for_captcha(driver, timeout_s: int) -> bool:
        """
        If Scholar shows a CAPTCHA, pause here and let the user solve it manually.
        Returns True if we detected a captcha and waited (so caller can retry printing).
        """
        try:
            # Common Scholar captcha container ids/classes (best-effort)
            captcha_present = driver.find_elements(By.ID, "gs_captcha_ccl") or \
                            driver.find_elements(By.ID, "recaptcha") or \
                            driver.find_elements(By.CSS_SELECTOR, "form[action*='sorry']")

            if captcha_present:
                print("⚠️ CAPTCHA detected. Please solve it in the visible browser window.")
                # Poll for disappearance up to timeout
                end = time.time() + timeout_s
                while time.time() < end:
                    time.sleep(3)
                    still_there = driver.find_elements(By.ID, "gs_captcha_ccl") or \
                                driver.find_elements(By.ID, "recaptcha") or \
                                driver.find_elements(By.CSS_SELECTOR, "form[action*='sorry']")
                    if not still_there:
                        print("✅ CAPTCHA cleared. Resuming.")
                        return True
                print("⌛ CAPTCHA not cleared within timeout; continuing anyway.")
                return True
        except Exception:
            pass
        return False
    pdf_paths = []
    width = 8.5 if args.letter else 8.27

    try:
        for i, url in enumerate(urls, 1):
            name = f"{i:03d}_{safe_name(url)}.pdf"
            out_path = out_dir / name
            print(f"[{i}/{len(urls)}] Printing → {out_path.name}")

            # Random human-ish delay before each navigation
            delay = random.uniform(args.min_wait, args.max_wait)
            time.sleep(delay)

            # Simple backoff retries
            attempts, backoff = 0, 3
            while attempts < 3:
                attempts += 1
                try:
                    driver.get(url)
                    # If CAPTCHA appears, pause and let you solve it
                    if args.headful and maybe_wait_for_captcha(driver, args.captcha_timeout):
                        # After captcha solved, reload page to ensure content
                        driver.get(url)

                    # Let things settle (network idle + lazy images)
                    time.sleep(max(1.0, args.wait_ms / 1000.0))

                    # Now print single-page PDF
                    print_one_page_pdf(
                        driver, url, out_path,
                        width_in=width,
                        margin_in=args.margin,
                        wait_ms=0  # already waited
                    )
                    pdf_paths.append(out_path)
                    break  # success
                except WebDriverException as e:
                    print(f"  ! Attempt {attempts} failed: {e}. Backing off {backoff}s.")
                    time.sleep(backoff)
                    backoff *= 2  # exponential backoff

            # Cooldown after bursts
            if i % args.rest_every == 0 and i < len(urls):
                print(f"🛑 Cooling down for {args.cooldown_sec}s to be polite to Scholar.")
                time.sleep(args.cooldown_sec)

    finally:
        driver.quit()
    if not pdf_paths:
        print("No PDFs were created; nothing to merge.", file=sys.stderr)
        sys.exit(2)

    merged_path = Path(args.merged)
    merge_pdfs(pdf_paths, merged_path)
    print(f"Done. Merged PDF → {merged_path.resolve()}")
    print(f"Individual PDFs in → {out_dir.resolve()}")

if __name__ == "__main__":
    main()

