import QtQuick

Item {
    id: root

    property alias imageX: videoFrame.x
    property alias imageY: videoFrame.y
    property real imageScaleX: 1.0
    property real imageScaleY: 1.0
    property real imageWidth: 0
    property real imageHeight: 0

    Image {
        id: videoFrame
        source: "image://hdr/frame"
        cache: false
        smooth: false // Match GraphicsView FastTransformation
        width: root.imageWidth / Screen.devicePixelRatio
        height: root.imageHeight / Screen.devicePixelRatio
        transform: Scale {
            xScale: root.imageScaleX
            yScale: root.imageScaleY
            origin.x: 0
            origin.y: 0
        }
    }

    function refresh() {
        // Use a timestamp to force the image provider to refresh
        videoFrame.source = "";
        videoFrame.source = "image://hdr/frame?t=" + Date.now();
    }
}
