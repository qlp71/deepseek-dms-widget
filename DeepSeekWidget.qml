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
    readonly property string _python:      _pluginDir + ".venv/bin/python"
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
    // Daily (array of {day, inputTokens, outputTokens})
    property var daily: []

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

            // Monthly usage
            StyledText {
                visible: root.cookieStatus !== "missing"
                text: root.fetchRunning ? "…" : root.monthlyTokenUsage
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
                        text: tr.notLoggedInHint || "Click Re-login to get Cookie"
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

                // Login button
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

                        StyledText {
                            text: "K = Thousand  M = Million  B = Billion"
                            font.pixelSize: Theme.fontSizeSmall - 2
                            color: Qt.rgba(Theme.surfaceVariantText.r, Theme.surfaceVariantText.g, Theme.surfaceVariantText.b, 0.5)
                        }
                    }
                }

                // Trend chart
                StyledRect {
                    width: parent.width
                    height: 180
                    radius: Theme.cornerRadius
                    color: Theme.surfaceContainerHigh

                    Column {
                        anchors { fill: parent; margins: Theme.spacingM }
                        spacing: Theme.spacingXS

                        StyledText {
                            text: root.curYear > 0
                                ? (tr.dailyTokenTrend || "Daily Token Usage (This Month)") + "  " + root.curYear + "-" + (root.curMonth < 10 ? "0" + root.curMonth : root.curMonth)
                                : (tr.dailyTokenTrend || "Daily Token Usage (This Month)")
                            font.pixelSize: Theme.fontSizeSmall
                            color: Theme.surfaceVariantText
                        }

                        Canvas {
                            id: trendCanvas
                            width: parent.width
                            height: 130

                            property var chartData: root.daily

                            onChartDataChanged: requestPaint()
                            onWidthChanged: requestPaint()

                            onPaint: {
                                const ctx = getContext("2d")
                                ctx.clearRect(0, 0, width, height)

                                const data = chartData
                                if (!data || data.length === 0) {
                                    ctx.fillStyle = Qt.rgba(1,1,1,0.2)
                                    ctx.font = "10px sans-serif"
                                    ctx.textAlign = "center"
                                    ctx.fillText(root.tr.noData || "No data", width / 2, height / 2)
                                    return
                                }

                                const PAD_L = 40, PAD_R = 6, PAD_T = 8, PAD_B = 20
                                const chartW = width - PAD_L - PAD_R
                                const chartH = height - PAD_T - PAD_B
                                const n = data.length
                                const gap  = chartW / n
                                const barW = Math.max(2, Math.min(gap * 0.7, 18))

                                let maxTotal = 1
                                for (const d of data) {
                                    const t = (d.inputTokens || 0) + (d.outputTokens || 0)
                                    if (t > maxTotal) maxTotal = t
                                }

                                // grid lines
                                ctx.strokeStyle = Qt.rgba(1,1,1,0.07)
                                ctx.lineWidth = 0.5
                                for (let i = 0; i <= 4; i++) {
                                    const y = PAD_T + chartH * (1 - i / 4)
                                    ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(width - PAD_R, y); ctx.stroke()
                                }

                                // y-axis labels
                                ctx.fillStyle = Qt.rgba(1,1,1,0.35)
                                ctx.font = "9px sans-serif"
                                ctx.textAlign = "right"
                                for (let i = 0; i <= 4; i++) {
                                    const v = maxTotal * i / 4
                                    const lbl = v >= 1e9 ? (v/1e9).toFixed(1)+"B" : v >= 1e6 ? (v/1e6).toFixed(1)+"M" : v >= 1e3 ? (v/1e3).toFixed(0)+"K" : String(Math.round(v))
                                    ctx.fillText(lbl, PAD_L - 3, PAD_T + chartH * (1 - i / 4) + 3)
                                }

                                // bars + x labels
                                for (let i = 0; i < n; i++) {
                                    const d = data[i]
                                    const cx = PAD_L + gap * i + gap / 2
                                    const inpH  = chartH * ((d.inputTokens  || 0) / maxTotal)
                                    const outH  = chartH * ((d.outputTokens || 0) / maxTotal)
                                    const totalH = inpH + outH
                                    const barX  = cx - barW / 2
                                    const baseY = PAD_T + chartH

                                    // output (bottom)
                                    ctx.globalAlpha = 0.85
                                    ctx.fillStyle = "#cba6f7"
                                    ctx.fillRect(barX, baseY - outH, barW, outH)

                                    // input (top)
                                    ctx.fillStyle = "#89dceb"
                                    ctx.fillRect(barX, baseY - totalH, barW, inpH)
                                    ctx.globalAlpha = 1.0

                                    // x label: show only every 5 days and day 1
                                    if (d.day === 1 || d.day % 5 === 0) {
                                        ctx.fillStyle = Qt.rgba(1,1,1,0.4)
                                        ctx.textAlign = "center"
                                        ctx.font = "8px sans-serif"
                                        ctx.fillText(String(d.day), cx, height - 3)
                                    }
                                }
                            }
                        }

                        // Legend
                        Row {
                            spacing: Theme.spacingM

                            Row {
                                spacing: 4
                                Rectangle { width: 10; height: 8; radius: 1; color: "#89dceb"; opacity: 0.85; anchors.verticalCenter: parent.verticalCenter }
                                StyledText { text: tr.inputTokens || "Input"; font.pixelSize: Theme.fontSizeSmall - 1; color: Theme.surfaceVariantText }
                            }
                            Row {
                                spacing: 4
                                Rectangle { width: 10; height: 8; radius: 1; color: "#cba6f7"; opacity: 0.85; anchors.verticalCenter: parent.verticalCenter }
                                StyledText { text: tr.outputTokens || "Output"; font.pixelSize: Theme.fontSizeSmall - 1; color: Theme.surfaceVariantText }
                            }
                        }
                    }
                }

                // Bottom action row
                Row {
                    width: parent.width
                    spacing: Theme.spacingS

                    Repeater {
                        model: [
                            { label: tr.usagePage   || "Usage",    url: "https://platform.deepseek.com/usage"      },
                            { label: tr.monitorPage || "Monitoring",      url: "https://console.deepseek.com/monitoring"  },
                            { label: tr.apiKeysPage || "API Keys", url: "https://platform.deepseek.com/api_keys"   }
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

                // Refresh button
                StyledRect {
                    width: parent.width
                    height: 34
                    radius: Theme.cornerRadius
                    color: refreshArea.containsMouse ? Theme.primary : Theme.surfaceContainerHigh
                    opacity: root.fetchRunning ? 0.6 : 1
                    StyledText { anchors.centerIn: parent; text: root.fetchRunning ? "…" : (tr.refresh || "Refresh"); font.pixelSize: Theme.fontSizeSmall; color: refreshArea.containsMouse ? Theme.onPrimary : Theme.surfaceText }
                    MouseArea { id: refreshArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; enabled: !root.fetchRunning; onClicked: root.refreshAll() }
                }

            } // contentCol
        } // PopoutComponent
    } // popoutContent

} // PluginComponent
