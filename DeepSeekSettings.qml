import QtQuick
import qs.Common
import qs.Modules.Plugins
import qs.Services
import qs.Widgets

PluginSettings {
    id: root
    pluginId: "deepseekWidget"

    readonly property string _pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")

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
        const path = _pluginDir + "cookie.txt"
        Proc.runCommand(
            "deepseekWidget.settings.cookieCheck",
            ["test", "-f", path],
            (stdout, exitCode) => { cookieExists = (exitCode === 0) },
            0, 3000
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

    SelectionSetting {
        settingKey: "refreshSeconds"
        label: tr.refreshInterval || "Refresh interval"
        description: ""
        options: [
            { label: tr.min1  || "1 min",  value: "60"   },
            { label: tr.min5  || "5 min",  value: "300"  },
            { label: tr.min15 || "15 min", value: "900"  },
            { label: tr.min30 || "30 min", value: "1800" }
        ]
        defaultValue: "300"
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

    StyledText {
        width: parent.width
        text: tr.prereqTitle || "Prerequisites"
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
