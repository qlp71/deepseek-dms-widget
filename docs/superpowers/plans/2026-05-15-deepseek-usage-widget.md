# DeepSeek Usage Widget 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 DankMaterialShell 顶栏中实现 DeepSeek 余额与 Token 用量 Widget，支持 Playwright 登录、多月趋势图、i18n。

**Architecture:** QML PluginComponent 驱动状态与定时器，通过 `Proc.runCommand` 调用 Python 脚本拉取数据；Cookie 存为本地文件；历史月度数据通过 `savePluginData` 持久化；Canvas 绘制堆叠柱+折线趋势图。

**Tech Stack:** QML (Quickshell/DMS), Python 3, Playwright (Chromium), `urllib` (标准库), JSON

---

## 准备工作

在开始前执行以下操作：
1. `cd /home/gylove1994/code-spaces/deepseek-api-widget`
2. 确认旧插件已存在（后续 Task 1 会删除它）：`ls ~/.config/DankMaterialShell/plugins/`

---

## 文件清单

| 文件 | 操作 | 职责 |
|------|------|------|
| `plugin.json` | 创建 | 插件清单，id=deepseekWidget |
| `DeepSeekWidget.qml` | 创建 | 主 PluginComponent，bar pill + popout + Canvas 图表 |
| `DeepSeekSettings.qml` | 创建 | 设置面板，Cookie 状态 + 刷新间隔 + 历史月数 + 语言 |
| `i18n/zh_CN.json` | 创建 | 中文字符串 |
| `i18n/en_US.json` | 创建 | 英文字符串 |
| `scripts/fetch.py` | 创建 | 并发拉取 get_user_summary + amount + cost，输出 JSON |
| `scripts/login.py` | 创建 | Playwright Chromium 登录助手，输出 cookie.txt |
| `scripts/requirements.txt` | 创建 | playwright>=1.44.0 |
| `sync.sh` | 创建 | 删除旧插件 + rsync 到 DMS 插件目录 |
| `~/.config/DankMaterialShell/settings.json` | 修改 | 替换 barConfigs 中的 deepseekUsage 为 deepseekWidget |

---

## Task 1：清理旧插件 + 脚手架

**Files:**
- Create: `plugin.json`
- Create: `sync.sh`
- Modify: `~/.config/DankMaterialShell/settings.json`

- [ ] **Step 1: 删除旧插件目录**

```bash
rm -rf ~/.config/DankMaterialShell/plugins/DeepSeekUsageWidget
```

- [ ] **Step 2: 创建 plugin.json**

```json
{
  "id": "deepseekWidget",
  "name": "DeepSeek 用量",
  "description": "API 余额与平台 Token 用量，Cookie 自动登录",
  "version": "1.0.0",
  "author": "deepseek-api-widget",
  "type": "widget",
  "capabilities": ["dankbar-widget"],
  "component": "./DeepSeekWidget.qml",
  "settings": "./DeepSeekSettings.qml",
  "icon": "account_balance",
  "permissions": ["settings_read", "settings_write", "network", "process"],
  "requires": ["python3"],
  "requires_dms": ">=0.1.0"
}
```

- [ ] **Step 3: 创建 sync.sh**

```bash
#!/usr/bin/env bash
set -e
PLUGIN_ID="DeepSeekWidget"
SRC="$(cd "$(dirname "$0")" && pwd)"
DST="$HOME/.config/DankMaterialShell/plugins/$PLUGIN_ID"
OLD_DST="$HOME/.config/DankMaterialShell/plugins/DeepSeekUsageWidget"

[ -d "$OLD_DST" ] && rm -rf "$OLD_DST" && echo "Removed old plugin: $OLD_DST"

mkdir -p "$DST"
rsync -av \
  --exclude='.superpowers' \
  --exclude='docs' \
  --exclude='.git' \
  --exclude='sync.sh' \
  "$SRC/" "$DST/"
echo "Synced to: $DST"
```

```bash
chmod +x sync.sh
```

- [ ] **Step 4: 更新 settings.json — 替换 barConfigs 中的 widget 条目**

在 `~/.config/DankMaterialShell/settings.json` 的 `centerWidgets` 数组中，找到：
```json
{ "id": "deepseekUsage", "enabled": true }
```
替换为：
```json
{ "id": "deepseekWidget", "enabled": true }
```

同时在 `plugin_settings.json` 中，删除 `deepseekUsage` 键，添加：
```json
"deepseekWidget": { "enabled": true }
```

- [ ] **Step 5: 运行 sync.sh 确认目录同步正常（此时插件文件尚未全部创建，仅验证脚本可运行）**

```bash
bash sync.sh
ls ~/.config/DankMaterialShell/plugins/DeepSeekWidget/
```

Expected: 看到 `plugin.json` 和 `sync.sh` 之外的文件（暂时只有 plugin.json）

---

## Task 2：i18n 字符串文件

**Files:**
- Create: `i18n/zh_CN.json`
- Create: `i18n/en_US.json`

- [ ] **Step 1: 创建 i18n/zh_CN.json**

```bash
mkdir -p i18n
```

```json
{
  "pluginTitle": "DeepSeek 用量",
  "balance": "余额",
  "thisMonthInput": "本月输入",
  "thisMonthOutput": "本月输出",
  "thisMonthCost": "本月花费",
  "bonusBalance": "赠金余额",
  "relogin": "重新登录 Platform",
  "reloginHint": "Playwright Chromium 自动等待登录成功",
  "loginRunning": "浏览器已打开，完成登录后自动退出…",
  "authExpiredHint": "会话已过期，请重新登录",
  "tokenTrend": "近 %1 月 Token 趋势",
  "refresh": "刷新",
  "notLoggedIn": "未登录",
  "notLoggedInHint": "请点击「重新登录」获取 Cookie",
  "loading": "加载中…",
  "fetchError": "拉取失败",
  "usagePage": "Usage",
  "monitorPage": "监控",
  "apiKeysPage": "API Keys",
  "cookieStatusOk": "Cookie 有效",
  "cookieStatusExpired": "Cookie 已过期",
  "cookieStatusMissing": "未设置 Cookie",
  "lastLogin": "上次登录",
  "refreshInterval": "刷新间隔",
  "historyMonths": "趋势图历史月数",
  "language": "显示语言",
  "prereqTitle": "前置依赖",
  "prereqStep1": "进入插件目录",
  "prereqStep2": "安装依赖: pip install -r scripts/requirements.txt",
  "prereqStep3": "安装浏览器: playwright install chromium",
  "inputTokens": "输入",
  "outputTokens": "输出",
  "costLabel": "花费 (¥)",
  "min1": "1 分钟",
  "min5": "5 分钟",
  "min15": "15 分钟",
  "min30": "30 分钟",
  "month1": "1 个月",
  "month3": "3 个月",
  "month6": "6 个月",
  "langZh": "中文",
  "langEn": "English"
}
```

- [ ] **Step 2: 创建 i18n/en_US.json**

```json
{
  "pluginTitle": "DeepSeek Usage",
  "balance": "Balance",
  "thisMonthInput": "Input (Mo.)",
  "thisMonthOutput": "Output (Mo.)",
  "thisMonthCost": "Cost (Mo.)",
  "bonusBalance": "Bonus Balance",
  "relogin": "Re-login Platform",
  "reloginHint": "Playwright Chromium waits for login automatically",
  "loginRunning": "Browser opened, finish login to continue…",
  "authExpiredHint": "Session expired, please re-login",
  "tokenTrend": "Token Trend (%1 Mo.)",
  "refresh": "Refresh",
  "notLoggedIn": "Not logged in",
  "notLoggedInHint": "Click Re-login to get Cookie",
  "loading": "Loading…",
  "fetchError": "Fetch failed",
  "usagePage": "Usage",
  "monitorPage": "Monitor",
  "apiKeysPage": "API Keys",
  "cookieStatusOk": "Cookie valid",
  "cookieStatusExpired": "Cookie expired",
  "cookieStatusMissing": "No cookie set",
  "lastLogin": "Last login",
  "refreshInterval": "Refresh interval",
  "historyMonths": "Trend history months",
  "language": "Language",
  "prereqTitle": "Prerequisites",
  "prereqStep1": "Go to plugin directory",
  "prereqStep2": "Install deps: pip install -r scripts/requirements.txt",
  "prereqStep3": "Install browser: playwright install chromium",
  "inputTokens": "Input",
  "outputTokens": "Output",
  "costLabel": "Cost (¥)",
  "min1": "1 min",
  "min5": "5 min",
  "min15": "15 min",
  "min30": "30 min",
  "month1": "1 month",
  "month3": "3 months",
  "month6": "6 months",
  "langZh": "中文",
  "langEn": "English"
}
```

- [ ] **Step 3: Commit**

```bash
git init  # 若尚未初始化
git add plugin.json sync.sh i18n/
git commit -m "feat: scaffold plugin manifest, sync script, i18n strings"
```

---

## Task 3：Python 脚本 — scripts/fetch.py

**Files:**
- Create: `scripts/fetch.py`
- Create: `scripts/requirements.txt`

- [ ] **Step 1: 创建 scripts/requirements.txt**

```
playwright>=1.44.0
```

- [ ] **Step 2: 创建 scripts/fetch.py**

```python
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


def fetch_url(url: str, cookie: str, timeout: int = 30) -> tuple[int | None, str | None, str | None]:
    req = Request(url, headers={**HEADERS_TEMPLATE, "Cookie": cookie})
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


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


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
    }
    for (y2, m2) in months_to_fetch:
        key_a = f"amount_{y2}_{m2}"
        key_c = f"cost_{y2}_{m2}"
        urls[key_a] = f"{BASE}/api/v0/usage/amount?year={y2}&month={m2}"
        urls[key_c] = f"{BASE}/api/v0/usage/cost?year={y2}&month={m2}"

    # Concurrent fetch
    raw: dict[str, tuple[int | None, str | None, str | None]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(fetch_url, url, cookie): label for label, url in urls.items()}
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
            if biz_a:
                entry["inputTokens"] = int(biz_a.get("prompt_tokens") or biz_a.get("input_tokens") or 0)
                entry["outputTokens"] = int(biz_a.get("completion_tokens") or biz_a.get("output_tokens") or 0)

        if not c_err and c_code == 200:
            biz_c = extract_biz_data(c_body or "")
            if biz_c:
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

    if errors:
        output["ok"] = False
        output["error"] = "; ".join(errors)
    else:
        output["error"] = None

    print(json.dumps(output, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: 验证脚本帮助信息可正常打印**

```bash
python3 scripts/fetch.py --help
```

Expected: 打印 usage，无报错

- [ ] **Step 4: Commit**

```bash
git add scripts/
git commit -m "feat: add fetch.py for platform usage data"
```

---

## Task 4：Python 脚本 — scripts/login.py

**Files:**
- Create: `scripts/login.py`

- [ ] **Step 1: 创建 scripts/login.py**

```python
#!/usr/bin/env python3
"""
Launch Playwright Chromium to log in to platform.deepseek.com.
Polls /api/v0/users/get_user_summary until auth succeeds, then writes cookie to file.

Usage:
  python3 scripts/login.py --output /path/to/cookie.txt [--timeout 900]

Exit codes:
  0  success
  1  timeout / auth not achieved
  2  playwright not installed
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PLATFORM_URL = "https://platform.deepseek.com/usage"
PROBE_URL = "https://platform.deepseek.com/api/v0/users/get_user_summary"
POLL_MS = 1500


def _cookie_header(cookies: list[dict]) -> str:
    parts = []
    for c in cookies:
        domain = c.get("domain", "")
        if "deepseek.com" not in domain:
            continue
        name, value = c.get("name"), c.get("value")
        if name and value is not None:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def _is_auth_ok(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    code = payload.get("code")
    msg = str(payload.get("msg") or "").lower()
    if code == 40002 or "missing token" in msg:
        return False
    if code == 0:
        return True
    biz = payload.get("data", {})
    return isinstance(biz, dict) and bool(biz)


def _wait_for_auth(page, deadline: float) -> bool:
    js = """async (url) => {
        try {
            const r = await fetch(url, { credentials: 'include', headers: { Accept: 'application/json' } });
            return await r.json();
        } catch (e) {
            return { _fetchError: String(e) };
        }
    }"""
    while time.time() < deadline:
        try:
            result = page.evaluate(js, PROBE_URL)
        except Exception:
            page.wait_for_timeout(POLL_MS)
            continue
        if isinstance(result, dict) and result.get("_fetchError"):
            page.wait_for_timeout(POLL_MS)
            continue
        if _is_auth_ok(result):
            return True
        page.wait_for_timeout(POLL_MS)
    return False


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

    deadline = time.time() + args.timeout
    print(
        f"Opening Chromium. Log in to platform.deepseek.com in the browser window.\n"
        f"Waiting up to {args.timeout}s for successful auth…",
        file=sys.stderr,
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(locale="zh-CN")
        page = context.new_page()
        page.goto(PLATFORM_URL, wait_until="domcontentloaded")

        ok = _wait_for_auth(page, deadline)
        if not ok:
            browser.close()
            print("Timeout: auth not achieved within time limit.", file=sys.stderr)
            return 1

        cookies = context.cookies()
        browser.close()

    cookie_str = _cookie_header(cookies)
    if not cookie_str:
        print("No deepseek.com cookies found after login.", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(cookie_str, encoding="utf-8")
    print(f"Cookie saved to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 验证脚本帮助信息**

```bash
python3 scripts/login.py --help
```

Expected: 打印 usage，无报错

- [ ] **Step 3: Commit**

```bash
git add scripts/login.py
git commit -m "feat: add login.py Playwright cookie helper"
```

---

## Task 5：QML 主组件 — DeepSeekWidget.qml（状态 + 数据层）

**Files:**
- Create: `DeepSeekWidget.qml`（本 Task 实现状态管理、定时刷新、数据解析；UI 在 Task 6）

- [ ] **Step 1: 创建 DeepSeekWidget.qml — 状态与逻辑部分**

```qml
import QtQuick
import Quickshell
import qs.Common
import qs.Services
import qs.Widgets
import qs.Modules.Plugins

PluginComponent {
    id: root

    // ── 脚本路径 ──────────────────────────────────────────────
    readonly property string _pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")
    readonly property string _fetchScript: _pluginDir + "scripts/fetch.py"
    readonly property string _loginScript: _pluginDir + "scripts/login.py"
    readonly property string _cookieFile:  _pluginDir + "cookie.txt"

    // ── i18n ──────────────────────────────────────────────────
    property var tr: ({})
    function _loadI18n() {
        const locale = String(pluginData.locale || "zh_CN")
        const url = Qt.resolvedUrl("./i18n/" + locale + ".json")
        const xhr = new XMLHttpRequest()
        xhr.open("GET", url, false)
        xhr.send()
        try { tr = JSON.parse(xhr.responseText) } catch(e) { tr = {} }
    }
    Component.onCompleted: _loadI18n()
    Connections {
        target: root
        function onPluginDataChanged() { root._loadI18n() }
    }

    // ── 状态 ──────────────────────────────────────────────────
    property string cookieStatus: "missing"   // "ok" | "expired" | "missing"
    property bool   loginRunning: false
    property bool   fetchRunning: false
    property string lastFetchTime: ""
    property string lastError: ""

    // 余额
    property string balanceNormal: "—"
    property string balanceBonus:  "0"
    property string balanceCurrency: "CNY"
    property string tokenEstimation: "—"

    // 本月
    property int    curYear: 0
    property int    curMonth: 0
    property int    inputTokens: 0
    property int    outputTokens: 0
    property string monthlyCost: "—"
    property string monthlyTokenUsage: "—"

    // 历史（array of {year,month,inputTokens,outputTokens,cost}）
    property var history: pluginData.history || []

    // ── 工具函数 ──────────────────────────────────────────────
    function _fmtTokens(n) {
        const v = Number(n)
        if (!isFinite(v)) return "—"
        if (v >= 1e9) return (v / 1e9).toFixed(1) + "B"
        if (v >= 1e6) return (v / 1e6).toFixed(1) + "M"
        if (v >= 1e3) return (v / 1e3).toFixed(0) + "K"
        return String(v)
    }

    function _fmtCurrency(s) {
        const v = parseFloat(s)
        if (!isFinite(v)) return "—"
        return "¥ " + v.toFixed(2)
    }

    function _cookieFileExists() {
        const xhr = new XMLHttpRequest()
        xhr.open("GET", Qt.resolvedUrl("./cookie.txt"), false)
        try { xhr.send(); return xhr.status === 200 && xhr.responseText.trim().length > 0 }
        catch(e) { return false }
    }

    // ── 解析 fetch.py 输出 ────────────────────────────────────
    function _parseFetchOutput(stdout, exitCode) {
        fetchRunning = false
        let o = null
        try { o = JSON.parse(String(stdout || "").trim()) } catch(e) {}

        if (!o) {
            lastError = (tr.fetchError || "Fetch failed") + (exitCode !== 0 ? " (exit " + exitCode + ")" : "")
            return
        }

        if (o.authExpired) {
            cookieStatus = "expired"
        } else if (cookieStatus !== "missing") {
            cookieStatus = "ok"
        }

        if (o.error) lastError = o.error

        // Balance
        if (o.balance) {
            const b = o.balance
            balanceCurrency  = b.currency || "CNY"
            balanceNormal    = _fmtCurrency(b.normal)
            balanceBonus     = _fmtCurrency(b.bonus)
            tokenEstimation  = _fmtTokens(b.tokenEstimation)
            monthlyTokenUsage = _fmtTokens(b.monthlyTokenUsage)
            const costs = b.monthlyCosts || []
            if (costs.length > 0) monthlyCost = _fmtCurrency(costs[0].amount)
        }

        // Current month
        if (o.current) {
            const c = o.current
            curYear       = c.year  || 0
            curMonth      = c.month || 0
            inputTokens   = c.inputTokens  || 0
            outputTokens  = c.outputTokens || 0
            if (c.cost) monthlyCost = _fmtCurrency(c.cost)
        }

        // History — merge into pluginData
        if (o.history && Array.isArray(o.history)) {
            const merged = _mergeHistory(pluginData.history || [], o.history)
            root.history = merged
            savePluginData({ history: merged })
        }

        const now = new Date()
        lastFetchTime = now.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    }

    function _mergeHistory(existing, incoming) {
        const map = {}
        for (const e of existing) map[e.year + "-" + e.month] = e
        for (const e of incoming)  map[e.year + "-" + e.month] = e
        return Object.values(map).sort((a, b) => a.year !== b.year ? a.year - b.year : a.month - b.month)
    }

    // ── 数据拉取 ──────────────────────────────────────────────
    function refreshAll() {
        if (fetchRunning) return
        if (!_cookieFileExists()) {
            cookieStatus = "missing"
            return
        }
        fetchRunning = true
        const months = Number(pluginData.historyMonths) || 3
        Proc.runCommand(
            "deepseekWidget.fetch",
            ["python3", _fetchScript, "--cookie-file", _cookieFile, "--months", String(months)],
            (stdout, exitCode) => _parseFetchOutput(stdout, exitCode),
            120
        )
    }

    // ── 登录助手 ──────────────────────────────────────────────
    function launchLogin() {
        if (loginRunning) return
        loginRunning = true
        if (typeof ToastService !== "undefined")
            ToastService.showInfo(tr.loginRunning || "Browser opened…")
        Proc.runCommand(
            "deepseekWidget.login",
            ["python3", _loginScript, "--output", _cookieFile, "--timeout", "900"],
            (stdout, exitCode) => {
                loginRunning = false
                if (exitCode === 2) {
                    if (typeof ToastService !== "undefined")
                        ToastService.showError("playwright not installed — run: pip install -r scripts/requirements.txt && playwright install chromium")
                    return
                }
                if (exitCode !== 0) {
                    if (typeof ToastService !== "undefined")
                        ToastService.showError("Login timed out or failed (exit " + exitCode + ")")
                    return
                }
                cookieStatus = "ok"
                if (typeof ToastService !== "undefined")
                    ToastService.showInfo("Cookie saved — refreshing…")
                refreshAll()
            },
            960
        )
    }

    // ── 定时器 ────────────────────────────────────────────────
    Timer {
        id: refreshTimer
        interval: Math.max(60, Number(pluginData.refreshSeconds) || 300) * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refreshAll()
    }
```

> 注意：此步骤只创建状态层，UI 组件（horizontalBarPill、popoutContent）在 Task 6 追加。文件尚不完整，不要运行 sync.sh。

- [ ] **Step 2: Commit（中间状态，标记 WIP）**

```bash
git add DeepSeekWidget.qml
git commit -m "wip: DeepSeekWidget state layer (no UI yet)"
```

---

## Task 6：QML 主组件 — DeepSeekWidget.qml（UI 层）

**Files:**
- Modify: `DeepSeekWidget.qml`（追加 bar pill、popout、Canvas 图表）

- [ ] **Step 1: 追加 horizontalBarPill 到 DeepSeekWidget.qml（在 Task 5 的 Timer 之后、PluginComponent 闭括号之前）**

```qml
    // ── Bar Pill ──────────────────────────────────────────────
    horizontalBarPill: Component {
        StyledRect {
            implicitWidth: pillRow.implicitWidth + Theme.spacingM * 2
            height: parent.widgetThickness
            radius: Theme.cornerRadius
            color: Theme.surfaceContainerHigh
            clip: true

            Row {
                id: pillRow
                anchors.centerIn: parent
                spacing: Theme.spacingS

                // DeepSeek Logo SVG
                Item {
                    width: 16; height: 16
                    anchors.verticalCenter: parent.verticalCenter
                    Canvas {
                        anchors.fill: parent
                        onPaint: {
                            const ctx = getContext("2d")
                            ctx.clearRect(0, 0, 16, 16)
                            // Outer circle
                            ctx.beginPath()
                            ctx.arc(8, 8, 7.5, 0, Math.PI * 2)
                            ctx.fillStyle = "#4D6BFF"
                            ctx.fill()
                            // Left eye white
                            ctx.beginPath()
                            ctx.arc(5.5, 7.5, 2.2, 0, Math.PI * 2)
                            ctx.fillStyle = "white"
                            ctx.fill()
                            // Right eye white
                            ctx.beginPath()
                            ctx.arc(10.5, 7.5, 2.2, 0, Math.PI * 2)
                            ctx.fillStyle = "white"
                            ctx.fill()
                            // Left pupil
                            ctx.beginPath()
                            ctx.arc(5.5, 7.5, 1.0, 0, Math.PI * 2)
                            ctx.fillStyle = "#4D6BFF"
                            ctx.fill()
                            // Right pupil
                            ctx.beginPath()
                            ctx.arc(10.5, 7.5, 1.0, 0, Math.PI * 2)
                            ctx.fillStyle = "#4D6BFF"
                            ctx.fill()
                            // Smile arc
                            ctx.beginPath()
                            ctx.arc(8, 8, 3.5, 0.2 * Math.PI, 0.8 * Math.PI)
                            ctx.strokeStyle = "white"
                            ctx.lineWidth = 1.2
                            ctx.lineCap = "round"
                            ctx.stroke()
                        }
                    }
                }

                StyledText {
                    text: root.balanceNormal
                    font.pixelSize: Theme.fontSizeSmall
                    color: Theme.primary
                    anchors.verticalCenter: parent.verticalCenter
                }

                StyledText {
                    text: "|"
                    font.pixelSize: Theme.fontSizeSmall
                    color: Theme.surfaceVariantText
                    anchors.verticalCenter: parent.verticalCenter
                }

                StyledText {
                    text: root.monthlyTokenUsage !== "—" ? root.monthlyTokenUsage : (root.cookieStatus === "missing" ? "—" : (root.fetchRunning ? "…" : "—"))
                    font.pixelSize: Theme.fontSizeSmall
                    color: Theme.surfaceText
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }
    }
```

- [ ] **Step 2: 追加 popoutContent（合并数据卡片 + 登录区）**

```qml
    // ── Popout ────────────────────────────────────────────────
    popoutWidth: 420
    popoutHeight: 660

    popoutContent: Component {
        PopoutComponent {
            headerText: tr.pluginTitle || "DeepSeek 用量"
            showCloseButton: true

            Column {
                width: parent.width
                spacing: Theme.spacingS

                // 认证告警条
                StyledRect {
                    width: parent.width
                    height: authHintText.implicitHeight + Theme.spacingS * 2
                    visible: root.cookieStatus === "expired"
                    radius: Theme.cornerRadius
                    color: Qt.rgba(Theme.error.r, Theme.error.g, Theme.error.b, 0.12)
                    border.width: 1
                    border.color: Theme.error

                    StyledText {
                        id: authHintText
                        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: Theme.spacingS }
                        text: tr.authExpiredHint || "Session expired"
                        font.pixelSize: Theme.fontSizeSmall
                        color: Theme.error
                        wrapMode: Text.WordWrap
                    }
                }

                // 登录按钮
                StyledRect {
                    width: parent.width
                    height: 44
                    radius: Theme.cornerRadius
                    color: loginArea.containsMouse ? Theme.surfaceContainer : Theme.surfaceContainerHigh
                    border.width: 1
                    border.color: Theme.outline
                    opacity: root.loginRunning ? 0.6 : 1

                    Row {
                        anchors { fill: parent; margins: Theme.spacingS }
                        spacing: Theme.spacingS

                        DankIcon {
                            name: "vpn_key"
                            size: Theme.fontSizeMedium
                            color: Theme.primary
                            anchors.verticalCenter: parent.verticalCenter
                        }

                        StyledText {
                            width: parent.width - 32 - Theme.spacingS * 3
                            text: root.loginRunning
                                ? (tr.loginRunning || "Browser opened…")
                                : (tr.relogin || "Re-login Platform")
                            font.pixelSize: Theme.fontSizeSmall
                            font.weight: Font.Medium
                            color: Theme.surfaceText
                            anchors.verticalCenter: parent.verticalCenter
                            wrapMode: Text.WordWrap
                        }
                    }

                    MouseArea {
                        id: loginArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        enabled: !root.loginRunning
                        onClicked: root.launchLogin()
                    }
                }

                // 合并数据卡片
                StyledRect {
                    width: parent.width
                    height: dataCol.implicitHeight + Theme.spacingM * 2
                    radius: Theme.cornerRadius
                    color: Theme.surfaceContainerHigh

                    Column {
                        id: dataCol
                        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: Theme.spacingM }
                        spacing: Theme.spacingS

                        StyledText {
                            text: root.curYear > 0
                                ? (root.curYear + "-" + (root.curMonth < 10 ? "0" + root.curMonth : root.curMonth) + " UTC")
                                : "—"
                            font.pixelSize: Theme.fontSizeSmall
                            color: Theme.surfaceVariantText
                        }

                        Row {
                            width: parent.width
                            spacing: 0

                            Repeater {
                                model: [
                                    { label: tr.balance || "余额",         value: root.balanceNormal,    color: Theme.primary },
                                    { label: tr.thisMonthInput || "本月输入", value: root._fmtTokens(root.inputTokens),   color: "#89dceb" },
                                    { label: tr.thisMonthOutput || "本月输出", value: root._fmtTokens(root.outputTokens), color: "#cba6f7" },
                                    { label: tr.thisMonthCost || "本月花费",  value: root.monthlyCost,    color: "#f9e2af" }
                                ]

                                delegate: Column {
                                    width: parent.width / 4
                                    spacing: 2

                                    StyledText {
                                        text: modelData.value
                                        font.pixelSize: Theme.fontSizeMedium
                                        font.weight: Font.Bold
                                        color: modelData.color
                                        elide: Text.ElideRight
                                        width: parent.width - 4
                                    }

                                    StyledText {
                                        text: modelData.label
                                        font.pixelSize: Theme.fontSizeSmall - 2
                                        color: Theme.surfaceVariantText
                                    }
                                }
                            }
                        }

                        // 赠金（仅在 > 0 时显示）
                        StyledText {
                            visible: parseFloat(root.balanceBonus.replace("¥ ", "")) > 0
                            text: (tr.bonusBalance || "赠金") + ": " + root.balanceBonus
                            font.pixelSize: Theme.fontSizeSmall - 1
                            color: Theme.surfaceVariantText
                        }
                    }
                }
```

- [ ] **Step 3: 追加 Canvas 趋势图**

```qml
                // 趋势图
                StyledRect {
                    width: parent.width
                    height: 160
                    radius: Theme.cornerRadius
                    color: Theme.surfaceContainerHigh

                    Column {
                        anchors { fill: parent; margins: Theme.spacingM }
                        spacing: Theme.spacingXS

                        StyledText {
                            text: (tr.tokenTrend || "近 %1 月 Token 趋势").replace("%1", String(pluginData.historyMonths || 3))
                            font.pixelSize: Theme.fontSizeSmall
                            color: Theme.surfaceVariantText
                        }

                        Canvas {
                            id: trendCanvas
                            width: parent.width
                            height: 110

                            property var chartData: {
                                const hist = root.history || []
                                const cur = root.curYear > 0 ? [{
                                    year: root.curYear, month: root.curMonth,
                                    inputTokens: root.inputTokens, outputTokens: root.outputTokens,
                                    cost: root.monthlyCost.replace("¥ ", "")
                                }] : []
                                return [...hist, ...cur].slice(-(Number(pluginData.historyMonths) || 3))
                            }

                            onChartDataChanged: requestPaint()
                            onWidthChanged: requestPaint()

                            onPaint: {
                                const ctx = getContext("2d")
                                ctx.clearRect(0, 0, width, height)

                                const data = chartData
                                if (!data || data.length === 0) return

                                const PAD_L = 36, PAD_R = 8, PAD_T = 8, PAD_B = 20
                                const chartW = width - PAD_L - PAD_R
                                const chartH = height - PAD_T - PAD_B
                                const n = data.length
                                const barW = Math.min(32, chartW / n * 0.55)
                                const gap  = chartW / n

                                // Max value for Y scale
                                let maxTotal = 1
                                for (const d of data) {
                                    const t = (d.inputTokens || 0) + (d.outputTokens || 0)
                                    if (t > maxTotal) maxTotal = t
                                }

                                // Grid lines
                                ctx.strokeStyle = Qt.rgba(1,1,1,0.08)
                                ctx.lineWidth = 0.5
                                for (let i = 0; i <= 3; i++) {
                                    const y = PAD_T + chartH * (1 - i / 3)
                                    ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(width - PAD_R, y); ctx.stroke()
                                }

                                // Y labels
                                ctx.fillStyle = Qt.rgba(1,1,1,0.35)
                                ctx.font = "9px sans-serif"
                                ctx.textAlign = "right"
                                for (let i = 0; i <= 3; i++) {
                                    const v = maxTotal * i / 3
                                    const lbl = v >= 1e6 ? (v/1e6).toFixed(1)+"M" : v >= 1e3 ? (v/1e3).toFixed(0)+"K" : String(Math.round(v))
                                    const y = PAD_T + chartH * (1 - i / 3) + 3
                                    ctx.fillText(lbl, PAD_L - 3, y)
                                }

                                // Bars + cost line points
                                const costPts = []
                                let maxCost = 0.01
                                for (const d of data) { const c = parseFloat(d.cost) || 0; if (c > maxCost) maxCost = c }

                                for (let i = 0; i < n; i++) {
                                    const d = data[i]
                                    const cx = PAD_L + gap * i + gap / 2
                                    const totalH = chartH * ((d.inputTokens + d.outputTokens) / maxTotal)
                                    const outH   = chartH * ((d.outputTokens || 0) / maxTotal)
                                    const inH    = totalH - outH
                                    const barX   = cx - barW / 2
                                    const baseY  = PAD_T + chartH

                                    // Output segment (bottom, purple)
                                    ctx.fillStyle = "#cba6f7"
                                    ctx.globalAlpha = 0.8
                                    ctx.beginPath()
                                    ctx.roundRect(barX, baseY - outH, barW, outH, [0, 0, 2, 2])
                                    ctx.fill()

                                    // Input segment (top, teal)
                                    ctx.fillStyle = "#89dceb"
                                    ctx.beginPath()
                                    ctx.roundRect(barX, baseY - totalH, barW, inH, [2, 2, 0, 0])
                                    ctx.fill()
                                    ctx.globalAlpha = 1.0

                                    // X label
                                    ctx.fillStyle = Qt.rgba(1,1,1,0.35)
                                    ctx.textAlign = "center"
                                    ctx.font = "8px sans-serif"
                                    ctx.fillText(String(d.month).padStart(2, "0"), cx, height - 3)

                                    // Cost point Y
                                    const costV = parseFloat(d.cost) || 0
                                    costPts.push({ x: cx, y: PAD_T + chartH * (1 - costV / maxCost) })
                                }

                                // Cost line (yellow dashed)
                                if (costPts.length >= 2) {
                                    ctx.strokeStyle = "#f9e2af"
                                    ctx.lineWidth = 2
                                    ctx.lineCap = "round"
                                    ctx.lineJoin = "round"
                                    ctx.setLineDash([4, 3])
                                    ctx.beginPath()
                                    ctx.moveTo(costPts[0].x, costPts[0].y)
                                    for (let i = 1; i < costPts.length; i++) ctx.lineTo(costPts[i].x, costPts[i].y)
                                    ctx.stroke()
                                    ctx.setLineDash([])
                                    for (const pt of costPts) {
                                        ctx.beginPath(); ctx.arc(pt.x, pt.y, 3, 0, Math.PI * 2)
                                        ctx.fillStyle = "#f9e2af"; ctx.fill()
                                    }
                                }
                            }
                        }

                        // 图例
                        Row {
                            spacing: Theme.spacingM

                            Row {
                                spacing: 4
                                Rectangle { width: 10; height: 8; radius: 1; color: "#89dceb"; opacity: 0.8; anchors.verticalCenter: parent.verticalCenter }
                                StyledText { text: tr.inputTokens || "输入"; font.pixelSize: Theme.fontSizeSmall - 1; color: Theme.surfaceVariantText }
                            }
                            Row {
                                spacing: 4
                                Rectangle { width: 10; height: 8; radius: 1; color: "#cba6f7"; opacity: 0.8; anchors.verticalCenter: parent.verticalCenter }
                                StyledText { text: tr.outputTokens || "输出"; font.pixelSize: Theme.fontSizeSmall - 1; color: Theme.surfaceVariantText }
                            }
                            Row {
                                spacing: 4
                                Canvas {
                                    width: 14; height: 8
                                    onPaint: {
                                        const ctx = getContext("2d")
                                        ctx.strokeStyle = "#f9e2af"; ctx.lineWidth = 2
                                        ctx.setLineDash([3,2])
                                        ctx.beginPath(); ctx.moveTo(0,4); ctx.lineTo(14,4); ctx.stroke()
                                        ctx.setLineDash([])
                                        ctx.beginPath(); ctx.arc(7,4,2,0,Math.PI*2)
                                        ctx.fillStyle = "#f9e2af"; ctx.fill()
                                    }
                                }
                                StyledText { text: tr.costLabel || "花费 (¥)"; font.pixelSize: Theme.fontSizeSmall - 1; color: Theme.surfaceVariantText }
                            }
                        }
                    }
                }
```

- [ ] **Step 4: 追加底部操作行 + 关闭 Column 和 PopoutComponent**

```qml
                // 底部操作行
                Row {
                    width: parent.width
                    spacing: Theme.spacingS

                    Repeater {
                        model: [
                            { label: tr.usagePage   || "Usage",    url: "https://platform.deepseek.com/usage"      },
                            { label: tr.monitorPage || "监控",      url: "https://console.deepseek.com/monitoring"  },
                            { label: tr.apiKeysPage || "API Keys", url: "https://platform.deepseek.com/api_keys"   },
                        ]
                        delegate: StyledRect {
                            width: (parent.width - Theme.spacingS * 2) / 3
                            height: 34
                            radius: Theme.cornerRadius
                            color: linkArea.containsMouse ? Theme.surfaceContainer : Theme.surfaceContainerHigh
                            StyledText { anchors.centerIn: parent; text: modelData.label; font.pixelSize: Theme.fontSizeSmall; color: Theme.surfaceText }
                            MouseArea { id: linkArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: Quickshell.execDetached(["xdg-open", modelData.url]) }
                        }
                    }
                }

                // 刷新按钮
                StyledRect {
                    width: parent.width
                    height: 34
                    radius: Theme.cornerRadius
                    color: refreshArea.containsMouse ? Theme.primary : Theme.surfaceContainerHigh
                    opacity: root.fetchRunning ? 0.6 : 1
                    StyledText { anchors.centerIn: parent; text: root.fetchRunning ? "…" : (tr.refresh || "刷新"); font.pixelSize: Theme.fontSizeSmall; color: refreshArea.containsMouse ? Theme.onPrimary : Theme.surfaceText }
                    MouseArea { id: refreshArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; enabled: !root.fetchRunning; onClicked: root.refreshAll() }
                }

            } // Column
        } // PopoutComponent
    } // popoutContent

} // PluginComponent
```

- [ ] **Step 5: 运行 sync.sh 并确认插件目录文件完整**

```bash
bash sync.sh
ls ~/.config/DankMaterialShell/plugins/DeepSeekWidget/
```

Expected: 看到 `DeepSeekWidget.qml`, `plugin.json`, `i18n/`, `scripts/`

- [ ] **Step 6: Commit**

```bash
git add DeepSeekWidget.qml
git commit -m "feat: DeepSeekWidget UI — bar pill, popout, canvas trend chart"
```

---

## Task 7：QML 设置面板 — DeepSeekSettings.qml

**Files:**
- Create: `DeepSeekSettings.qml`

- [ ] **Step 1: 创建 DeepSeekSettings.qml**

```qml
import QtQuick
import qs.Common
import qs.Modules.Plugins
import qs.Widgets

PluginSettings {
    id: root
    pluginId: "deepseekWidget"

    // i18n（与 widget 保持一致，从 pluginData.locale 读取）
    property var tr: ({})
    function _loadI18n() {
        const locale = String(pluginData.locale || "zh_CN")
        const url = Qt.resolvedUrl("./i18n/" + locale + ".json")
        const xhr = new XMLHttpRequest()
        xhr.open("GET", url, false)
        try { xhr.send(); tr = JSON.parse(xhr.responseText) } catch(e) { tr = {} }
    }
    Component.onCompleted: _loadI18n()

    // ── 标题 ──────────────────────────────────────────────────
    StyledText {
        width: parent.width
        text: tr.pluginTitle || "DeepSeek 用量"
        font.pixelSize: Theme.fontSizeLarge
        font.weight: Font.Bold
        color: Theme.surfaceText
    }

    StyledText {
        width: parent.width
        text: tr.notLoggedInHint || "余额与用量通过平台 Cookie 获取。点击下方「重新登录」完成授权。"
        font.pixelSize: Theme.fontSizeSmall
        color: Theme.surfaceVariantText
        wrapMode: Text.WordWrap
    }

    // ── Cookie 状态卡片 ───────────────────────────────────────
    StyledRect {
        width: parent.width
        height: cookieRow.implicitHeight + Theme.spacingM * 2
        radius: Theme.cornerRadius
        color: Theme.surfaceContainerHigh

        Row {
            id: cookieRow
            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: Theme.spacingM }
            spacing: Theme.spacingS

            // Status dot
            Rectangle {
                width: 8; height: 8; radius: 4
                anchors.verticalCenter: parent.verticalCenter
                color: {
                    const cookieFile = Qt.resolvedUrl("./cookie.txt")
                    const xhr = new XMLHttpRequest()
                    xhr.open("GET", cookieFile, false)
                    try { xhr.send(); return (xhr.status === 200 && xhr.responseText.trim().length > 0) ? "#a6e3a1" : "#f38ba8" }
                    catch(e) { return "#6c7086" }
                }
            }

            Column {
                spacing: 2
                StyledText {
                    text: {
                        const cookieFile = Qt.resolvedUrl("./cookie.txt")
                        const xhr = new XMLHttpRequest()
                        xhr.open("GET", cookieFile, false)
                        try { xhr.send(); return (xhr.status === 200 && xhr.responseText.trim().length > 0) ? (tr.cookieStatusOk || "Cookie 有效") : (tr.cookieStatusMissing || "未设置 Cookie") }
                        catch(e) { return tr.cookieStatusMissing || "未设置 Cookie" }
                    }
                    font.pixelSize: Theme.fontSizeSmall
                    font.weight: Font.Medium
                    color: Theme.surfaceText
                }
                StyledText {
                    text: "cookie.txt"
                    font.pixelSize: Theme.fontSizeSmall - 1
                    color: Theme.surfaceVariantText
                }
            }
        }
    }

    // ── 刷新间隔 ──────────────────────────────────────────────
    SelectionSetting {
        settingKey: "refreshSeconds"
        label: tr.refreshInterval || "刷新间隔"
        description: ""
        options: [
            { label: tr.min1  || "1 分钟",  value: "60"   },
            { label: tr.min5  || "5 分钟",  value: "300"  },
            { label: tr.min15 || "15 分钟", value: "900"  },
            { label: tr.min30 || "30 分钟", value: "1800" }
        ]
        defaultValue: "300"
    }

    // ── 历史月数 ──────────────────────────────────────────────
    SelectionSetting {
        settingKey: "historyMonths"
        label: tr.historyMonths || "趋势图历史月数"
        description: ""
        options: [
            { label: tr.month1 || "1 个月", value: "1" },
            { label: tr.month3 || "3 个月", value: "3" },
            { label: tr.month6 || "6 个月", value: "6" }
        ]
        defaultValue: "3"
    }

    // ── 语言 ──────────────────────────────────────────────────
    SelectionSetting {
        settingKey: "locale"
        label: tr.language || "显示语言"
        description: ""
        options: [
            { label: "中文", value: "zh_CN" },
            { label: "English", value: "en_US" }
        ]
        defaultValue: "zh_CN"
    }

    // ── 前置依赖说明 ──────────────────────────────────────────
    StyledText {
        width: parent.width
        text: tr.prereqTitle || "前置依赖"
        font.pixelSize: Theme.fontSizeMedium
        font.weight: Font.Bold
        color: Theme.surfaceText
    }

    StyledRect {
        width: parent.width
        height: prereqCol.implicitHeight + Theme.spacingM * 2
        radius: Theme.cornerRadius
        color: Theme.surfaceContainer

        Column {
            id: prereqCol
            anchors { left: parent.left; right: parent.right; margins: Theme.spacingM; verticalCenter: parent.verticalCenter }
            spacing: Theme.spacingXS

            Repeater {
                model: [
                    "cd ~/.config/DankMaterialShell/plugins/DeepSeekWidget",
                    "pip install -r scripts/requirements.txt",
                    "playwright install chromium"
                ]
                delegate: StyledText {
                    width: parent.width
                    text: (index + 1) + ".  " + modelData
                    font.pixelSize: Theme.fontSizeSmall
                    font.family: "monospace"
                    color: Theme.primary
                    wrapMode: Text.WrapAnywhere
                }
            }
        }
    }
}
```

- [ ] **Step 2: 运行 sync.sh**

```bash
bash sync.sh
```

- [ ] **Step 3: Commit**

```bash
git add DeepSeekSettings.qml
git commit -m "feat: DeepSeekSettings panel with cookie status, intervals, i18n"
```

---

## Task 8：最终集成 — 同步、启用插件、验收

**Files:**
- Modify: `~/.config/DankMaterialShell/plugin_settings.json`（启用 deepseekWidget）

- [ ] **Step 1: 确认 plugin_settings.json 中 deepseekWidget 已启用**

打开 `~/.config/DankMaterialShell/plugin_settings.json`，确认包含：
```json
"deepseekWidget": { "enabled": true }
```
若无，手动添加。

- [ ] **Step 2: 确认 settings.json barConfigs 中包含 deepseekWidget**

检查 `~/.config/DankMaterialShell/settings.json` 的 `centerWidgets` 数组，确认有：
```json
{ "id": "deepseekWidget", "enabled": true }
```

- [ ] **Step 3: 安装 Python 依赖**

```bash
cd ~/.config/DankMaterialShell/plugins/DeepSeekWidget
pip install -r scripts/requirements.txt
playwright install chromium
```

- [ ] **Step 4: 手动测试 fetch.py（无 cookie 时应输出错误 JSON）**

```bash
python3 scripts/fetch.py --cookie-file /nonexistent/cookie.txt --months 3
```

Expected output:
```json
{"ok": false, "error": "cookie file not found: /nonexistent/cookie.txt", "authExpired": false}
```

- [ ] **Step 5: 手动测试 login.py 帮助信息**

```bash
python3 scripts/login.py --help
```

Expected: usage 输出，无报错

- [ ] **Step 6: 重载 DMS（通知 Quickshell 重新加载插件）**

在 DMS 设置中点击「重新加载插件」或重启 Quickshell：
```bash
# 若 DMS 支持 quickshell reload：
qs --reload 2>/dev/null || echo "请在 DMS 设置中手动重载"
```

- [ ] **Step 7: 验证 bar pill 出现在顶栏**

顶栏应出现含 DeepSeek Logo 的 pill，点击应弹出 popout。Cookie 尚未设置时应显示「未登录」。

- [ ] **Step 8: 点击「重新登录 Platform」完成首次登录**

点击 popout 的重新登录按钮，Chromium 窗口弹出，完成登录后窗口自动关闭，pill 开始显示数据。

- [ ] **Step 9: Final commit**

```bash
cd /home/gylove1994/code-spaces/deepseek-api-widget
git add -A
git commit -m "feat: complete DeepSeek usage widget v1.0.0"
```

---

## 自检记录

**Spec coverage:**
- [x] Bar pill: DeepSeek Logo + 余额 + token 总量（Task 6）
- [x] Popout: 告警条 + 登录按钮 + 合并数据卡片 + 趋势图 + 快捷链接 + 刷新（Task 6）
- [x] 堆叠柱（输入/输出）+ 黄虚线（花费）（Task 6 Step 3）
- [x] 设置页: Cookie 状态 + 刷新间隔 + 历史月数 + 语言 + 依赖说明（Task 7）
- [x] i18n zh_CN / en_US（Task 2）
- [x] fetch.py: get_user_summary + amount + cost 并发（Task 3）
- [x] login.py: Playwright 登录 + cookie.txt 输出（Task 4）
- [x] Cookie 文件存储（Task 3/4）
- [x] pluginData history 持久化（Task 5）
- [x] 定时刷新 + 手动刷新（Task 5）
- [x] 旧插件删除 + sync.sh（Task 1）
- [x] 错误处理: missing cookie / authExpired / network error（Task 5/6）

**Type consistency:** `_fmtTokens` / `_fmtCurrency` 在 Task 5 定义，Task 6 中使用 — 一致。`launchLogin` 在 Task 5 定义，Task 6 调用 — 一致。`history` array schema 在 Task 3（fetch.py 输出）与 Task 5/6（QML 消费）一致。
