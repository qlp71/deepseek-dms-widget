#!/usr/bin/env python3
"""
Fetch DeepSeek platform balance and usage via platform cookie.

Endpoints (all require platform.deepseek.com cookie):
  GET /api/v0/users/get_user_summary  -> balance + monthly summary
  GET /api/v0/usage/amount?year=Y&month=M -> input/output token breakdown
  GET /api/v0/usage/cost?year=Y&month=M   -> cost breakdown
  GET /api/v0/users/get_api_keys      -> list of API keys
  GET /api/v0/usage/by_api_key/amount?start=S&end=E&tz=Z -> per-key usage buckets
  GET /api/v0/usage/by_api_key/cost?start=S&end=E&tz=Z   -> per-key cost buckets

The `by_api_key` endpoints back the platform's new "by API key" usage view and
let the widget break spending down per API key.  They take an epoch-second
[start, end) range plus a whole-hour timezone offset (seconds), mirroring the
platform frontend exactly.  They return 404 on older platform versions; the
fetcher then simply reports no keys.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
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
    except HTTPError as e:
        return e.code, None, f"HTTP {e.code}"
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


def build_pricing_map(amount_body: str, cost_body: str) -> dict:
    """Derive exact per-model, per-token-type pricing from monthly API responses.

    Uses the ACTUAL cost and token counts reported by DeepSeek to compute
    the price per token for each (model, token_type) combination.  This
    automatically adapts to peak/off-peak pricing changes.

    Returns {(model, token_type): price_per_token} (yuan per single token).
    """
    # ── Extract token counts from monthly amount response ──────
    token_counts: dict[tuple[str, str], int] = {}
    try:
        a_o = json.loads(amount_body)
        a_biz = (a_o.get("data") or {}).get("biz_data") or {}
        for model_entry in a_biz.get("total", []):
            model = model_entry.get("model", "unknown")
            for u in model_entry.get("usage", []):
                t = u.get("type", "")
                amt = int(u.get("amount", 0))
                if amt > 0:
                    token_counts[(model, t)] = amt
    except Exception:
        pass

    # ── Extract costs from monthly cost response ────────────────
    cost_map: dict[tuple[str, str], float] = {}
    try:
        c_o = json.loads(cost_body)
        c_biz = (c_o.get("data") or {}).get("biz_data") or {}
        entries = c_biz if isinstance(c_biz, list) else [c_biz]
        for entry in entries:
            for model_entry in entry.get("total", []):
                model = model_entry.get("model", "unknown")
                for u in model_entry.get("usage", []):
                    t = u.get("type", "")
                    try:
                        amt = float(u.get("amount", 0))
                    except (ValueError, TypeError):
                        amt = 0.0
                    if amt > 0:
                        cost_map[(model, t)] = cost_map.get((model, t), 0.0) + amt
    except Exception:
        pass

    # ── Derive per-token pricing ────────────────────────────────
    pricing: dict[tuple[str, str], float] = {}
    for key, tokens in token_counts.items():
        cost = cost_map.get(key, 0)
        if tokens > 0 and cost > 0:
            pricing[key] = cost / tokens

    return pricing


def parse_daily(body: str, cur_day: int, pricing_map: dict | None = None) -> list[dict]:
    """Parse group_by=day amount response into [{day, inputTokens, outputTokens, cost}].

    Cost is computed from per-token-type counts × the pricing map derived
    from the official monthly cost/amount APIs, so it always matches what
    the DeepSeek platform reports.
    """
    try:
        o = json.loads(body)
        biz = (o.get("data") or {}).get("biz_data") or {}
        days_raw = biz.get("days", [])
    except Exception:
        return []

    if pricing_map is None:
        pricing_map = {}

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
        day_cost = 0.0
        models = []  # per-model breakdown
        for model_entry in d.get("data", []):
            model = model_entry.get("model", "unknown")
            # Skip deprecated "chat" family models
            if "chat" in model.lower():
                continue
            m_inp = 0
            m_out = 0
            m_cost = 0.0
            for u in model_entry.get("usage", []):
                t = u.get("type", "")
                amt = int(u.get("amount", 0))
                if t in ("PROMPT_CACHE_HIT_TOKEN", "PROMPT_CACHE_MISS_TOKEN", "PROMPT_TOKEN"):
                    m_inp += amt
                elif t == "RESPONSE_TOKEN":
                    m_out += amt
                # Look up exact per-token price from official data
                price = pricing_map.get((model, t))
                if price is not None and price > 0 and amt > 0:
                    m_cost += amt * price
            inp += m_inp
            out += m_out
            day_cost += m_cost
            models.append({
                "model": model,
                "inputTokens": m_inp,
                "outputTokens": m_out,
                "cost": round(m_cost, 6),
            })
        result.append({
            "day": day_num,
            "inputTokens": inp,
            "outputTokens": out,
            "cost": round(day_cost, 6),
            "models": models,
        })
    return result


def parse_daily_cost(body: str, cur_day: int) -> list[dict]:
    """Parse group_by=day cost response into [{day, cost}].

    Supports multiple response shapes the DeepSeek cost API may return:
      - biz_data.days[] with {date, data: [{model, usage:[{type, amount}]}]}
      - biz_data as a flat list of {date, total: [{model, usage:[{type, amount}]}]}
      - biz_data as a flat list of {date, amount} (simplified)
    """
    try:
        o = json.loads(body)
        biz = (o.get("data") or {}).get("biz_data") or {}
    except Exception:
        return []

    # Shape 1: {days: [...]}
    if isinstance(biz, dict):
        days_raw = biz.get("days", [])
        if not days_raw:
            # Maybe the dict itself has date keys
            days_raw = list(biz.values()) if biz else []
    # Shape 2: flat list [{date, ...}, ...]
    elif isinstance(biz, list):
        days_raw = biz
    else:
        return []

    result = []
    for d in days_raw:
        if not isinstance(d, dict):
            continue
        date_str = d.get("date", "")
        try:
            day_num = int(date_str.split("-")[2])
        except Exception:
            # Try 'day' key as fallback
            day_num = d.get("day")
            if day_num is None:
                continue
        if day_num > cur_day:
            continue

        total_cost = 0.0
        # Format A: nested {data: [{model, usage:[{type, amount}]}]}
        entries = d.get("data", [])
        if entries:
            for model_entry in entries:
                for u in model_entry.get("usage", []):
                    try:
                        total_cost += float(u.get("amount", 0))
                    except (TypeError, ValueError):
                        pass
        else:
            # Format B: {total: [{model, usage:[{type, amount}]}]}
            total_entries = d.get("total", [])
            for model_entry in total_entries:
                for u in model_entry.get("usage", []):
                    try:
                        total_cost += float(u.get("amount", 0))
                    except (TypeError, ValueError):
                        pass
            # Format C: direct {amount: "0.123"} or {cost: "0.123"}
            if total_cost == 0:
                direct = d.get("amount") or d.get("cost") or 0
                try:
                    total_cost = float(direct)
                except (TypeError, ValueError):
                    pass

        result.append({"day": day_num, "cost": round(total_cost, 6)})

    return result


# ── Per-API-key usage (platform "by API key" view) ──────────────────

def get_tz_sec() -> int:
    """Whole-hour timezone offset in seconds (matches the platform's `tz` param)."""
    local = datetime.now().astimezone()
    off = int(local.utcoffset().total_seconds())
    return 3600 * (off // 3600)


def month_range_secs(year: int, month: int, tz_sec: int) -> tuple[int, int]:
    """Local-midnight epoch range for a month: [start of day 1, start of next month)."""
    start_dt = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end_dt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_dt = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    start = int(start_dt.timestamp()) - tz_sec
    end = int(end_dt.timestamp()) - tz_sec
    return start, end


def parse_api_keys(body: str) -> list[dict]:
    """Parse /api/v0/users/get_api_keys response -> [{trackingId, name, sensitiveId}]."""
    try:
        o = json.loads(body)
        biz = (o.get("data") or {}).get("biz_data") or {}
        keys = biz.get("api_keys") or []
    except Exception:
        return []
    result = []
    for k in keys:
        tid = k.get("tracking_id")
        if not tid:
            continue
        result.append({
            "trackingId": tid,
            "name": k.get("name") or tid,
            "sensitiveId": k.get("sensitive_id") or "",
        })
    return result


def collect_key_meta_from_series(bodies: list[str]) -> dict[str, dict]:
    """Fallback: derive {tracking_id: {trackingId, name, sensitiveId}} from by_api_key series.

    The by_api_key/amount response already carries api_key metadata per series,
    so keys can be listed even if get_api_keys itself fails.
    """
    meta: dict[str, dict] = {}
    for body in bodies:
        if not body:
            continue
        try:
            o = json.loads(body)
            biz = (o.get("data") or {}).get("biz_data") or {}
            series = biz.get("series") or []
        except Exception:
            continue
        for item in series:
            api_key = item.get("api_key") or {}
            tid = api_key.get("tracking_id")
            if not tid:
                continue
            meta.setdefault(tid, {
                "trackingId": tid,
                "name": api_key.get("name") or tid,
                "sensitiveId": api_key.get("sensitive_id") or "",
            })
    return meta


def parse_by_api_key_amount(body: str, tz_sec: int, cur_day: int) -> dict[str, dict]:
    """Parse /api/v0/usage/by_api_key/amount response.

    biz_data.series[] = [{api_key:{tracking_id,name,sensitive_id,valid}, model,
                          buckets:[{time (unix sec), usage:{RESPONSE_TOKEN, REQUEST,
                          PROMPT_CACHE_HIT_TOKEN, PROMPT_CACHE_MISS_TOKEN}}]}]

    Buckets are aggregated per day in the requested timezone (the platform may
    return hourly buckets for short ranges; this always folds them by day).

    Returns {tid: {day: {"inp": int, "out": int, "models": {model: {"inp":..,"out":..}}}}}
    """
    try:
        o = json.loads(body)
        biz = (o.get("data") or {}).get("biz_data") or {}
        series = biz.get("series") or []
    except Exception:
        return {}
    tzinfo = timezone(timedelta(seconds=tz_sec))

    result: dict[str, dict] = {}
    for item in series:
        api_key = item.get("api_key") or {}
        tid = api_key.get("tracking_id")
        if not tid:
            continue
        model = item.get("model", "unknown")
        for b in item.get("buckets") or []:
            t = b.get("time")
            if t is None:
                continue
            try:
                day_num = datetime.fromtimestamp(int(t), tz=tzinfo).day
            except (TypeError, ValueError, OSError):
                continue
            if day_num > cur_day:
                continue
            usage = b.get("usage") or {}
            try:
                hit = int(usage.get("PROMPT_CACHE_HIT_TOKEN") or 0)
                miss = int(usage.get("PROMPT_CACHE_MISS_TOKEN") or 0)
                out = int(usage.get("RESPONSE_TOKEN") or 0)
            except (TypeError, ValueError):
                hit = miss = out = 0
            if hit == 0 and miss == 0 and out == 0:
                continue
            day = result.setdefault(tid, {}).setdefault(day_num, {"inp": 0, "out": 0, "models": {}})
            day["inp"] += hit + miss
            day["out"] += out
            m = day["models"].setdefault(model, {"inp": 0, "out": 0})
            m["inp"] += hit + miss
            m["out"] += out
    return result


def parse_by_api_key_cost(body: str, tz_sec: int, cur_day: int) -> dict[str, dict]:
    """Parse /api/v0/usage/by_api_key/cost response.

    biz_data.data[] = [{currency, series: [{api_key, model, buckets:[{time, cost}]}]}]
    Returns {tid: {day: {model: cost}}}
    """
    try:
        o = json.loads(body)
        biz = (o.get("data") or {}).get("biz_data") or {}
        data = biz.get("data") if isinstance(biz, dict) else biz
    except Exception:
        return {}
    if not isinstance(data, list):
        return {}
    tzinfo = timezone(timedelta(seconds=tz_sec))

    result: dict[str, dict] = {}
    for group in data:
        for item in group.get("series") or []:
            api_key = item.get("api_key") or {}
            tid = api_key.get("tracking_id")
            if not tid:
                continue
            model = item.get("model", "unknown")
            for b in item.get("buckets") or []:
                t = b.get("time")
                if t is None:
                    continue
                try:
                    day_num = datetime.fromtimestamp(int(t), tz=tzinfo).day
                    cost = float(b.get("cost") or 0)
                except (TypeError, ValueError, OSError):
                    continue
                if day_num > cur_day or cost <= 0:
                    continue
                day = result.setdefault(tid, {}).setdefault(day_num, {})
                day[model] = day.get(model, 0.0) + cost
    return result


def merge_by_api_key(amount_map: dict, cost_map: dict) -> dict[str, list[dict]]:
    """Combine per-key amount + cost daily data into the widget's daily shape.

    Returns {tid: [{day, inputTokens, outputTokens, cost, models:
                    [{model, inputTokens, outputTokens, cost}]}]}
    """
    out: dict[str, list[dict]] = {}
    for tid in sorted(set(amount_map) | set(cost_map)):
        a_days = amount_map.get(tid) or {}
        c_days = cost_map.get(tid) or {}
        merged: dict[int, dict] = {}
        for d, v in a_days.items():
            merged[d] = {"day": d, "inputTokens": v["inp"], "outputTokens": v["out"], "cost": 0.0, "models": []}
        for d, models in c_days.items():
            day = merged.setdefault(d, {"day": d, "inputTokens": 0, "outputTokens": 0, "cost": 0.0, "models": []})
            day["cost"] = round(sum(models.values()), 6)
        for d, v in a_days.items():
            day = merged[d]
            day["models"] = [
                {
                    "model": model,
                    "inputTokens": mv["inp"],
                    "outputTokens": mv["out"],
                    "cost": round(c_days.get(d, {}).get(model, 0.0), 6),
                }
                for model, mv in v["models"].items()
            ]
        out[tid] = [merged[d] for d in sorted(merged)]
    return out


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
    tz_sec = get_tz_sec()
    local_now = datetime.now()  # for capping "future" days in the current month
    local_day = local_now.day

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
        "dailyCost": f"{BASE}/api/v0/usage/cost?year={cur_year}&month={cur_month}&group_by=day",
        "apiKeys": f"{BASE}/api/v0/users/get_api_keys",
    }
    for (y2, m2) in months_to_fetch:
        key_a = f"amount_{y2}_{m2}"
        key_c = f"cost_{y2}_{m2}"
        key_daily = f"daily_{y2}_{m2}"
        urls[key_a] = f"{BASE}/api/v0/usage/amount?year={y2}&month={m2}"
        urls[key_c] = f"{BASE}/api/v0/usage/cost?year={y2}&month={m2}"
        urls[key_daily] = f"{BASE}/api/v0/usage/amount?year={y2}&month={m2}&group_by=day"
        # Per-API-key usage/cost (platform "by API key" view): epoch range + tz
        s_sec, e_sec = month_range_secs(y2, m2, tz_sec)
        urls[f"byKeyAmount_{y2}_{m2}"] = f"{BASE}/api/v0/usage/by_api_key/amount?start={s_sec}&end={e_sec}&tz={tz_sec}"
        urls[f"byKeyCost_{y2}_{m2}"] = f"{BASE}/api/v0/usage/by_api_key/cost?start={s_sec}&end={e_sec}&tz={tz_sec}"

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
    pricing_map: dict = {}  # derived from current month's official data
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
            # Derive exact per-model per-token-type pricing from official monthly
            # amount + cost data so daily costs match the platform exactly.
            if not a_err and a_code == 200 and not c_err and c_code == 200:
                pricing_map = build_pricing_map(a_body or "", c_body or "")
        else:
            history.append(entry)

    output["history"] = history

    # Parse daily breakdown for ALL months (with group_by=day)
    daily_by_month: dict[str, list[dict]] = {}
    for (y2, m2) in months_to_fetch:
        month_key = f"{y2}-{m2}"
        is_current = (y2 == cur_year and m2 == cur_month)
        day_limit = local_day if is_current else 31  # cap future days for current month only
        key_daily = f"daily_{y2}_{m2}"
        d_code, d_body, d_err = raw.get(key_daily, (None, None, "not fetched"))

        # For non-current months, rebuild pricing from their own monthly data
        month_pricing = pricing_map if is_current else {}
        if not is_current:
            key_a = f"amount_{y2}_{m2}"
            key_c = f"cost_{y2}_{m2}"
            a_code2, a_body2, a_err2 = raw.get(key_a, (None, None, "not fetched"))
            c_code2, c_body2, c_err2 = raw.get(key_c, (None, None, "not fetched"))
            if not a_err2 and a_code2 == 200 and not c_err2 and c_code2 == 200:
                month_pricing = build_pricing_map(a_body2 or "", c_body2 or "")

        if not d_err and d_code == 200:
            daily_data = parse_daily(d_body or "", day_limit, month_pricing)
            daily_by_month[month_key] = daily_data
            if is_current:
                output["daily"] = daily_data
        else:
            daily_by_month[month_key] = []
            if is_current:
                output["daily"] = []
            if d_err:
                errors.append(f"daily_{month_key}: {d_err}")

    output["dailyByMonth"] = daily_by_month

    # ── Per-API-key usage (platform "by API key" view) ───────────────
    output["apiKeys"] = []
    output["byApiKey"] = {}

    k_code, k_body, k_err = raw.get("apiKeys", (None, None, "not fetched"))
    api_keys = parse_api_keys(k_body or "") if (not k_err and k_code == 200) else []

    by_api_key: dict[str, dict] = {}
    by_api_key_supported = True
    for (y2, m2) in months_to_fetch:
        month_key = f"{y2}-{m2}"
        is_current = (y2 == cur_year and m2 == cur_month)
        day_limit = local_day if is_current else 31

        a_code, a_body, a_err = raw.get(f"byKeyAmount_{y2}_{m2}", (None, None, "not fetched"))
        c_code, c_body, c_err = raw.get(f"byKeyCost_{y2}_{m2}", (None, None, "not fetched"))

        # The by_api_key endpoints only exist on the new platform UI; a 404
        # means the per-key view is unavailable on this account/version.
        if a_code == 404 or c_code == 404:
            by_api_key_supported = False
            break
        if a_err or c_err or a_code != 200 or c_code != 200:
            if is_current:
                errors.append(f"by_api_key_{month_key}: amount={a_err or a_code} cost={c_err or c_code}")
            continue

        amount_map = parse_by_api_key_amount(a_body or "", tz_sec, day_limit)
        cost_map = parse_by_api_key_cost(c_body or "", tz_sec, day_limit)
        for tid, daily in merge_by_api_key(amount_map, cost_map).items():
            by_api_key.setdefault(tid, {})[month_key] = daily

    if by_api_key_supported:
        # Fallback: derive key metadata from the series themselves when
        # get_api_keys is unavailable.
        series_bodies = [
            (raw.get(f"byKeyAmount_{y2}_{m2}", (None, None, ""))[1] or "")
            for (y2, m2) in months_to_fetch
        ]
        known = {k["trackingId"] for k in api_keys}
        for tid, meta in collect_key_meta_from_series(series_bodies).items():
            if tid not in known:
                api_keys.append(meta)
        output["apiKeys"] = api_keys
        output["byApiKey"] = by_api_key

    if errors:
        output["ok"] = False
        output["error"] = "; ".join(errors)
    else:
        output["error"] = None

    print(json.dumps(output, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
