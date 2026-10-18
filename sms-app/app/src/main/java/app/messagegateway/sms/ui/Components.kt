package app.messagegateway.sms.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.widget.Toast
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.background
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import app.messagegateway.sms.domain.ProcessingState
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

private val timeFormat = DateTimeFormatter.ofLocalizedDateTime(FormatStyle.SHORT).withZone(ZoneId.systemDefault())

fun formatTime(millis: Long): String = timeFormat.format(Instant.ofEpochMilli(millis))

fun copyToClipboard(context: Context, label: String, text: String) {
    context.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText(label, text))
    Toast.makeText(context, "$label copied", Toast.LENGTH_SHORT).show()
}

@Composable
fun SectionCard(title: String, modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            content()
        }
    }
}

/** A label and a value, with a clipboard icon to copy the value and an optional [extra] icon before it. */
@Composable
fun CopyRow(
    label: String,
    value: String,
    context: Context,
    mono: Boolean = true,
    copyValue: String = value,
    extra: @Composable () -> Unit = {},
) {
    // The icons sit on the value's line, not centred against label + value together.
    Column(Modifier.fillMaxWidth()) {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                value,
                Modifier.weight(1f),
                style = MaterialTheme.typography.bodyMedium,
                fontFamily = if (mono) FontFamily.Monospace else null,
            )
            extra()
            SmallIconButton(ClipboardIcon, "Copy $label") { copyToClipboard(context, label, copyValue) }
        }
    }
}

/**
 * One color per meaning, everywhere in the app: amber waiting, slate sent, green delivered, red failed,
 * blue received, purple command, teal from the server, grey for the rest.
 */
@Composable
fun StateChip(state: ProcessingState) {
    val scheme = MaterialTheme.colorScheme
    val status = LocalStatusPalette.current
    val (container, content) = when (state) {
        ProcessingState.Pending -> status.warn.container to status.warn.onContainer
        ProcessingState.Processed, ProcessingState.Sent -> { OriginTag(Origin.Sent, state.name); return }
        ProcessingState.Delivered -> status.good.container to status.good.onContainer
        ProcessingState.Failed -> status.bad.container to status.bad.onContainer
        ProcessingState.Cancelled -> scheme.surfaceVariant to scheme.onSurfaceVariant
    }
    Tag(state.name, container, content)
}

/** Where a message travelled, as a tag: its own hue, or neutral when the kind says nothing worth highlighting. */
enum class Origin(val label: String) { Sent("Sent"), Received("Received"), Command("Command"), Server("Server"), Local("Local"), Sim("") }

@Composable
fun OriginTag(origin: Origin, text: String = origin.label) {
    val dark = isSystemInDarkTheme()
    val scheme = MaterialTheme.colorScheme
    val (container, content) = when (origin) {
        // Fixed, not the theme's secondary: that follows the wallpaper and can land on any other tag's hue.
        Origin.Sent -> if (dark) Color(0xFF2A3440) to Color(0xFFD5DEE8) else Color(0xFFDDE3EA) to Color(0xFF2B3A4A)
        Origin.Received -> if (dark) Color(0xFF16314F) to Color(0xFFCFE2FA) else Color(0xFFD6E6FA) to Color(0xFF0F3E78)
        Origin.Command -> if (dark) Color(0xFF332452) to Color(0xFFE3D6FA) else Color(0xFFE6DDF7) to Color(0xFF3B2470)
        Origin.Server -> if (dark) Color(0xFF0F3A37) to Color(0xFFC4EFEA) else Color(0xFFCDEDEA) to Color(0xFF0A4540)
        Origin.Local, Origin.Sim -> scheme.surfaceVariant to scheme.onSurfaceVariant
    }
    Tag(text, container, content)
}

/** A small label: the card's own text is the content, so this stays at the size of a caption. */
@Composable
fun Tag(text: String, container: Color, content: Color) {
    Surface(color = container, contentColor = content, shape = RoundedCornerShape(6.dp)) {
        Text(
            text,
            modifier = Modifier.padding(horizontal = 6.dp, vertical = 1.dp),
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
fun Pill(text: String, container: Color, content: Color) {
    Surface(color = container, contentColor = content, shape = CircleShape) {
        Text(
            text,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 3.dp),
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
fun StatusDot(color: Color) {
    Spacer(Modifier.size(10.dp).background(color, CircleShape))
}

@Composable
fun EmptyState(text: String, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(text, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
fun RowSpacer() = Spacer(Modifier.width(12.dp))
