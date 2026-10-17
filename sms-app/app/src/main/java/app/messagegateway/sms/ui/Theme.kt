package app.messagegateway.sms.ui

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.shape.RoundedCornerShape

// Blue taken from the app icon, used when the phone has no wallpaper-based (Material You) colors.
private val LightColors = lightColorScheme(
    primary = Color(0xFF1B4FE0),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFDCE4FF),
    onPrimaryContainer = Color(0xFF001551),
    secondary = Color(0xFF00677F),
    secondaryContainer = Color(0xFFB9EAFF),
    onSecondaryContainer = Color(0xFF001F29),
    tertiaryContainer = Color(0xFFFFE9B8),
    onTertiaryContainer = Color(0xFF271900),
    background = Color(0xFFFAFAFF),
    surface = Color(0xFFFAFAFF),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFFB4C5FF),
    onPrimary = Color(0xFF00256E),
    primaryContainer = Color(0xFF1F3BA5),
    onPrimaryContainer = Color(0xFFDCE4FF),
    secondary = Color(0xFF58D6FF),
    secondaryContainer = Color(0xFF004E60),
    onSecondaryContainer = Color(0xFFB9EAFF),
    tertiaryContainer = Color(0xFF5D4200),
    onTertiaryContainer = Color(0xFFFFE9B8),
    background = Color(0xFF10131C),
    surface = Color(0xFF10131C),
)

private val AppShapes = Shapes(
    small = RoundedCornerShape(10.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(24.dp),
)

/** Colors for one health level. [accent] is for dots and switch tracks; [onAccent] is what sits on it. */
@Immutable
data class StatusStyle(val container: Color, val onContainer: Color, val accent: Color, val onAccent: Color)

@Immutable
data class StatusPalette(val good: StatusStyle, val warn: StatusStyle, val bad: StatusStyle) {
    fun of(level: Level): StatusStyle = when (level) {
        Level.Good -> good
        Level.Warn -> warn
        Level.Bad -> bad
    }
}

// Green / amber / red do not exist in Material You's wallpaper colors, so health colors are fixed. Text on a tinted
// card is at least 9:1, and a switch thumb on its track at least 6:1, in both modes.
private val LightStatus = StatusPalette(
    good = StatusStyle(Color(0xFFBFE8C9), Color(0xFF0B3A1B), Color(0xFF146C2E), Color.White),
    warn = StatusStyle(Color(0xFFF8DE9A), Color(0xFF3D2B00), Color(0xFF8A5300), Color.White),
    bad = StatusStyle(Color(0xFFF6C6C0), Color(0xFF5A0E08), Color(0xFFB3261E), Color.White),
)

private val DarkStatus = StatusPalette(
    good = StatusStyle(Color(0xFF133A22), Color(0xFFC6F0D2), Color(0xFF6FD58D), Color(0xFF003913)),
    warn = StatusStyle(Color(0xFF43330A), Color(0xFFFFE4A0), Color(0xFFF2B84B), Color(0xFF3B2800)),
    bad = StatusStyle(Color(0xFF4D1814), Color(0xFFFFDAD5), Color(0xFFFF8A80), Color(0xFF4A0A06)),
)

val LocalStatusPalette = staticCompositionLocalOf { LightStatus }

@Composable
fun AppTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val colors = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            val context = LocalContext.current
            if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        }
        dark -> DarkColors
        else -> LightColors
    }
    CompositionLocalProvider(LocalStatusPalette provides if (dark) DarkStatus else LightStatus) {
        MaterialTheme(colorScheme = colors, shapes = AppShapes, content = content)
    }
}
