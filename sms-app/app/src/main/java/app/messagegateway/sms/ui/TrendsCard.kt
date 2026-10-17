package app.messagegateway.sms.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.Canvas
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min

private enum class Series { Sent, Received }

private val dayShort = DateTimeFormatter.ofPattern("MMM d", Locale.getDefault())
private val dayLong = DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM)

// Categorical slot 1 of the chart palette, for the one series that is not a pass/fail status.
private val ReceivedLight = Color(0xFF2A78D6)
private val ReceivedDark = Color(0xFF3987E5)

/**
 * Message volume per day. One row of controls above the chart (range and series), the chart, a legend where
 * there are two series, and the same numbers as a table for anyone who can't or won't use the chart.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun TrendsCard(vm: AppViewModel, modifier: Modifier = Modifier) {
    val days by vm.trendDays.collectAsStateWithLifecycle()
    val buckets by vm.trendBuckets.collectAsStateWithLifecycle()
    val pendingMessages by vm.pendingMessages.collectAsStateWithLifecycle()
    val pendingDeliveries by vm.pendingDeliveries.collectAsStateWithLifecycle()
    var series by remember { mutableStateOf(Series.Sent) }
    var table by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf<Int?>(null) }

    val palette = LocalStatusPalette.current
    val receivedColor = if (palette.good.accent.luminance() > 0.4f) ReceivedDark else ReceivedLight
    val sentTotal = buckets.sumOf { it.sent }
    val failedTotal = buckets.sumOf { it.failed }
    val receivedTotal = buckets.sumOf { it.received }

    Card(
        modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Activity", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)

            // Everything that scopes the chart is in this one row, above it.
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
                Segmented(listOf("Sent", "Received"), selectedIndex = series.ordinal, Modifier.weight(1.4f)) {
                    series = Series.entries[it]; selected = null
                }
                Segmented(listOf("7d", "14d", "30d"), selectedIndex = listOf(7, 14, 30).indexOf(days), Modifier.weight(1f)) {
                    vm.setTrendDays(listOf(7, 14, 30)[it]); selected = null
                }
            }

            Text(
                when (series) {
                    Series.Sent -> when {
                        sentTotal + failedTotal == 0 -> "No texts sent in the last $days days"
                        else -> "$sentTotal sent" + (if (failedTotal > 0) " · $failedTotal failed" else "") + " in the last $days days"
                    }
                    Series.Received -> if (receivedTotal == 0) "No texts received in the last $days days" else "$receivedTotal received in the last $days days"
                },
                style = MaterialTheme.typography.bodyMedium,
            )

            if (table) {
                TrendsTable(buckets, series)
            } else {
                ColumnsChart(
                    buckets = buckets,
                    series = series,
                    sentColor = palette.good.accent,
                    failedColor = palette.bad.accent,
                    receivedColor = receivedColor,
                    selected = selected,
                    onSelect = { selected = it },
                )
                // Two series need a legend; a single series is named by the line above.
                if (series == Series.Sent) {
                    Row(horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
                        LegendKey(palette.good.accent, "Sent")
                        LegendKey(palette.bad.accent, "Failed")
                    }
                }
            }

            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "Waiting: $pendingMessages texts · $pendingDeliveries webhooks",
                    Modifier.weight(1f),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                TextButton({ table = !table; selected = null }) { Text(if (table) "Show chart" else "Show table") }
            }
        }
    }
}

/** A compact one-of-N switch. The check icon is dropped and the padding trimmed so the labels fit on a narrow phone. */
@Composable
private fun Segmented(labels: List<String>, selectedIndex: Int, modifier: Modifier, onSelect: (Int) -> Unit) {
    SingleChoiceSegmentedButtonRow(modifier) {
        labels.forEachIndexed { i, label ->
            SegmentedButton(
                selected = i == selectedIndex,
                onClick = { onSelect(i) },
                shape = SegmentedButtonDefaults.itemShape(i, labels.size),
                icon = {},
                contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 4.dp),
                label = { Text(label, maxLines = 1) },
            )
        }
    }
}

private fun Color.luminance(): Float = (0.2126f * red + 0.7152f * green + 0.0722f * blue)

@Composable
private fun LegendKey(color: Color, label: String) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Box(Modifier.size(10.dp).background(color, RoundedCornerShape(2.dp)))
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun TrendsTable(buckets: List<DayBucket>, series: Series) {
    val active = buckets.filter { if (series == Series.Sent) it.outgoing > 0 else it.received > 0 }.asReversed()
    if (active.isEmpty()) {
        Text("Nothing in this period.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        return
    }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row {
            Text("Day", Modifier.weight(1f), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (series == Series.Sent) {
                Text("Sent", Modifier.width(56.dp), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("Failed", Modifier.width(56.dp), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            } else {
                Text("Received", Modifier.width(72.dp), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        active.take(14).forEach { d ->
            Row {
                Text(d.date.format(dayLong), Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                if (series == Series.Sent) {
                    Text("${d.sent}", Modifier.width(56.dp), style = MaterialTheme.typography.bodyMedium)
                    Text("${d.failed}", Modifier.width(56.dp), style = MaterialTheme.typography.bodyMedium)
                } else {
                    Text("${d.received}", Modifier.width(72.dp), style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
        val hidden = buckets.count { if (series == Series.Sent) it.outgoing > 0 else it.received > 0 } - 14
        if (hidden > 0) Text("…and $hidden earlier days", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

private val ChartHeight = 168.dp
private val YAxisWidth = 30.dp
private val XAxisHeight = 22.dp
private val TopPad = 8.dp
private val MaxBar = 24.dp
private val SegmentGap = 2.dp
private val EndRadius = 4.dp

@Composable
private fun ColumnsChart(
    buckets: List<DayBucket>,
    series: Series,
    sentColor: Color,
    failedColor: Color,
    receivedColor: Color,
    selected: Int?,
    onSelect: (Int?) -> Unit,
) {
    val measurer = rememberTextMeasurer()
    val axisText = MaterialTheme.typography.labelSmall.copy(color = MaterialTheme.colorScheme.onSurfaceVariant)
    val gridColor = MaterialTheme.colorScheme.outlineVariant
    val bandColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.06f)
    val n = buckets.size
    // The first frame has no data yet: hold the chart's space so the card doesn't jump when it arrives.
    if (n == 0) {
        Spacer(Modifier.fillMaxWidth().height(ChartHeight))
        return
    }
    fun total(b: DayBucket) = if (series == Series.Sent) b.outgoing else b.received
    val ticks = Trends.niceTicks(buckets.maxOfOrNull(::total) ?: 0)
    val topValue = ticks.last().toFloat()
    val labelAt = Trends.labelIndexes(n).toSet()
    val summary = buckets.filter { total(it) > 0 }.joinToString("; ") {
        "${it.date.format(dayLong)}: " + if (series == Series.Sent) "${it.sent} sent, ${it.failed} failed" else "${it.received} received"
    }.ifEmpty { "No activity in this period" }

    BoxWithConstraints(Modifier.fillMaxWidth().height(ChartHeight)) {
        val widthDp = maxWidth
        val plotWidthDp = widthDp - YAxisWidth
        val slotDp = plotWidthDp / n

        Canvas(
            Modifier.fillMaxWidth().height(ChartHeight)
                .semantics { contentDescription = "Daily message volume. $summary" }
                // A tap anywhere in a day's slot selects that day: the target is the whole slot, not the thin bar.
                .pointerInput(n, selected) {
                    detectTapGestures { tap ->
                        val left = YAxisWidth.toPx()
                        val index = floor((tap.x - left) / ((size.width - left) / n)).toInt()
                        onSelect(if (index in 0 until n && index != selected) index else null)
                    }
                },
        ) {
            val left = YAxisWidth.toPx()
            val top = TopPad.toPx()
            val bottom = size.height - XAxisHeight.toPx()
            val plotH = bottom - top
            val slot = (size.width - left) / n
            fun y(value: Float) = bottom - value / topValue * plotH

            // Recessive hairline grid with the round tick values.
            for (t in ticks) {
                val gy = y(t.toFloat())
                drawLine(gridColor, Offset(left, gy), Offset(size.width, gy), strokeWidth = 1.dp.toPx())
                val label = measurer.measure(Trends.compact(t), axisText)
                drawText(label, topLeft = Offset(left - 6.dp.toPx() - label.size.width, gy - label.size.height / 2f))
            }

            selected?.let { i -> drawRect(bandColor, Offset(left + slot * i, top), Size(slot, plotH)) }

            val barW = min(MaxBar.toPx(), slot * 0.62f)
            val gap = SegmentGap.toPx()
            buckets.forEachIndexed { i, b ->
                val x = left + slot * i + (slot - barW) / 2f
                if (series == Series.Sent) {
                    val sentH = barHeight(b.sent, topValue, plotH)
                    val failedH = barHeight(b.failed, topValue, plotH)
                    // Sent sits on the baseline; Failed stacks above it, with a surface-colored gap between them.
                    if (b.sent > 0) column(x, bottom - sentH, barW, sentH, sentColor, roundTop = b.failed == 0)
                    if (b.failed > 0) {
                        val base = if (b.sent > 0) bottom - sentH - gap else bottom
                        column(x, base - failedH, barW, failedH, failedColor, roundTop = true)
                    }
                } else if (b.received > 0) {
                    val h = barHeight(b.received, topValue, plotH)
                    column(x, bottom - h, barW, h, receivedColor, roundTop = true)
                }
            }

            for (i in labelAt) {
                val label = measurer.measure(buckets[i].date.format(dayShort), axisText)
                val cx = left + slot * i + slot / 2f
                val lx = (cx - label.size.width / 2f).coerceIn(left, size.width - label.size.width)
                drawText(label, topLeft = Offset(lx, bottom + 5.dp.toPx()))
            }
        }

        selected?.let { i ->
            val b = buckets[i]
            val tipWidth = 150.dp
            val centre = YAxisWidth + slotDp * i + slotDp / 2
            val x = (centre - tipWidth / 2).coerceIn(YAxisWidth, maxOf(YAxisWidth, widthDp - tipWidth))
            Tooltip(b, series, sentColor, failedColor, receivedColor, Modifier.offset(x = x, y = 0.dp).width(tipWidth))
        }
    }
}

private fun barHeight(count: Int, top: Float, plotH: Float): Float =
    if (count <= 0) 0f else max(2f, count / top * plotH)

/** A column with its data end (the top) rounded 4dp when [roundTop] and square at the baseline end. */
private fun DrawScope.column(x: Float, y: Float, w: Float, h: Float, color: Color, roundTop: Boolean) {
    val r = if (roundTop) min(EndRadius.toPx(), h) else 0f
    val path = Path().apply {
        addRoundRect(RoundRect(Rect(x, y, x + w, y + h), topLeft = CornerRadius(r), topRight = CornerRadius(r)))
    }
    drawPath(path, color)
}

@Composable
private fun Tooltip(b: DayBucket, series: Series, sent: Color, failed: Color, received: Color, modifier: Modifier) {
    Surface(
        modifier,
        shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.surfaceContainerHighest,
        shadowElevation = 3.dp,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(b.date.format(dayLong), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (series == Series.Sent) {
                TooltipRow(sent, b.sent, "sent")
                TooltipRow(failed, b.failed, "failed")
            } else {
                TooltipRow(received, b.received, "received")
            }
        }
    }
}

/** The value leads, the name follows; a short colored stroke keys the series. */
@Composable
private fun TooltipRow(color: Color, value: Int, label: String) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Box(Modifier.width(12.dp).height(3.dp).background(color, RoundedCornerShape(2.dp)))
        Text("$value", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
        Text(label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
