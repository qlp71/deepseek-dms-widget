#!/usr/bin/env python3
"""
Launch Playwright Chromium to log in to platform.deepseek.com.
Intercepts the platform's own network requests to detect auth success,
then writes the cookie + Authorization header to file.

Usage:
  python3 scripts/login.py --output /path/to/cookie.txt [--timeout 900]

Exit codes:
  0  success
  1  timeout / auth not achieved
  2  playwright not installed
"""

from __future__ import annotations

import argparse
import sys
import time
import threading
from pathlib import Path

PLATFORM_URL = "https://platform.deepseek.com/usage"
PROBE_PATTERN = "/api/v0/users/get_user_summary"


def main() -> int:
    ap = argparse.ArgumentParser(description="DeepSeek platform cookie login helper")
    ap.add_argument("--output", required=True, type=Path, help="File to write cookie string to")
    ap.add_argument("--timeout", type=int, default=900, help="Max wait seconds (default 900)")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright not installed. Run: pip install -r scripts/requirements.txt && playwright install chromium",
            file=sys.stderr,
        )
        return 2

    print(
        f"Opening Chromium. Log in to platform.deepseek.com in the browser window.\n"
        f"Waiting up to {args.timeout}s for successful auth…",
        file=sys.stderr, flush=True,
    )

    captured: dict = {}
    deadline = time.time() + args.timeout

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(locale="zh-CN")
        page = context.new_page()

        def on_request(request):
            if PROBE_PATTERN in request.url and not captured.get("ok"):
                headers = request.headers
                auth = headers.get("authorization") or headers.get("Authorization") or ""
                cookie = headers.get("cookie") or headers.get("Cookie") or ""
                if auth or cookie:
                    print(f"[debug] intercepted request to {PROBE_PATTERN}", file=sys.stderr, flush=True)
                    print(f"[debug] auth present: {bool(auth)}, cookie len: {len(cookie)}", file=sys.stderr, flush=True)
                    captured["auth"] = auth
                    captured["cookie"] = cookie

        def on_response(response):
            if PROBE_PATTERN in response.url and not captured.get("ok"):
                try:
                    body = response.json()
                    print(f"[debug] response code={body.get('code')} captured={bool(captured.get('auth') or captured.get('cookie'))}", file=sys.stderr, flush=True)
                    if body.get("code") == 0 and (captured.get("auth") or captured.get("cookie")):
                        captured["ok"] = True
                        print("[debug] auth SUCCESS detected", file=sys.stderr, flush=True)
                except Exception as e:
                    print(f"[debug] response parse error: {e}", file=sys.stderr, flush=True)

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            page.goto(PLATFORM_URL, wait_until="domcontentloaded")
        except Exception:
            pass

        while time.time() < deadline:
            if captured.get("ok"):
                break
            try:
                page.wait_for_timeout(500)
            except Exception:
                break

        if not captured.get("ok"):
            try:
                browser.close()
            except Exception:
                pass
            print("Timeout: auth not detected within time limit.", file=sys.stderr)
            return 1

        # Build credential string: prefer Authorization header, fall back to cookie
        auth_val = captured.get("auth", "")
        cookie_val = captured.get("cookie", "")
        credential = auth_val if auth_val else cookie_val

        try:
            browser.close()
        except Exception:
            pass

    if not credential:
        print("No credentials captured after login.", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(credential, encoding="utf-8")
    print(f"Credentials saved to {args.output} (len={len(credential)})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
