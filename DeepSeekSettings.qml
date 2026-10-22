import QtQuick
import qs.Common
import qs.Modules.Plugins
import qs.Services
import qs.Widgets

PluginSettings {
    id: root
    pluginId: "deepseekWidget"

    readonly property string _pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")
    readonly property string _cookieFile: _pluginDir + "cookie.txt"
    readonly property string _loginScript: _pluginDir + "scripts/login.py"
    property bool loginRunning: false
    property string loginMessage: ""

    property var tr: ({})
    function _loadI18n() {
        const locale = String(pluginData.locale || "en_US")
        const path = _pluginDir + "i18n/" + locale + ".json"
        Proc.runCommand(
            "deepseekWidget.settings.i18n",
            ["cat", path],
            (stdout, exitCode) => {
                if (exitCode === 0 && stdout.trim()) {
                    try { tr = JSON.parse(stdout) } catch(e) {}
                }
            },
            0, 5000
        )
    }
    Component.onCompleted: { _loadI18n(); _checkCookie() }

    property bool cookieExists: false
    function _checkCookie() {
        Proc.runCommand(
            "deepseekWidget.settings.cookieCheck",
            ["test", "-f", _cookieFile],
            (stdout, exitCode) => { cookieExists = (exitCode === 0) },
            0, 3000
        )
    }

    function _runLogin() {
        if (loginRunning) return
        loginRunning = true
        loginMessage = tr.systemLoginRunning || "Waiting for browser login…"
        Proc.runCommand(
            "deepseekWidget.settings.login",
            ["python", _loginScript, "--output", _cookieFile,
             "--locale", String(pluginData.locale || "en_US")],
            (stdout, exitCode) => {
                loginRunning = false
                _checkCookie()
                if (exitCode === 0) {
                    loginMessage = tr.systemLoginSaved || "Credential saved — refresh the widget to load data."
                } else {
                    loginMessage = tr.systemLoginFailed || "Login or credential verification failed."
                }
            },
            0,
            960000
        )
    }

    StyledText {
        width: parent.width
        text: tr.pluginTitle || "DeepSeek Usage"
        font.pixelSize: Theme.fontSizeLarge
        font.weight: Font.Bold
        color: Theme.surfaceText
    }

    StyledText {
        width: parent.width
        text: tr.notLoggedInHint || "Balance and usage are fetched via platform cookie. Click Re-login below to authorize."
        font.pixelSize: Theme.fontSizeSmall
        color: Theme.surfaceVariantText
        wrapMode: Text.WordWrap
    }

    // Cookie status card
    StyledRect {
        width: parent.width
        height: cookieRow.implicitHeight + Theme.spacingM * 2
        radius: Theme.cornerRadius
        color: Theme.surfaceContainerHigh

        Row {
            id: cookieRow
            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: Theme.spacingM }
            spacing: Theme.spacingS

            Rectangle {
                width: 8; height: 8; radius: 4
                anchors.verticalCenter: parent.verticalCenter
                color: root.cookieExists ? "#a6e3a1" : "#f38ba8"
            }

            Column {
                spacing: 2
                StyledText {
                    text: root.cookieExists ? (tr.cookieStatusOk || "Cookie valid") : (tr.cookieStatusMissing || "No cookie set")
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

    StyledRect {
        width: parent.width
        height: loginCol.implicitHeight + Theme.spacingM * 2
        radius: Theme.cornerRadius
        color: Theme.surfaceContainerHigh

        Column {
            id: loginCol
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: Theme.spacingM }
            spacing: Theme.spacingS

            StyledText {
                width: parent.width
                text: tr.systemLoginTitle || "Log in with your own browser"
                font.pixelSize: Theme.fontSizeMedium
                font.weight: Font.Bold
                color: Theme.surfaceText
            }

            StyledText {
                width: parent.width
                text: tr.systemLoginHint || "Opens your default browser. After logging in, copy the Authorization request header from DevTools and paste it into the secure dialog."
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.surfaceVariantText
                wrapMode: Text.WordWrap
            }

            DankButton {
                width: parent.width
                text: tr.systemLoginButton || "Open browser & import credential"
                iconName: "open_in_new"
                enabled: !root.loginRunning
                onClicked: root._runLogin()
            }

            StyledText {
                visible: root.loginMessage !== ""
                width: parent.width
                text: root.loginMessage
                font.pixelSize: Theme.fontSizeSmall
                color: root.loginMessage === (tr.systemLoginSaved || "Credential saved — refresh the widget to load data.")
                    ? "#a6e3a1" : Theme.surfaceVariantText
                wrapMode: Text.WordWrap
            }
        }
    }

    SelectionSetting {
        settingKey: "refreshSeconds"
        label: tr.refreshInterval || "Refresh interval"
        description: ""
        options: [
            { label: tr.min5  || "5 min",  value: "300"  },
            { label: tr.min15 || "15 min", value: "900"  },
            { label: tr.min30 || "30 min", value: "1800" },
            { label: tr.min60 || "60 min", value: "3600" }
        ]
        defaultValue: "900"
    }

    SelectionSetting {
        settingKey: "historyMonths"
        label: tr.historyMonths || "Trend history months"
        description: ""
        options: [
            { label: tr.month1 || "1 month", value: "1" },
            { label: tr.month3 || "3 months", value: "3" },
            { label: tr.month6 || "6 months", value: "6" }
        ]
        defaultValue: "3"
    }

    SelectionSetting {
        settingKey: "locale"
        label: tr.language || "Language"
        description: ""
        options: [
            { label: tr.langZh || "中文", value: "zh_CN" },
            { label: tr.langEn || "English", value: "en_US" }
        ]
        defaultValue: "en_US"
    }
}
