import QtQuick
import Quickshell
import qs.Common
import qs.Services
import qs.Widgets
import qs.Modules.Plugins

PluginComponent {
    id: root

    // ── Script paths ───────────────────────────────────────────
    readonly property string _pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")
    readonly property string _fetchScript: _pluginDir + "scripts/fetch.py"
    readonly property string _loginScript: _pluginDir + "scripts/login.py"
    readonly property string _cookieFile:  _pluginDir + "cookie.txt"
    readonly property string _python: "python"
    // Unique command ID per instance to avoid debounce callback collisions
    readonly property string _cmdId: "deepseekWidget_" + Math.random().toString(36).slice(2, 10)

    // ── i18n ──────────────────────────────────────────────────
    property var tr: ({})
    function _loadI18n() {
        const locale = String(pluginData.locale || "en_US")
        const path = _pluginDir + "i18n/" + locale + ".json"
        Proc.runCommand(
            _cmdId + ".i18n",
            ["cat", path],
            (stdout, exitCode) => {
                if (exitCode === 0 && stdout.trim()) {
                    try { tr = JSON.parse(stdout) } catch(e) {}
                }
            },
            0, 5000
        )
    }
    Component.onCompleted: _loadI18n()
    Connections {
        target: root
        function onPluginDataChanged() { root._loadI18n() }
    }

    // ── State ──────────────────────────────────────────────────
    property string cookieStatus: "missing"   // "ok" | "expired" | "missing"
    property bool   loginRunning: false
    property bool   fetchRunning: false
    property string lastFetchTime: ""
    property string lastError: ""

    // Balance
    property string balanceNormal: "—"
    property string balanceBonus:  "0"
    property string balanceCurrency: "CNY"
    property string tokenEstimation: "—"

    // Current month
    property int    curYear: 0
    property int    curMonth: 0
    property int    inputTokens: 0
    property int    outputTokens: 0
    property string monthlyCost: "—"
    property string monthlyTokenUsage: "—"

    // History (array of {year,month,inputTokens,outputTokens,cost})
    property var history: pluginData.history || []
    // Daily (array of {day, inputTokens, outputTokens, cost, models})
    property var daily: []
    // Cached daily data per month: {"2026-7": [...], "2026-6": [...]}
    property var dailyCache: pluginData.dailyCache || {}

    // Chart state
    property string chartMode: "cost"     // "cost" | "tokens"
    property int viewYear: 0
    property int viewMonth: 0

    // ── Utility functions ─────────────────────────────────────
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

    // ── Today's cost ───────────────────────────────────────────
    function _getTodayCost() {
        const dl = daily || []
        const now = new Date()
        const today = now.getDate()
        for (const d of dl) {
            if (d.day === today && d.cost != null) return _fmtCurrency(d.cost)
        }
        return "—"
    }

    // ── Chart data helpers ──────────────────────────────────────
    function _monthKey(y, m) { return y + "-" + m }

    function _getChartData() {
        const key = _monthKey(viewYear, viewMonth)
        return dailyCache[key] || []
    }

    function _canGoPrev() {
        const keys = Object.keys(dailyCache).sort()
        const key = _monthKey(viewYear, viewMonth)
        return keys.indexOf(key) > 0
    }

    function _canGoNext() {
        const keys = Object.keys(dailyCache).sort()
        const key = _monthKey(viewYear, viewMonth)
        const idx = keys.indexOf(key)
        return idx >= 0 && idx < keys.length - 1
    }

    function _goPrevMonth() {
        const keys = Object.keys(dailyCache).sort()
        const key = _monthKey(viewYear, viewMonth)
        const idx = keys.indexOf(key)
        if (idx > 0) {
            const [y, m] = keys[idx - 1].split("-")
            viewYear = parseInt(y); viewMonth = parseInt(m)
        }
    }

    function _goNextMonth() {
        const keys = Object.keys(dailyCache).sort()
        const key = _monthKey(viewYear, viewMonth)
        const idx = keys.indexOf(key)
        if (idx >= 0 && idx < keys.length - 1) {
            const [y, m] = keys[idx + 1].split("-")
            viewYear = parseInt(y); viewMonth = parseInt(m)
        }
    }

    function _isCurrentMonth() {
        return viewYear === curYear && viewMonth === curMonth
    }

    function _getMonthTotal() {
        const data = _getChartData()
        if (!data || data.length === 0) return "—"
        let total = 0
        if (chartMode === "cost") {
            for (const d of data) total += d.cost || 0
            return _fmtCurrency(total)
        } else {
            for (const d of data) {
                const models = (d.models || []).filter(m => !m.model.toLowerCase().includes("chat"))
                for (const m of models) total += (m.inputTokens || 0) + (m.outputTokens || 0)
            }
            return _fmtTokens(total)
        }
    }

    // ── Parse fetch.py output ─────────────────────────────────
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
        } else if (!o.ok && o.error && (String(o.error).includes("not found") || String(o.error).includes("is empty"))) {
            cookieStatus = "missing"
        } else if (o.ok) {
            cookieStatus = "ok"
        }
        // Network / partial error: keep current status

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

        // Daily breakdown
        if (o.daily && Array.isArray(o.daily)) {
            root.daily = o.daily
        }

        // Merge dailyByMonth into cache
        if (o.dailyByMonth && typeof o.dailyByMonth === "object") {
            const cache = Object.assign({}, pluginData.dailyCache || {})
            for (const key of Object.keys(o.dailyByMonth)) {
                cache[key] = o.dailyByMonth[key]
            }
            root.dailyCache = cache
            savePluginData({ dailyCache: cache, history: root.history })
        }

        // Initialize viewYear/viewMonth from current data
        if (root.viewYear === 0 && root.curYear > 0) {
            root.viewYear = root.curYear
            root.viewMonth = root.curMonth
        }

        const now = new Date()
        lastFetchTime = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    }

    function _mergeHistory(existing, incoming) {
        const map = {}
        for (const e of existing) map[e.year + "-" + e.month] = e
        for (const e of incoming)  map[e.year + "-" + e.month] = e
        return Object.values(map).sort((a, b) => a.year !== b.year ? a.year - b.year : a.month - b.month)
    }

    // ── Data fetch ────────────────────────────────────────────
    function refreshAll() {
        if (fetchRunning) return
        fetchRunning = true
        const months = Number(pluginData.historyMonths) || 3
        Proc.runCommand(
            _cmdId + ".fetch",
            [_python, _fetchScript, "--cookie-file", _cookieFile, "--months", String(months)],
            (stdout, exitCode) => _parseFetchOutput(stdout, exitCode),
            50,
            120000
        )
    }

    // ── Login helper ──────────────────────────────────────────
    function launchLogin() {
        if (loginRunning) return
        loginRunning = true
        if (typeof ToastService !== "undefined")
            ToastService.showInfo(tr.loginRunning || "Browser opened…")
        Proc.runCommand(
            _cmdId + ".login",
            [_python, _loginScript, "--output", _cookieFile, "--timeout", "900"],
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
            0,
            960000
        )
    }

    // ── Timer ─────────────────────────────────────────────────
    Timer {
        id: refreshTimer
        interval: Math.max(60, Number(pluginData.refreshSeconds) || 300) * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refreshAll()
    }

    // ── Bar Pill ──────────────────────────────────────────────
    horizontalBarPill: Component {
        Row {
            spacing: Theme.spacingS

            // DeepSeek Logo (Canvas)
            /*
            Canvas {
                width: 16; height: 16
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.clearRect(0, 0, 16, 16)
                    ctx.beginPath()
                    ctx.arc(8, 8, 7.5, 0, Math.PI * 2)
                    ctx.fillStyle = "#4D6BFF"
                    ctx.fill()
                    ctx.beginPath()
                    ctx.arc(5.5, 7.5, 2.2, 0, Math.PI * 2)
                    ctx.fillStyle = "white"
                    ctx.fill()
                    ctx.beginPath()
                    ctx.arc(10.5, 7.5, 2.2, 0, Math.PI * 2)
                    ctx.fillStyle = "white"
                    ctx.fill()
                    ctx.beginPath()
                    ctx.arc(5.5, 7.5, 1.0, 0, Math.PI * 2)
                    ctx.fillStyle = "#4D6BFF"
                    ctx.fill()
                    ctx.beginPath()
                    ctx.arc(10.5, 7.5, 1.0, 0, Math.PI * 2)
                    ctx.fillStyle = "#4D6BFF"
                    ctx.fill()
                    ctx.beginPath()
                    ctx.arc(8, 8, 3.5, 0.2 * Math.PI, 0.8 * Math.PI)
                    ctx.strokeStyle = "white"
                    ctx.lineWidth = 1.2
                    ctx.lineCap = "round"
                    ctx.stroke()
                }
            }
            */

            // Not-logged-in state
            StyledText {
                visible: root.cookieStatus === "missing"
                text: root.loginRunning ? (root.tr.loading || "Loading…") : (root.tr.notLoggedIn || "Not logged in")
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.surfaceVariantText
            }

            // Logged in: balance
            StyledText {
                visible: root.cookieStatus !== "missing"
                text: root.balanceNormal
                font.pixelSize: Theme.fontSizeSmall
                color: root.cookieStatus === "expired" ? Theme.error : Theme.primary
            }

            StyledText {
                visible: root.cookieStatus !== "missing"
                text: "|"
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.surfaceVariantText
            }

            // Today's cost
            StyledText {
                visible: root.cookieStatus !== "missing"
                text: root.fetchRunning ? "…" : root._getTodayCost()
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.surfaceText
            }
        }
    }

    // ── Popout ────────────────────────────────────────────────
    popoutWidth: 420
    popoutHeight: 660

    popoutContent: Component {
        PopoutComponent {
            headerText: tr.pluginTitle || "DeepSeek Usage"
            showCloseButton: true

            Column {
                id: contentCol
                width: parent.width
                spacing: Theme.spacingS

                // Not-logged-in banner
                StyledRect {
                    width: parent.width
                    height: notLoginText.implicitHeight + Theme.spacingS * 2
                    visible: root.cookieStatus === "missing"
                    radius: Theme.cornerRadius
                    color: Qt.rgba(Theme.primary.r, Theme.primary.g, Theme.primary.b, 0.08)
                    border.width: 1
                    border.color: Qt.rgba(Theme.primary.r, Theme.primary.g, Theme.primary.b, 0.3)

                    StyledText {
                        id: notLoginText
                        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: Theme.spacingS }
                        text: tr.notLoggedInHint || "Click the key icon to login"
                        font.pixelSize: Theme.fontSizeSmall
                        color: Theme.primary
                        wrapMode: Text.WordWrap
                    }
                }

                // Auth expired banner
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

                // Combined data card
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
                                    { label: tr.balance || "Balance",           value: root.balanceNormal,              color: Theme.primary },
                                    { label: tr.thisMonthInput || "Input (Mo.)",  value: root._fmtTokens(root.inputTokens),  color: "#89dceb" },
                                    { label: tr.thisMonthOutput || "Output (Mo.)", value: root._fmtTokens(root.outputTokens), color: "#cba6f7" },
                                    { label: tr.thisMonthCost || "Cost (Mo.)",   value: root.monthlyCost,                color: "#f9e2af" }
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

                        // Bonus balance (only shown when > 0)
                        StyledText {
                            visible: parseFloat(root.balanceBonus.replace("¥ ", "")) > 0
                            text: (tr.bonusBalance || "Bonus Balance") + ": " + root.balanceBonus
                            font.pixelSize: Theme.fontSizeSmall - 1
                            color: Theme.surfaceVariantText
                        }
                        /*
                        StyledText {
                            text: "K = Thousand  M = Million  B = Billion"
                            font.pixelSize: Theme.fontSizeSmall - 2
                            color: Qt.rgba(Theme.surfaceVariantText.r, Theme.surfaceVariantText.g, Theme.surfaceVariantText.b, 0.5)
                        }
                        */
                    }
                }

                // Trend chart
                StyledRect {
                    width: parent.width
                    height: 240
                    radius: Theme.cornerRadius
                    color: Theme.surfaceContainerHigh

                    Column {
                        anchors { fill: parent; margins: Theme.spacingM }
                        spacing: Theme.spacingXS

                        Item {
                            width: parent.width; height: 28

                            StyledText {
                                id: chartTitle
                                text: {
                                    const m = root.viewMonth < 10 ? "0" + root.viewMonth : root.viewMonth
                                    const suffix = root._isCurrentMonth() ? " (" + (root.tr.currentMonth || "Current") + ")" : ""
                                    return (root.tr.dailyChart || "Daily Usage") + "  " + root.viewYear + "-" + m + suffix
                                }
                                font.pixelSize: Theme.fontSizeSmall
                                color: Theme.surfaceVariantText
                                anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                            }

                            StyledText {
                                text: "  ·  " + root._getMonthTotal()
                                font.pixelSize: Theme.fontSizeSmall
                                font.weight: Font.Bold
                                color: root.chartMode === "cost" ? "#f9e2af" : Theme.surfaceText
                                anchors { left: chartTitle.right; verticalCenter: parent.verticalCenter }
                            }

                            DankButton {
                                width: 28; height: 28
                                text: "¥"
                                iconName: ""
                                backgroundColor: "transparent"
                                textColor: "#f9e2af"
                                anchors { right: loginBtn.left; rightMargin: 2; verticalCenter: parent.verticalCenter }
                                onClicked: Proc.runCommand(
                                    _cmdId + ".topup",
                                    ["sh", "-c", "xdg-open https://platform.deepseek.com/top_up"],
                                    function() {}, 0, 5000
                                )
                            }

                            DankButton {
                                id: loginBtn
                                width: 28; height: 28
                                text: ""
                                iconName: "vpn_key"
                                backgroundColor: "transparent"
                                textColor: root.cookieStatus === "missing" ? Theme.error
                                         : root.cookieStatus === "expired" ? Theme.error
                                         : Theme.surfaceVariantText
                                opacity: root.loginRunning ? 0.4 : 1
                                anchors { right: refreshBtn.left; rightMargin: 2; verticalCenter: parent.verticalCenter }
                                onClicked: root.launchLogin()
                            }

                            DankButton {
                                id: refreshBtn
                                width: 28; height: 28
                                text: ""
                                iconName: "refresh"
                                backgroundColor: "transparent"
                                textColor: Theme.surfaceVariantText
                                opacity: root.fetchRunning ? 0.4 : 1
                                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                                onClicked: root.refreshAll()
                            }
                        }

                        Canvas {
                            id: trendCanvas
                            width: parent.width
                            height: 170

                            property var chartData: root._getChartData()
                            property string _mode: root.chartMode

                            onChartDataChanged: requestPaint()
                            on_ModeChanged: requestPaint()
                            onWidthChanged: requestPaint()

                            onPaint: {
                                const ctx = getContext("2d")
                                ctx.clearRect(0, 0, width, height)

                                const data = chartData
                                if (!data || data.length === 0) {
                                    ctx.fillStyle = Qt.rgba(1,1,1,0.2)
                                    ctx.font = "12px sans-serif"
                                    ctx.textAlign = "center"
                                    ctx.fillText(root.tr.noData || "No data", width / 2, height / 2)
                                    return
                                }

                                const isTokens = root.chartMode === "tokens"
                                const PAD_L = 44, PAD_R = 6, PAD_T = 14, PAD_B = 20
                                const chartW = width - PAD_L - PAD_R
                                const chartH = height - PAD_T - PAD_B
                                const n = data.length
                                const gap  = chartW / n

                                // ── Compute max value ────────────────────
                                let maxVal = 0.001
                                if (isTokens) {
                                    for (const d of data) {
                                        const models = d.models || []
                                        for (const m of models) {
                                            const t = (m.inputTokens || 0) + (m.outputTokens || 0)
                                            if (t > maxVal) maxVal = t
                                        }
                                    }
                                } else {
                                    for (const d of data) {
                                        const c = d.cost || 0
                                        if (c > maxVal) maxVal = c
                                    }
                                }

                                // ── Grid lines ──────────────────────────
                                ctx.strokeStyle = Qt.rgba(1,1,1,0.07)
                                ctx.lineWidth = 0.5
                                for (let i = 0; i <= 4; i++) {
                                    const y = PAD_T + chartH * (1 - i / 4)
                                    ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(width - PAD_R, y); ctx.stroke()
                                }

                                // ── Y-axis labels ────────────────────────
                                ctx.fillStyle = Qt.rgba(1,1,1,0.35)
                                ctx.font = "11px sans-serif"
                                ctx.textAlign = "right"
                                for (let i = 0; i <= 4; i++) {
                                    const v = maxVal * i / 4
                                    let lbl
                                    if (isTokens) {
                                        lbl = v >= 1e9 ? (v/1e9).toFixed(1)+"B" : v >= 1e6 ? (v/1e6).toFixed(1)+"M" : v >= 1e3 ? (v/1e3).toFixed(0)+"K" : String(Math.round(v))
                                    } else {
                                        lbl = "¥" + v.toFixed(2)
                                    }
                                    ctx.fillText(lbl, PAD_L - 3, PAD_T + chartH * (1 - i / 4) + 3)
                                }

                                const baseY = PAD_T + chartH

                                if (!isTokens) {
                                    // ── COST MODE: single bar per day ────
                                    const barW = Math.max(2, Math.min(gap * 0.7, 18))
                                    for (let i = 0; i < n; i++) {
                                        const d = data[i]
                                        if (d.day == null) continue
                                        const cx  = PAD_L + gap * i + gap / 2
                                        const barH = chartH * ((d.cost || 0) / maxVal)
                                        const barX = cx - barW / 2

                                        ctx.globalAlpha = 0.85
                                        ctx.fillStyle = "#f9e2af"
                                        ctx.fillRect(barX, baseY - barH, barW, barH)
                                        ctx.globalAlpha = 1.0

                                        if ((d.cost || 0) > 0 && barH > 6) {
                                            ctx.fillStyle = Qt.rgba(1,1,1,0.6)
                                            ctx.textAlign = "center"
                                            ctx.font = "11px sans-serif"
                                            ctx.fillText((d.cost || 0).toFixed(2), cx, baseY - barH - 2)
                                        }
                                        if (d.day === 1 || d.day % 5 === 0) {
                                            ctx.fillStyle = Qt.rgba(1,1,1,0.4)
                                            ctx.textAlign = "center"
                                            ctx.font = "10px sans-serif"
                                            ctx.fillText(String(d.day), cx, height - 3)
                                        }
                                    }
                                } else {
                                    // ── TOKENS MODE: per-model stacked bars ──
                                    // Color pairs: [input, output] per model
                                    const MODEL_COLORS = [
                                        ["#5b9bd5", "#a855f7"],  // model 0: medium blue + purple
                                        ["#38bdf8", "#e879f9"],  // model 1: light blue + pink-purple
                                        ["#22d3bb", "#f97316"],  // model 2: teal + orange (fallback)
                                    ]

                                    for (let i = 0; i < n; i++) {
                                        const d = data[i]
                                        if (d.day == null) continue
                                        const models = (d.models || []).filter(m => !m.model.toLowerCase().includes("chat"))
                                        if (models.length === 0) continue

                                        const numModels = Math.min(models.length, 3)
                                        const groupW = Math.min(gap * 0.8, 30)
                                        const modelW = Math.max(2, groupW / numModels - 2)
                                        const groupX = PAD_L + gap * i + gap / 2 - groupW / 2

                                        for (let mi = 0; mi < numModels; mi++) {
                                            const m = models[mi]
                                            const inp = m.inputTokens || 0
                                            const out = m.outputTokens || 0
                                            const total = inp + out
                                            if (total === 0) continue

                                            const colors = MODEL_COLORS[mi] || MODEL_COLORS[0]
                                            const mx = groupX + mi * (modelW + 2)
                                            const outH = chartH * (out / maxVal)
                                            const inpH = chartH * (inp / maxVal)
                                            const totalH = outH + inpH

                                            ctx.globalAlpha = 0.85
                                            // output (bottom)
                                            ctx.fillStyle = colors[1]
                                            ctx.fillRect(mx, baseY - outH, modelW, outH)
                                            // input (top)
                                            ctx.fillStyle = colors[0]
                                            ctx.fillRect(mx, baseY - totalH, modelW, inpH)
                                            ctx.globalAlpha = 1.0

                                            // Value label
                                            if (totalH > 6) {
                                                ctx.fillStyle = Qt.rgba(1,1,1,0.55)
                                                ctx.textAlign = "center"
                                                ctx.font = "10px sans-serif"
                                                ctx.fillText(
                                                    total >= 1e6 ? (total/1e6).toFixed(1)+"M" : total >= 1e3 ? (total/1e3).toFixed(1)+"K" : String(total),
                                                    mx + modelW / 2,
                                                    baseY - totalH - 2
                                                )
                                            }
                                        }

                                        // x label
                                        if (d.day === 1 || d.day % 5 === 0) {
                                            ctx.fillStyle = Qt.rgba(1,1,1,0.4)
                                            ctx.textAlign = "center"
                                            ctx.font = "10px sans-serif"
                                            ctx.fillText(String(d.day), PAD_L + gap * i + gap / 2, height - 3)
                                        }
                                    }
                                }
                            }
                        }

                        // Legend
                        Row {
                            spacing: Theme.spacingM
                            visible: root.chartMode === "tokens"

                            Repeater {
                                model: {
                                    const LEGEND_COLORS = [["#5b9bd5", "#a855f7"], ["#38bdf8", "#e879f9"], ["#22d3bb", "#f97316"]]
                                    const data = root._getChartData()
                                    if (!data || data.length === 0) return []
                                    for (const d of data) {
                                        const models = (d.models || []).filter(m => !m.model.toLowerCase().includes("chat"))
                                        if (models.length > 0) {
                                            return models.slice(0, 3).map((m, idx) => ({
                                                name: m.model,
                                                inColor: (LEGEND_COLORS[idx] || LEGEND_COLORS[0])[0],
                                                outColor: (LEGEND_COLORS[idx] || LEGEND_COLORS[0])[1],
                                            }))
                                        }
                                    }
                                    return []
                                }
                                delegate: Column {
                                    spacing: 2
                                    Row {
                                        spacing: 4
                                        Rectangle { width: 10; height: 6; radius: 1; color: modelData.inColor; opacity: 0.85; anchors.verticalCenter: parent.verticalCenter }
                                        Rectangle { width: 10; height: 6; radius: 1; color: modelData.outColor; opacity: 0.85; anchors.verticalCenter: parent.verticalCenter }
                                        StyledText { text: modelData.name; font.pixelSize: Theme.fontSizeSmall - 1; color: Theme.surfaceVariantText }
                                    }
                                }
                            }
                            StyledText {
                                visible: root.chartMode !== "tokens"
                                text: (tr.inputTokens || "Input") + " / " + (tr.outputTokens || "Output")
                                font.pixelSize: Theme.fontSizeSmall - 1
                                color: Theme.surfaceVariantText
                            }
                        }
                    }
                }

                // Bottom action row
                Row {
                    width: parent.width
                    spacing: Theme.spacingS

                    DankButton {
                        width: (parent.width - Theme.spacingS * 3) / 4
                        height: 34
                        text: "◀"
                        backgroundColor: Theme.surfaceContainerHigh
                        textColor: root._canGoPrev() ? Theme.surfaceText : Theme.surfaceVariantText
                        onClicked: { if (root._canGoPrev()) root._goPrevMonth() }
                    }
                    DankButton {
                        width: (parent.width - Theme.spacingS * 3) / 4
                        height: 34
                        text: root.chartMode === "cost" ? "● Cost" : "Cost"
                        backgroundColor: root.chartMode === "cost" ? Theme.primary : Theme.surfaceContainerHigh
                        textColor: root.chartMode === "cost" ? Theme.onPrimary : Theme.surfaceText
                        onClicked: { root.chartMode = "cost" }
                    }
                    DankButton {
                        width: (parent.width - Theme.spacingS * 3) / 4
                        height: 34
                        text: root.chartMode === "tokens" ? "● Tokens" : "Tokens"
                        backgroundColor: root.chartMode === "tokens" ? Theme.primary : Theme.surfaceContainerHigh
                        textColor: root.chartMode === "tokens" ? Theme.onPrimary : Theme.surfaceText
                        onClicked: { root.chartMode = "tokens" }
                    }
                    DankButton {
                        width: (parent.width - Theme.spacingS * 3) / 4
                        height: 34
                        text: "▶"
                        backgroundColor: Theme.surfaceContainerHigh
                        textColor: root._canGoNext() ? Theme.surfaceText : Theme.surfaceVariantText
                        onClicked: { if (root._canGoNext()) root._goNextMonth() }
                    }
                }

            } // contentCol
        } // PopoutComponent
    } // popoutContent

} // PluginComponent
