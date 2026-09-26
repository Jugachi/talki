import QtQuick
import QtQuick.Controls
import org.kde.layershell 1.0 as LayerShell

// Layer-shell surface that never takes keyboard focus, so pastes land in the user's app.
Window {
    id: win
    readonly property bool layerShell: true
    readonly property int barWidth: Math.round(236 * bar.uiScale)
    readonly property int barHeight: Math.round(64 * bar.uiScale)
    width: barWidth
    height: barHeight
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
    visible: false

    LayerShell.Window.scope: "talki-bar"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
    LayerShell.Window.exclusionZone: -1
    // Once the bar has been placed on a monitor it stays there; otherwise it opens on the active one.
    LayerShell.Window.wantsToBeOnActiveScreen: !bar.hasScreen
    LayerShell.Window.screenConfiguration: bar.hasScreen ? LayerShell.Window.ScreenFromQWindow
                                                         : LayerShell.Window.ScreenFromCompositor
    LayerShell.Window.screen: bar.targetScreen
    LayerShell.Window.activateOnShow: false
    // Once dragged, the bar is anchored top-left and placed through its margins.
    LayerShell.Window.margins: bar.margins
    LayerShell.Window.anchors: bar.dragging ? (LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom
                                               | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight)
                             : bar.hasPos ? (LayerShell.Window.AnchorTop | LayerShell.Window.AnchorLeft)
                             : bar.edge === "left" ? LayerShell.Window.AnchorLeft
                             : bar.edge === "right" ? LayerShell.Window.AnchorRight
                             : bar.edge === "top" ? LayerShell.Window.AnchorTop
                             : LayerShell.Window.AnchorBottom

    // The compositor resizes the surface for a drag; restore the bar size afterwards.
    Connections {
        target: bar
        function onDraggingChanged() {
            if (!bar.dragging) { win.width = win.barWidth; win.height = win.barHeight }
        }
        function onUiScaleChanged() {
            if (!bar.dragging) { win.width = win.barWidth; win.height = win.barHeight }
        }
    }

    // Where the docked bar currently sits, used as the starting point of the first drag.
    function dockedPos() {
        const sw = Screen.width, sh = Screen.height
        if (bar.edge === "left") return Qt.point(0, (sh - barHeight) / 2)
        if (bar.edge === "right") return Qt.point(sw - barWidth, (sh - barHeight) / 2)
        if (bar.edge === "top") return Qt.point((sw - barWidth) / 2, 0)
        return Qt.point((sw - barWidth) / 2, sh - barHeight)
    }

    OverlayBody { anchors.fill: parent }
}
