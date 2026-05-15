# DeepSeek 用量 Widget 设计文档

**日期：** 2026-05-15  
**项目：** deepseek-api-widget  
**目标系统：** DankMaterialShell (DMS) / Dankbar  

---

## 1. 背景与目标

在 DMS 顶栏（Dankbar）中新增一个插件 Widget，实时展示用户的 DeepSeek API 余额与平台 token 用量。由于 DeepSeek 平台用量接口（`platform.deepseek.com`）需要浏览器登录态（Cookie），无公开 API Key 可直接调用，因此需要内置 Playwright Chromium 登录助手，在 Cookie 失效时引导用户重新登录并自动保存凭证。

---

## 2. 开发工作流

- **源码位置：** `/home/gylove1994/code-spaces/deepseek-api-widget/`
- **部署目标：** `~/.config/DankMaterialShell/plugins/DeepSeekWidget/`
- **同步方式：** `sync.sh` 脚本将源码复制或 symlink 到 DMS 插件目录
- **删除旧插件：** 移除 `~/.config/DankMaterialShell/plugins/DeepSeekUsageWidget/`，并从 `settings.json` 的 `barConfigs` 中移除旧 `deepseekUsage` 条目，替换为新 `deepseekWidget` 条目

---

## 3. 文件结构

```
deepseek-api-widget/
├── plugin.json                    # 插件清单
├── DeepSeekWidget.qml             # 主 PluginComponent
├── DeepSeekSettings.qml           # 设置面板 PluginSettings
├── i18n/
│   ├── zh_CN.json                 # 中文（默认）
│   └── en_US.json                 # 英文
├── scripts/
│   ├── fetch.py                   # 数据拉取脚本
│   ├── login.py                   # Playwright 登录助手
│   └── requirements.txt           # playwright
├── sync.sh                        # 同步到 DMS 插件目录
└── docs/
    └── superpowers/specs/
        └── 2026-05-15-deepseek-usage-widget-design.md
```

---

## 4. 插件清单（plugin.json）

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

---

## 5. 架构：QML 原生驱动 + Python 辅助脚本

### 5.1 状态管理（QML）

| 属性 | 类型 | 说明 |
|------|------|------|
| `balance` | string | 余额显示字符串，如 `¥ 12.50` |
| `monthlyInputTokens` | int | 本月输入 token 数 |
| `monthlyOutputTokens` | int | 本月输出 token 数 |
| `monthlyCost` | real | 本月花费（¥） |
| `history` | array | 历史月度数据，存于 `pluginData` |
| `cookieStatus` | string | `"ok"` / `"expired"` / `"missing"` |
| `loginRunning` | bool | Playwright 登录进程是否运行中 |
| `fetchRunning` | bool | 数据拉取进程是否运行中 |
| `lastFetchTime` | string | 上次成功拉取时间 |
| `lastError` | string | 最近错误信息 |

### 5.2 定时刷新

- QML `Timer`：`interval = pluginData.refreshSeconds * 1000`，`triggeredOnStart: true`
- 刷新时调用 `Proc.runCommand("deepseekWidget.fetch", ...)`
- Popout 内「↻ 刷新」按钮可立即触发

### 5.3 i18n

- `i18n/zh_CN.json` 和 `en_US.json` 各包含所有 UI 字符串的键值对
- QML 通过 `JSON.parse` 加载对应文件内容到 `property var tr`
- 设置中 `locale` 字段（`"zh_CN"` / `"en_US"`）控制加载哪个文件
- 所有显示文本通过 `tr.keyName` 引用，不硬编码字符串

---

## 6. Python 脚本

### 6.1 scripts/fetch.py

**职责：** 并发拉取余额 + 本月用量（amount + cost），支持拉取多月历史

**调用方式：**
```bash
python3 scripts/fetch.py \
  --cookie-file ~/.config/DankMaterialShell/plugins/DeepSeekWidget/cookie.txt \
  --months 3
```

**输出（stdout JSON）：**
```json
{
  "ok": true,
  "authExpired": false,
  "balance": {
    "currency": "CNY",
    "normal": "31.29",
    "bonus": "0.00",
    "tokenEstimation": "10430897"
  },
  "current": {
    "year": 2026, "month": 5,
    "inputTokens": 842000,
    "outputTokens": 358000,
    "monthlyTokenUsage": 386784528,
    "cost": "68.71"
  },
  "history": [
    { "year": 2026, "month": 3, "inputTokens": 400000, "outputTokens": 200000, "cost": "1.20" },
    { "year": 2026, "month": 4, "inputTokens": 560000, "outputTokens": 290000, "cost": "1.68" }
  ],
  "error": null
}
```

**接口：**
- `GET https://platform.deepseek.com/api/v0/users/get_user_summary`  
  提供余额（`normal_wallets`、`bonus_wallets`）+ 本月 token 总用量（`monthly_token_usage`）+ 本月花费（`monthly_costs`）。**这是主接口，同时满足余额和本月摘要需求。**
- `GET https://platform.deepseek.com/api/v0/usage/amount?year=Y&month=M`  
  提供本月输入/输出 token 分项（用于趋势图堆叠柱）
- `GET https://platform.deepseek.com/api/v0/usage/cost?year=Y&month=M`  
  补充花费分项明细（与 `get_user_summary` 的 `monthly_costs` 可互为验证）

三个接口均使用平台 Cookie 并发拉取。`get_user_summary` 返回的 `monthly_token_usage` 作为 pill 显示的 token 总量；`amount` 接口提供输入/输出分项用于图表堆叠柱。

**响应字段映射（get_user_summary）：**

| 字段路径 | 用途 |
|---------|------|
| `biz_data.normal_wallets[0].balance` | 普通余额（CNY） |
| `biz_data.bonus_wallets[0].balance` | 赠金余额（CNY） |
| `biz_data.total_available_token_estimation` | 可用 token 估算 |
| `biz_data.monthly_token_usage` | 本月 token 总用量（pill 显示） |
| `biz_data.monthly_costs[0].amount` | 本月花费（CNY） |

**鉴权失效判断：** 响应 `{"code": 40002, "msg": "Missing Token"}` → 设 `authExpired: true`

### 6.2 scripts/login.py

**职责：** 启动 Playwright Chromium（有头），打开 `platform.deepseek.com/usage`，轮询用量接口直到鉴权成功，输出 Cookie 字符串到 stdout

**调用方式：**
```bash
python3 scripts/login.py --timeout 900
```

**输出（stdout）：** 纯文本 Cookie Header 字符串，如：
```
intercom-id-xxx=abc; ds_session_token=eyJ...
```

**退出码：** `0` 成功，`1` 超时，`2` 缺少 playwright

**流程：**
1. 启动 Chromium（headless=False，locale=zh-CN）
2. 导航到 `platform.deepseek.com/usage`
3. 每 1.5 秒 evaluate JS fetch 用量接口，检测是否不再返回 40002
4. 成功后提取 `deepseek.com` 域 Cookie，拼为 `name=value; ...` 格式输出
5. 关闭浏览器

### 6.3 scripts/requirements.txt

```
playwright>=1.44.0
```

---

## 7. QML 组件设计

### 7.1 DeepSeekWidget.qml（PluginComponent）

**Bar Pill（horizontalBarPill）：**
- DeepSeek 官方 SVG Logo（16×16，蓝色 `#4D6BFF`）
- 余额：`¥ 12.50`（绿色）
- 分隔符：`|`
- 本月 token 总量：`1.2M`（按 K/M 格式化，绿色）

**Popout（popoutContent，宽 420px）：**

1. **标题栏：** "DeepSeek 用量" + 关闭按钮
2. **认证告警条**（`cookieStatus === "expired"` 时显示，红色左边框）
3. **重新登录按钮**（`loginRunning` 时显示进度状态）
4. **合并数据卡片：**
   - 副标题：`YYYY-MM UTC`
   - 四列：余额（`normal_wallets` CNY）/ 本月输入（`amount` 接口）/ 本月输出（`amount` 接口）/ 本月花费（`monthly_costs` CNY）
   - 赠金余额若 > 0 则在余额列下方显示小字补充
5. **趋势图（QML Canvas）：**
   - 堆叠柱：下段紫（输出）+ 上段蓝（输入）
   - 黄色虚线折线叠加花费趋势
   - X 轴：月份标签；Y 轴：动态计算最大值
   - 图例：输入 / 输出 / 花费
6. **底部操作行：** Usage / 监控 / API Keys（xdg-open）+ ↻ 刷新按钮

### 7.2 DeepSeekSettings.qml（PluginSettings）

1. **标题 + 副标题**
2. **Cookie 状态卡片：**
   - 状态指示灯（绿/红/灰）
   - 状态文字 + 上次登录时间
   - 「重新登录」按钮（触发同 widget 内的 `launchLogin()`）
3. **刷新间隔**（分段选择器，SelectionSetting）：1 / 5 / 15 / 30 分钟
4. **历史月数**（分段选择器，SelectionSetting）：1 / 3 / 6 个月
5. **语言**（分段选择器，SelectionSetting）：`zh_CN` / `en_US`
6. **前置依赖说明**（代码块样式，分步骤）

---

## 8. 数据流

```
Timer tick / 手动刷新
    │
    ▼
Proc.runCommand("deepseekWidget.fetch",
    ["python3", fetchScript, "--cookie-file", cookieFile, "--months", historyMonths])
    │
    ▼ stdout JSON
parseFetchResult(stdout, exitCode)
    ├── 更新 balance / monthlyTokens / monthlyCost
    ├── 合并 history → savePluginData({ history: [...] })
    └── authExpired=true → cookieStatus = "expired"

用户点击「重新登录」
    │
    ▼
Proc.runCommand("deepseekWidget.login",
    ["python3", loginScript, "--output", cookieFile, "--timeout", "900"])
    │
    ▼ stdout cookie string written to cookieFile by login.py
loginRunning = false
→ 触发立即刷新
```

---

## 9. 数据持久化

### 9.1 pluginData（savePluginData，DMS 设置文件）

| 键 | 类型 | 说明 |
|----|------|------|
| `refreshSeconds` | int | 刷新间隔（秒）|
| `historyMonths` | int | 历史月数 |
| `locale` | string | `"zh_CN"` / `"en_US"` |
| `history` | array | 历史月度数据（fetch 后合并保存）|

### 9.2 Cookie 文件

- 路径：`~/.config/DankMaterialShell/plugins/DeepSeekWidget/cookie.txt`
- 格式：纯文本 Cookie Header 字符串（`name=value; name2=value2`）
- 由 `login.py --output PATH` 写入；由 `fetch.py --cookie-file PATH` 读取
- Cookie 不存入 `pluginData`，避免 CLI 参数长度限制和 ps 可见问题

---

## 10. 错误处理

| 场景 | 行为 |
|------|------|
| Cookie 为空 / 未设置 | pill 显示 `—`，popout 显示「未登录」提示 |
| 鉴权过期（40002）| `cookieStatus = "expired"`，显示告警条 |
| 网络请求失败 | `lastError` 更新，pill 保留上次有效数据并添加 `!` 标记 |
| Python 脚本不存在 | toast 提示路径错误 |
| playwright 未安装 | 登录失败后 toast 提示安装命令 |
| fetch 超时（>60s）| 进程被 Proc 取消，显示超时错误 |

---

## 11. i18n 字符串键（zh_CN / en_US 对照）

| 键 | zh_CN | en_US |
|----|-------|-------|
| `balance` | 余额 | Balance |
| `thisMonthInput` | 本月输入 | Input (Mo.) |
| `thisMonthOutput` | 本月输出 | Output (Mo.) |
| `thisMonthCost` | 本月花费 | Cost (Mo.) |
| `relogin` | 重新登录 Platform | Re-login Platform |
| `authExpiredHint` | 会话已过期，请重新登录 | Session expired, please re-login |
| `loginRunning` | 浏览器已打开，完成登录后自动退出… | Browser opened, login to continue… |
| `tokenTrend` | 近 N 月 Token 趋势 | Token Trend (N Mo.) |
| `refresh` | 刷新 | Refresh |
| `notLoggedIn` | 未登录 | Not logged in |

---

## 12. sync.sh 设计

```bash
#!/usr/bin/env bash
# 将工作区同步到 DMS 插件目录
PLUGIN_ID="DeepSeekWidget"
SRC="$(cd "$(dirname "$0")" && pwd)"
DST="$HOME/.config/DankMaterialShell/plugins/$PLUGIN_ID"

# 删除旧插件
OLD_DST="$HOME/.config/DankMaterialShell/plugins/DeepSeekUsageWidget"
[ -d "$OLD_DST" ] && rm -rf "$OLD_DST" && echo "已删除旧插件: $OLD_DST"

mkdir -p "$DST"
rsync -av --exclude='.superpowers' --exclude='docs' --exclude='.git' \
  --exclude='sync.sh' "$SRC/" "$DST/"
echo "同步完成: $DST"
```

---

## 13. 不在范围内（Out of Scope）

- 自动获取 API Key（与平台 Cookie 是两套凭证）
- 多账号支持
- Windows / macOS 适配（仅 Linux + Wayland）
- Playwright Firefox / WebKit（仅 Chromium）
