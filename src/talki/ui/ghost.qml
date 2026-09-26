import QtQuick
import QtQuick.Controls
import org.kde.layershell 1.0 as LayerShell

// Click-through full-screen surface on another monitor. While the bar is dragged across
// monitors, the real bar keeps the pointer grab and this draws it where the cursor is.
Window {
    id: win
    readonly property bool layerShell: true
    readonly property int barWidth: Math.round(236 * bar.uiScale)
    readonly property int barHeight: Math.round(64 * bar.uiScale)
    width: 200
    height: 100
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
    visible: false

    LayerShell.Window.scope: "talki-bar-ghost"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.activateOnShow: false
    LayerShell.Window.wantsToBeOnActiveScreen: false
    LayerShell.Window.screenConfiguration: LayerShell.Window.ScreenFromQWindow
    LayerShell.Window.screen: win.screen
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom
                             | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight

    OverlayBody { anchors.fill: parent }
}
