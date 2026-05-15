#!/usr/bin/env python3
"""
Fetch DeepSeek platform balance and usage via platform cookie.

Endpoints (all require platform.deepseek.com cookie):
  GET /api/v0/users/get_user_summary  -> balance + monthly summary
  GET /api/v0/usage/amount?year=Y&month=M -> input/output token breakdown
  GET /api/v0/usage/cost?year=Y&month=M   -> cost breakdown
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE = "https://platform.deepseek.com"
HEADERS_TEMPLATE = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/131.0.0.0 Safari/537.36",
    "Origin": BASE,
    "Referer": BASE + "/usage",
}


def fetch_url(url: str, credential: str, timeout: int = 30) -> tuple[int | None, str | None, str | None]:
    # credential may be a Cookie string or "Bearer ..." Authorization header
    if credential.startswith("Bearer ") or credential.startswith("bearer "):
        headers = {**HEADERS_TEMPLATE, "Authorization": credential}
    else:
        headers = {**HEADERS_TEMPLATE, "Cookie": credential}
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8"), None
    except URLError as e:
        return None, None, str(e.reason)
    except Exception as e:
        return None, None, str(e)


def is_auth_expired(body: str) -> bool:
    try:
        o = json.loads(body)
        if o.get("code") == 40002:
            return True
        if "missing token" in str(o.get("msg", "")).lower():
            return True
    except Exception:
        pass
    return False


def extract_biz_data(body: str) -> dict | None:
    try:
        o = json.loads(body)
        data = o.get("data", {})
        if isinstance(data, dict):
            return data.get("biz_data") or data
        return None
    except Exception:
        return None


def parse_total_tokens(biz_data: dict) -> tuple[int, int]:
    """Sum input/output tokens from the 'total' array in amount API response."""
    inp = out = 0
    for model_entry in biz_data.get("total", []):
        for u in model_entry.get("usage", []):
            t, amt = u.get("type", ""), int(u.get("amount", 0))
            if t in ("PROMPT_CACHE_HIT_TOKEN", "PROMPT_CACHE_MISS_TOKEN"):
                inp += amt
            elif t == "RESPONSE_TOKEN":
                out += amt
    return inp, out


def parse_total_cost(biz_data_list: list) -> str:
    """Sum all cost amounts from cost API biz_data (list of {total:[{model,usage}]})."""
    total = 0.0
    for period in biz_data_list:
        for model_entry in period.get("total", []):
            for u in model_entry.get("usage", []):
                try:
                    total += float(u.get("amount", 0))
                except (TypeError, ValueError):
                    pass
    return f"{total:.10f}" if total > 0 else "0"


def parse_daily(body: str, cur_day: int) -> list[dict]:
    """Parse group_by=day amount response into [{day, inputTokens, outputTokens}]."""
    try:
        o = json.loads(body)
        biz = (o.get("data") or {}).get("biz_data") or {}
        days_raw = biz.get("days", [])
    except Exception:
        return []

    result = []
    for d in days_raw:
        date_str = d.get("date", "")
        try:
            day_num = int(date_str.split("-")[2])
        except Exception:
            continue
        if day_num > cur_day:
            continue
        inp = out = 0
        for model_entry in d.get("data", []):
            for u in model_entry.get("usage", []):
                t, amt = u.get("type", ""), int(u.get("amount", 0))
                if t in ("PROMPT_CACHE_HIT_TOKEN", "PROMPT_CACHE_MISS_TOKEN"):
                    inp += amt
                elif t == "RESPONSE_TOKEN":
                    out += amt
        result.append({"day": day_num, "inputTokens": inp, "outputTokens": out})
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch DeepSeek platform usage")
    ap.add_argument("--cookie-file", required=True, help="Path to cookie.txt")
    ap.add_argument("--months", type=int, default=3, help="Number of history months to fetch (including current)")
    args = ap.parse_args()

    cookie_path = Path(args.cookie_file)
    if not cookie_path.exists():
        print(json.dumps({"ok": False, "error": f"cookie file not found: {cookie_path}", "authExpired": False}))
        return 1

    cookie = cookie_path.read_text(encoding="utf-8").strip()
    if not cookie:
        print(json.dumps({"ok": False, "error": "cookie file is empty", "authExpired": False}))
        return 1
    credential = cookie

    now = datetime.now(timezone.utc)
    cur_year, cur_month = now.year, now.month

    # Build list of months to fetch (oldest first)
    months_to_fetch: list[tuple[int, int]] = []
    y, m = cur_year, cur_month
    for _ in range(max(1, args.months)):
        months_to_fetch.insert(0, (y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1

    # Build URL map
    urls: dict[str, str] = {
        "summary": f"{BASE}/api/v0/users/get_user_summary",
        "daily": f"{BASE}/api/v0/usage/amount?year={cur_year}&month={cur_month}&group_by=day",
    }
    for (y2, m2) in months_to_fetch:
        key_a = f"amount_{y2}_{m2}"
        key_c = f"cost_{y2}_{m2}"
        urls[key_a] = f"{BASE}/api/v0/usage/amount?year={y2}&month={m2}"
        urls[key_c] = f"{BASE}/api/v0/usage/cost?year={y2}&month={m2}"

    # Concurrent fetch
    raw: dict[str, tuple[int | None, str | None, str | None]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(fetch_url, url, credential): label for label, url in urls.items()}
        for fut in as_completed(futs):
            label = futs[fut]
            raw[label] = fut.result()

    # Check auth expiry
    auth_expired = any(
        r[1] and is_auth_expired(r[1]) for r in raw.values()
    )

    output: dict = {"ok": True, "authExpired": auth_expired}
    errors: list[str] = []

    # Parse summary
    s_code, s_body, s_err = raw.get("summary", (None, None, "not fetched"))
    if s_err:
        errors.append(f"summary: {s_err}")
        output["balance"] = None
    elif s_code != 200:
        errors.append(f"summary: HTTP {s_code}")
        output["balance"] = None
    else:
        biz = extract_biz_data(s_body or "")
        if biz:
            normal_wallets = biz.get("normal_wallets", [])
            bonus_wallets = biz.get("bonus_wallets", [])
            normal = normal_wallets[0] if normal_wallets else {}
            bonus = bonus_wallets[0] if bonus_wallets else {}
            output["balance"] = {
                "currency": normal.get("currency", "CNY"),
                "normal": normal.get("balance", "0"),
                "bonus": bonus.get("balance", "0"),
                "tokenEstimation": biz.get("total_available_token_estimation", "0"),
                "monthlyTokenUsage": biz.get("monthly_token_usage", "0"),
                "monthlyCosts": biz.get("monthly_costs", []),
            }
        else:
            errors.append("summary: unexpected response format")
            output["balance"] = None

    # Parse amount + cost per month
    history: list[dict] = []
    for (y2, m2) in months_to_fetch:
        key_a = f"amount_{y2}_{m2}"
        key_c = f"cost_{y2}_{m2}"

        a_code, a_body, a_err = raw.get(key_a, (None, None, "not fetched"))
        c_code, c_body, c_err = raw.get(key_c, (None, None, "not fetched"))

        entry: dict = {"year": y2, "month": m2, "inputTokens": 0, "outputTokens": 0, "cost": "0"}

        if not a_err and a_code == 200:
            biz_a = extract_biz_data(a_body or "")
            if isinstance(biz_a, dict):
                if "total" in biz_a:
                    # new structure: {total: [{model, usage:[{type,amount}]}], days:[...]}
                    entry["inputTokens"], entry["outputTokens"] = parse_total_tokens(biz_a)
                else:
                    # fallback: old flat structure
                    entry["inputTokens"] = int(biz_a.get("prompt_tokens") or biz_a.get("input_tokens") or 0)
                    entry["outputTokens"] = int(biz_a.get("completion_tokens") or biz_a.get("output_tokens") or 0)

        if not c_err and c_code == 200:
            biz_c = extract_biz_data(c_body or "")
            if isinstance(biz_c, list):
                entry["cost"] = parse_total_cost(biz_c)
            elif isinstance(biz_c, dict):
                costs = biz_c.get("costs") or biz_c.get("monthly_costs") or []
                if costs:
                    entry["cost"] = str(costs[0].get("amount", "0"))
                elif isinstance(biz_c.get("amount"), (int, float, str)):
                    entry["cost"] = str(biz_c["amount"])

        is_current = (y2 == cur_year and m2 == cur_month)
        if is_current:
            output["current"] = entry
        else:
            history.append(entry)

    output["history"] = history

    # Parse daily breakdown for current month
    d_code, d_body, d_err = raw.get("daily", (None, None, "not fetched"))
    if not d_err and d_code == 200:
        output["daily"] = parse_daily(d_body or "", now.day)
    else:
        output["daily"] = []
        if d_err:
            errors.append(f"daily: {d_err}")

    if errors:
        output["ok"] = False
        output["error"] = "; ".join(errors)
    else:
        output["error"] = None

    print(json.dumps(output, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
