#!/usr/bin/env python3
"""Import a DeepSeek platform credential using the user's normal browser.

The script opens platform.deepseek.com in the default browser and asks the
user to paste the Authorization request header copied from DevTools into a
secure zenity dialog.  The credential is verified and stored locally.

Usage:
  python3 scripts/login.py --output /path/to/cookie.txt [--locale zh_CN]

Exit codes:
  0  success
  1  timeout / auth not achieved / canceled
  2  zenity or xdg-open missing
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PLATFORM_URL = "https://platform.deepseek.com/usage"
BASE = "https://platform.deepseek.com"
HEADERS_TEMPLATE = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/131.0.0.0 Safari/537.36",
    "Origin": BASE,
    "Referer": BASE + "/usage",
}


def _is_zh(locale: str) -> bool:
    return str(locale).lower().startswith("zh")


def _normalize_credential(raw: str) -> str:
    """Accept the Authorization value, raw bearer token, or Cookie header."""
    value = (raw or "").strip()
    if not value:
        return ""

    # Strip a copied header name/protocol prefix if present.
    lower = value.lower()
    if lower.startswith("authorization:"):
        value = value.split(":", 1)[1].strip()
    elif lower.startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    elif lower.startswith("authorization="):
        value = value.split("=", 1)[1].strip()

    # Remove matching surrounding quotes from copied DevTools values.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()

    if value.lower().startswith("bearer "):
        return value
    # A cookie header usually contains '=' or ';'.  A bare token does not.
    if "=" in value or ";" in value:
        return value
    return "Bearer " + value


def _verify_credential(credential: str, timeout: int = 20) -> tuple[bool, str]:
    """Make one authenticated request so a bad/expired paste is rejected."""
    if credential.lower().startswith("bearer "):
        headers = {**HEADERS_TEMPLATE, "Authorization": credential}
    else:
        headers = {**HEADERS_TEMPLATE, "Cookie": credential}
    req = Request(f"{BASE}/api/v0/users/get_user_summary", headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        return False, f"HTTP {e.code}"
    except URLError as e:
        return False, str(e.reason)
    except Exception as e:
        return False, str(e)

    try:
        payload = json.loads(body)
    except Exception:
        return False, "response was not JSON"
    data = payload.get("data")
    if payload.get("code") == 0 and isinstance(data, dict) and data.get("biz_code") == 0:
        return True, ""
    msg = payload.get("msg") or payload.get("code") or "authentication rejected"
    return False, str(msg)


def _save_output(path: Path, credential: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(credential, encoding="utf-8")


def _run_zenity(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
    )


def import_from_system_browser(output: Path, locale: str) -> int:
    """Open the user's browser and import a credential pasted by the user."""
    zenity = shutil.which("zenity")
    if not zenity:
        print(
            "zenity is required for credential import. Install zenity and retry.",
            file=sys.stderr,
        )
        return 2

    opener = shutil.which("xdg-open")
    if not opener:
        print("xdg-open is required to open the default browser.", file=sys.stderr)
        return 2

    try:
        subprocess.Popen(
            [opener, PLATFORM_URL],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        print(f"Could not open the default browser: {e}", file=sys.stderr)
        return 2

    if _is_zh(locale):
        title = "DeepSeek 登录凭据"
        prompt = (
            "已在你的默认浏览器中打开 DeepSeek Usage 页面。\n\n"
            "1. 在浏览器里完成登录并打开 F12 开发者工具\n"
            "2. 切换到 Network/网络，刷新页面\n"
            "3. 筛选 get_user_summary，点击该请求\n"
            "4. 在 Request Headers/请求标头 中找到 authorization\n"
            "5. 复制完整值（通常以 Bearer 开头）粘贴到下面\n\n"
            "凭据只保存在本地 cookie.txt。"
        )
        bad_title = "凭据验证失败"
        bad_text = "platform.deepseek.com 未接受该凭据，请重新复制 Authorization。\n\n{error}"
    else:
        title = "DeepSeek credential"
        prompt = (
            "The DeepSeek Usage page was opened in your default browser.\n\n"
            "1. Log in and open DevTools (F12)\n"
            "2. Open Network and reload the page\n"
            "3. Filter for get_user_summary and select the request\n"
            "4. Find the authorization request header\n"
            "5. Copy its complete value (usually starts with Bearer) and paste it below\n\n"
            "The credential is stored locally in cookie.txt only."
        )
        bad_title = "Credential rejected"
        bad_text = "platform.deepseek.com rejected that credential. Please copy the Authorization header again.\n\n{error}"

    # A few attempts let the user correct a truncated paste without restarting
    # the whole login flow.
    last_error = ""
    for _ in range(3):
        try:
            proc = _run_zenity(
                [
                    zenity,
                    "--entry",
                    "--hide-text",
                    "--title=" + title,
                    "--text=" + prompt,
                    "--width=720",
                ]
            )
        except Exception as e:
            print(f"Could not open credential dialog: {e}", file=sys.stderr)
            return 2
        if proc.returncode != 0:
            print("Login canceled.", file=sys.stderr)
            return 1

        credential = _normalize_credential(proc.stdout)
        if not credential:
            continue

        ok, last_error = _verify_credential(credential)
        if ok:
            _save_output(output, credential)
            print(f"Credentials saved to {output} (len={len(credential)})", file=sys.stderr)
            return 0

        try:
            _run_zenity(
                [
                    zenity,
                    "--error",
                    "--title=" + bad_title,
                    "--text=" + bad_text.format(error=last_error),
                    "--width=520",
                ]
            )
        except Exception:
            pass

    print(f"Credential verification failed: {last_error}", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="DeepSeek platform credential import helper")
    ap.add_argument("--output", required=True, type=Path, help="File to write the credential to")
    ap.add_argument("--locale", default="en_US", help="UI locale, e.g. zh_CN or en_US")
    args = ap.parse_args()
    return import_from_system_browser(args.output, args.locale)


if __name__ == "__main__":
    raise SystemExit(main())
