import QtQuick
import QtQuick.Controls

// Fallback when layer-shell-qt is unavailable (X11 or non-KDE builds of Qt).
Window {
    id: win
    readonly property bool layerShell: false
    readonly property int barWidth: Math.round(236 * bar.uiScale)
    readonly property int barHeight: Math.round(64 * bar.uiScale)
    width: barWidth
    height: barHeight
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
    visible: false
    x: Screen.virtualX + (bar.hasPos ? bar.posX : dockedPos().x)
    y: Screen.virtualY + (bar.hasPos ? bar.posY : dockedPos().y)

    function dockedPos() {
        return Qt.point((Screen.width - width) / 2,
                        bar.edge === "top" ? 8 : Screen.height - height - 48)
    }

    OverlayBody { anchors.fill: parent }
}
