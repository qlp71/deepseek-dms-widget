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
import time
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


def fetch_url(
    url: str,
    credential: str,
    timeout: int = 30,
    retries: int = 3,
) -> tuple[int | None, str | None, str | None]:
    """Fetch a URL, retrying transient network/HTTP failures.

    The platform occasionally returns a 429/5xx or drops a connection while
    the widget is refreshing.  A single failed request used to turn one of the
    month series into an empty/zero value, which made the displayed totals
    disagree with each other.  Retry a couple of times with a short backoff.
    """
    # credential may be a Cookie string or "Bearer ..." Authorization header
    if credential.startswith("Bearer ") or credential.startswith("bearer "):
        headers = {**HEADERS_TEMPLATE, "Authorization": credential}
    else:
        headers = {**HEADERS_TEMPLATE, "Cookie": credential}
    req = Request(url, headers=headers)

    last_error: str | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8"), None
        except HTTPError as e:
            last_error = f"HTTP {e.code}"
            # Retry throttling and server-side failures only.  Auth/404 errors
            # are deterministic and should be surfaced immediately.
            if e.code not in (429, 500, 502, 503, 504) or attempt >= retries:
                return e.code, None, last_error
            retry_after = None
            try:
                retry_after = float(e.headers.get("Retry-After") or 0)
            except (AttributeError, TypeError, ValueError):
                pass
            if retry_after and retry_after > 0:
                time.sleep(min(retry_after, 10.0))
                continue
        except URLError as e:
            last_error = str(e.reason)
            if attempt >= retries:
                return None, None, last_error
        except Exception as e:
            last_error = str(e)
            if attempt >= retries:
                return None, None, last_error
        time.sleep(0.7 * (2 ** attempt))
    return None, None, last_error or "request failed"


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


def extract_biz_data(body: str) -> dict | list | None:
    """Return the API's ``biz_data`` payload (dict or list), if present."""
    try:
        o = json.loads(body)
        data = o.get("data")
        if not isinstance(data, dict):
            return None
        if "biz_data" in data:
            return data.get("biz_data")
        return data
    except Exception:
        return None


def _as_int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _as_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _iter_total_entries(biz_data) -> list[dict]:
    """Extract model/usage entries from all known API response shapes.

    The monthly endpoints currently return ``{total: [...]}``, while the
    group-by-day cost endpoint wraps that object in a one-element list.  Older
    platform versions occasionally returned a flat list of period objects.
    Normalize all of those here so every parser follows the same rules.
    """
    if isinstance(biz_data, dict):
        total = biz_data.get("total")
        return [e for e in total if isinstance(e, dict)] if isinstance(total, list) else []
    if isinstance(biz_data, list):
        entries: list[dict] = []
        for item in biz_data:
            if not isinstance(item, dict):
                continue
            total = item.get("total")
            if isinstance(total, list):
                entries.extend(e for e in total if isinstance(e, dict))
            elif "model" in item:
                # Already a flat model entry.
                entries.append(item)
        return entries
    return []


def _usage_amounts(model_entry: dict) -> dict[str, int]:
    usage = model_entry.get("usage") or []
    if not isinstance(usage, list):
        return {}
    return {
        str(u.get("type", "")): _as_int(u.get("amount"))
        for u in usage
        if isinstance(u, dict)
    }


def _prompt_tokens(amounts: dict[str, int]) -> int:
    """Return prompt tokens without double-counting aggregate + split fields.

    Newer responses expose cache-hit/cache-miss prompt tokens.  Some older
    responses only expose ``PROMPT_TOKEN``.  If both are present, the two
    cache fields are the precise breakdown and ``PROMPT_TOKEN`` is an
    aggregate/legacy field, so prefer the split fields.
    """
    split = amounts.get("PROMPT_CACHE_HIT_TOKEN", 0) + amounts.get("PROMPT_CACHE_MISS_TOKEN", 0)
    return split if split > 0 else amounts.get("PROMPT_TOKEN", 0)


def _iter_daily_entries(biz_data) -> list[dict]:
    """Extract day objects from grouped amount/cost API responses."""
    if isinstance(biz_data, dict):
        days = biz_data.get("days")
        return [d for d in days if isinstance(d, dict)] if isinstance(days, list) else []
    if isinstance(biz_data, list):
        days: list[dict] = []
        for item in biz_data:
            if not isinstance(item, dict):
                continue
            nested = item.get("days")
            if isinstance(nested, list):
                days.extend(d for d in nested if isinstance(d, dict))
            elif "date" in item or "day" in item:
                days.append(item)
        return days
    return []


def _day_number(day_entry: dict) -> int | None:
    date_str = str(day_entry.get("date") or "")
    if date_str:
        try:
            return int(date_str.split("-")[2])
        except (IndexError, ValueError):
            pass
    try:
        day = day_entry.get("day")
        return int(day) if day is not None else None
    except (TypeError, ValueError):
        return None


def parse_total_tokens(biz_data) -> tuple[int, int]:
    """Sum input/output tokens from the 'total' entries of an amount response."""
    inp = out = 0
    for model_entry in _iter_total_entries(biz_data):
        amounts = _usage_amounts(model_entry)
        inp += _prompt_tokens(amounts)
        out += amounts.get("RESPONSE_TOKEN", 0)
    return inp, out


def parse_total_cost(biz_data) -> str:
    """Sum all cost amounts from any supported monthly cost response shape."""
    total = 0.0
    for model_entry in _iter_total_entries(biz_data):
        for u in model_entry.get("usage") or []:
            if isinstance(u, dict):
                total += _as_float(u.get("amount"))
    return f"{total:.10f}" if total > 0 else "0"


def build_pricing_map(amount_body: str, cost_body: str) -> dict:
    """Derive per-model, per-token-type pricing from monthly API responses.

    Kept as a fallback for platforms/endpoints where the official grouped-by-
    day cost response is unavailable.
    """
    token_counts: dict[tuple[str, str], int] = {}
    for model_entry in _iter_total_entries(extract_biz_data(amount_body)):
        model = model_entry.get("model", "unknown")
        for token_type, amt in _usage_amounts(model_entry).items():
            if amt > 0:
                token_counts[(model, token_type)] = amt

    cost_map: dict[tuple[str, str], float] = {}
    for model_entry in _iter_total_entries(extract_biz_data(cost_body)):
        model = model_entry.get("model", "unknown")
        for token_type, amt in _usage_amounts(model_entry).items():
            if amt > 0:
                cost_map[(model, token_type)] = cost_map.get((model, token_type), 0.0) + amt

    pricing: dict[tuple[str, str], float] = {}
    for key, tokens in token_counts.items():
        cost = cost_map.get(key, 0)
        if tokens > 0 and cost > 0:
            pricing[key] = cost / tokens
    return pricing


def parse_daily(
    body: str,
    cur_day: int,
    pricing_map: dict | None = None,
    cost_by_day: dict[int, dict] | None = None,
) -> list[dict]:
    """Parse a group-by-day amount response into the widget's daily shape.

    When ``cost_by_day`` is supplied (parsed from the official group_by=day
    cost endpoint), those exact model/day costs are used.  The pricing-map
    calculation is kept only as a fallback for older platforms that do not
    expose the daily cost endpoint.
    """
    days_raw = _iter_daily_entries(extract_biz_data(body))

    if pricing_map is None:
        pricing_map = {}
    cost_by_day = cost_by_day or {}

    result = []
    for d in days_raw:
        day_num = _day_number(d)
        if day_num is None or day_num > cur_day:
            continue
        inp = out = 0
        day_cost = 0.0
        models = []  # per-model breakdown
        for model_entry in d.get("data", []):
            if not isinstance(model_entry, dict):
                continue
            model = model_entry.get("model", "unknown")
            amounts = _usage_amounts(model_entry)
            m_inp = _prompt_tokens(amounts)
            m_out = amounts.get("RESPONSE_TOKEN", 0)
            official_day = cost_by_day.get(day_num)
            if official_day is not None:
                m_cost = _as_float((official_day.get("models") or {}).get(model))
            else:
                m_cost = 0.0
                # Fallback: derive from monthly per-token-type prices.  Avoid
                # adding PROMPT_TOKEN when the split fields already exist.
                for token_type, amt in amounts.items():
                    if amt <= 0:
                        continue
                    if token_type == "PROMPT_TOKEN" and (
                        amounts.get("PROMPT_CACHE_HIT_TOKEN", 0) > 0
                        or amounts.get("PROMPT_CACHE_MISS_TOKEN", 0) > 0
                    ):
                        continue
                    price = pricing_map.get((model, token_type))
                    if price is not None and price > 0:
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

        official_day = cost_by_day.get(day_num)
        if official_day is not None:
            day_cost = _as_float(official_day.get("total"))

        result.append({
            "day": day_num,
            "inputTokens": inp,
            "outputTokens": out,
            "cost": round(day_cost, 6),
            "models": models,
        })
    return result


def parse_daily_cost(body: str, cur_day: int) -> dict[int, dict]:
    """Parse the group_by=day cost response.

    Returns ``{day: {"total": float, "models": {model: float}}}``.  The current
    platform wraps the day list in a one-element ``biz_data`` list; older
    responses used a dict/days shape or a flat list.  ``_iter_daily_entries``
    normalizes all of them.
    """
    days_raw = _iter_daily_entries(extract_biz_data(body))
    result: dict[int, dict] = {}
    for d in days_raw:
        day_num = _day_number(d)
        if day_num is None or day_num > cur_day:
            continue

        model_costs: dict[str, float] = {}
        entries = d.get("data")
        if not isinstance(entries, list) or not entries:
            entries = d.get("total")
        if isinstance(entries, list) and entries:
            for model_entry in entries:
                if not isinstance(model_entry, dict):
                    continue
                model = model_entry.get("model", "unknown")
                model_cost = sum(
                    _as_float(u.get("amount"))
                    for u in (model_entry.get("usage") or [])
                    if isinstance(u, dict)
                )
                model_costs[model] = model_costs.get(model, 0.0) + model_cost
            total_cost = sum(model_costs.values())
        else:
            # Simplified legacy shape: {date, amount} / {date, cost}.
            total_cost = _as_float(d.get("amount") if d.get("amount") is not None else d.get("cost"))

        result[day_num] = {
            "total": round(total_cost, 10),
            "models": {model: round(cost, 10) for model, cost in model_costs.items()},
        }

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
    """Parse /api/v0/users/get_api_keys response.

    Returns [{trackingId, name, sensitiveId, valid}] — every listed key is
    tagged valid=True (get_api_keys only returns active keys).
    """
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
            "valid": k.get("valid", True) is not False,
        })
    return result


def collect_key_meta_from_series(bodies: list[str]) -> dict[str, dict]:
    """Fallback: derive key metadata from by_api_key series.

    The by_api_key/amount response carries api_key metadata per series, so keys
    can be listed even if get_api_keys itself fails.  Series may still include
    recently deleted keys (api_key.valid == False) because of outstanding usage
    records — those are KEPT but tagged valid=False so the widget can mark them
    as deleted instead of hiding them (historical costs stay reconcilable).
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
                "valid": api_key.get("valid", True) is not False,
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
            if not isinstance(usage, dict):
                continue
            amounts = {str(k): _as_int(v) for k, v in usage.items()}
            inp = _prompt_tokens(amounts)
            out = amounts.get("RESPONSE_TOKEN", 0)
            if inp == 0 and out == 0:
                continue
            day = result.setdefault(tid, {}).setdefault(day_num, {"inp": 0, "out": 0, "models": {}})
            day["inp"] += inp
            day["out"] += out
            m = day["models"].setdefault(model, {"inp": 0, "out": 0})
            m["inp"] += inp
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

    # The platform UI and the user see local calendar months.  Using UTC here
    # made the widget jump to the previous month (and mix month boundaries) for
    # users east of UTC during the first hours of each month.
    local_now = datetime.now().astimezone()
    cur_year, cur_month = local_now.year, local_now.month
    tz_sec = get_tz_sec()
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

    # Build URL map.  The grouped-by-day amount/cost responses also contain
    # the monthly total, so one request per endpoint covers both the summary
    # card and the daily chart.  This halves request volume and avoids the
    # platform's aggressive 429 throttling.
    urls: dict[str, str] = {
        "summary": f"{BASE}/api/v0/users/get_user_summary",
        "apiKeys": f"{BASE}/api/v0/users/get_api_keys",
    }
    for (y2, m2) in months_to_fetch:
        key_a = f"amount_{y2}_{m2}"
        key_c = f"cost_{y2}_{m2}"
        urls[key_a] = f"{BASE}/api/v0/usage/amount?year={y2}&month={m2}&group_by=day"
        urls[key_c] = f"{BASE}/api/v0/usage/cost?year={y2}&month={m2}&group_by=day"
        # Per-API-key usage/cost (platform "by API key" view): epoch range + tz
        s_sec, e_sec = month_range_secs(y2, m2, tz_sec)
        urls[f"byKeyAmount_{y2}_{m2}"] = f"{BASE}/api/v0/usage/by_api_key/amount?start={s_sec}&end={e_sec}&tz={tz_sec}"
        urls[f"byKeyCost_{y2}_{m2}"] = f"{BASE}/api/v0/usage/by_api_key/cost?start={s_sec}&end={e_sec}&tz={tz_sec}"

    # Concurrent fetch
    raw: dict[str, tuple[int | None, str | None, str | None]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(fetch_url, url, credential): label for label, url in urls.items()}
        for fut in as_completed(futs):
            label = futs[fut]
            raw[label] = fut.result()

    # Check auth expiry
    auth_expired = any(
        r[1] and is_auth_expired(r[1]) for r in raw.values()
    )

    output: dict = {"ok": True, "authExpired": auth_expired}
    errors: list[str] = []    # aggregate/current data failures: block commit
    warnings: list[str] = []  # optional data failures: keep the good parts

    # Parse summary.  Balance is useful but not required for the usage chart,
    # so a transient summary failure must not hide otherwise valid usage data.
    s_code, s_body, s_err = raw.get("summary", (None, None, "not fetched"))
    if s_err:
        warnings.append(f"summary: {s_err}")
        output["balance"] = None
    elif s_code != 200:
        warnings.append(f"summary: HTTP {s_code}")
        output["balance"] = None
    else:
        biz = extract_biz_data(s_body or "")
        if isinstance(biz, dict):
            normal_wallets = biz.get("normal_wallets") or []
            bonus_wallets = biz.get("bonus_wallets") or []
            normal = normal_wallets[0] if normal_wallets else {}
            bonus = bonus_wallets[0] if bonus_wallets else {}
            output["balance"] = {
                "currency": normal.get("currency", "CNY"),
                "normal": normal.get("balance", "0"),
                "bonus": bonus.get("balance", "0"),
                # The current summary shape exposes token_estimation per wallet
                # and no longer exposes the old monthly_* fields.  Keep those
                # optional for older platform versions.
                "tokenEstimation": normal.get(
                    "token_estimation",
                    biz.get("total_available_token_estimation", "0"),
                ),
                "monthlyTokenUsage": biz.get("monthly_token_usage", "0"),
                "monthlyCosts": biz.get("monthly_costs", []),
                "totalCosts": biz.get("total_costs", []),
            }
        else:
            warnings.append("summary: unexpected response format")
            output["balance"] = None

    # Parse amount + cost and build the daily chart from the same responses.
    history: list[dict] = []
    daily_by_month: dict[str, list[dict]] = {}
    for (y2, m2) in months_to_fetch:
        month_key = f"{y2}-{m2}"
        is_current = (y2 == cur_year and m2 == cur_month)
        day_limit = local_day if is_current else 31
        key_a = f"amount_{y2}_{m2}"
        key_c = f"cost_{y2}_{m2}"

        a_code, a_body, a_err = raw.get(key_a, (None, None, "not fetched"))
        c_code, c_body, c_err = raw.get(key_c, (None, None, "not fetched"))
        biz_a = extract_biz_data(a_body or "") if (not a_err and a_code == 200) else None
        biz_c = extract_biz_data(c_body or "") if (not c_err and c_code == 200) else None

        entry: dict = {"year": y2, "month": m2, "inputTokens": 0, "outputTokens": 0, "cost": "0"}
        if isinstance(biz_a, (dict, list)):
            total_entries = _iter_total_entries(biz_a)
            if total_entries:
                entry["inputTokens"], entry["outputTokens"] = parse_total_tokens(biz_a)
            elif isinstance(biz_a, dict):
                # fallback: old flat structure
                entry["inputTokens"] = _as_int(biz_a.get("prompt_tokens") or biz_a.get("input_tokens"))
                entry["outputTokens"] = _as_int(biz_a.get("completion_tokens") or biz_a.get("output_tokens"))

        if isinstance(biz_c, (dict, list)):
            entry["cost"] = parse_total_cost(biz_c)
            if entry["cost"] == "0" and isinstance(biz_c, dict):
                costs = biz_c.get("costs") or biz_c.get("monthly_costs") or []
                if costs:
                    entry["cost"] = str(costs[0].get("amount", "0"))
                elif isinstance(biz_c.get("amount"), (int, float, str)):
                    entry["cost"] = str(biz_c["amount"])

        month_ok = isinstance(biz_a, (dict, list)) and isinstance(biz_c, (dict, list))
        daily_ok = False
        if month_ok:
            # Official daily cost is present in the same grouped-by-day cost
            # response.  Pricing-map derivation is only a compatibility
            # fallback when an older platform omits the day list.
            cost_by_day = parse_daily_cost(c_body or "", day_limit)
            monthly_pricing = build_pricing_map(a_body or "", c_body or "")
            if cost_by_day:
                daily_data = parse_daily(a_body or "", day_limit, monthly_pricing, cost_by_day)
            else:
                daily_data = parse_daily(a_body or "", day_limit, monthly_pricing)
                warnings.append(f"dailyCost_{month_key}: missing day cost data; used fallback")
            if daily_data:
                daily_by_month[month_key] = daily_data
                if is_current:
                    output["daily"] = daily_data
                daily_ok = True

        if month_ok and daily_ok:
            if is_current:
                output["current"] = entry
            else:
                history.append(entry)
        else:
            reason = a_err or c_err or f"HTTP {a_code}/{c_code}" or "unexpected response format"
            msg = f"month_{month_key}: {reason}"
            if is_current:
                errors.append(msg)
            else:
                warnings.append(msg)

    output["history"] = history
    output["dailyByMonth"] = daily_by_month

    # ── Per-API-key usage (platform "by API key" view) ───────────────
    k_code, k_body, k_err = raw.get("apiKeys", (None, None, "not fetched"))
    api_keys_ok = (not k_err and k_code == 200)
    api_keys = parse_api_keys(k_body or "") if api_keys_ok else []

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
            warnings.append(f"by_api_key_{month_key}: amount={a_err or a_code} cost={c_err or c_code}")
            continue

        amount_map = parse_by_api_key_amount(a_body or "", tz_sec, day_limit)
        cost_map = parse_by_api_key_cost(c_body or "", tz_sec, day_limit)
        for tid, daily in merge_by_api_key(amount_map, cost_map).items():
            by_api_key.setdefault(tid, {})[month_key] = daily

    # Merge key metadata derived from usage series.  Recently deleted keys
    # still appear there (valid=False) and are kept so the widget can mark
    # them instead of dropping their historical usage/cost data.  Keep the key
    # list independent from optional per-key usage requests: a 429 on the
    # by_api_key endpoints must not hide the selector itself.
    series_bodies = [
        (raw.get(f"byKeyAmount_{y2}_{m2}", (None, None, ""))[1] or "")
        for (y2, m2) in months_to_fetch
    ]
    known = {k["trackingId"] for k in api_keys}
    for tid, meta in collect_key_meta_from_series(series_bodies).items():
        if tid not in known:
            api_keys.append(meta)
    # Only publish if we actually obtained the list (or fallback metadata).
    # On total failure, leave the widget's existing selector untouched.
    if api_keys_ok or api_keys:
        output["apiKeys"] = api_keys
    if by_api_key_supported and by_api_key:
        output["byApiKey"] = by_api_key

    if auth_expired:
        errors.append("authentication expired")

    output["ok"] = not errors
    output["error"] = "; ".join(errors) if errors else None
    output["warning"] = "; ".join(warnings) if warnings else None

    print(json.dumps(output, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
