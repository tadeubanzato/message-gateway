package app.messagegateway.sms.ui

import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.unit.dp

// Thin outline glyphs (24 grid, 2px round stroke) for the bottom bar, in place of the stock filled set.

private fun outline(name: String, vararg paths: String): ImageVector =
    ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).apply {
        paths.forEach {
            addPath(
                PathParser().parsePathString(it).toNodes(),
                stroke = SolidColor(Color.Black),
                strokeLineWidth = 1.8f,
                strokeLineCap = StrokeCap.Round,
                strokeLineJoin = StrokeJoin.Round,
            )
        }
    }.build()

internal val NavHomeIcon: ImageVector by lazy {
    outline("Home", "M3 10.5L12 3l9 7.5V19a2 2 0 0 1-2 2h-4v-6H9v6H5a2 2 0 0 1-2-2z")
}

internal val NavMessagesIcon: ImageVector by lazy {
    outline("Messages", "M21 12a8 8 0 0 1-11.6 7.1L3 21l1.9-5.4A8 8 0 1 1 21 12z")
}

internal val NavWebhooksIcon: ImageVector by lazy {
    outline("Webhooks", "M13 2L3 14h9l-1 8 10-12h-9l1-8z")
}

internal val NavSettingsIcon: ImageVector by lazy {
    outline(
        "Settings",
        "M21 4h-7", "M10 4H3", "M21 12h-9", "M8 12H3", "M21 20h-5", "M12 20H3",
        "M14 2v4", "M8 10v4", "M16 18v4",
    )
}
