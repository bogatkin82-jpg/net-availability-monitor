package ru.netmon.android.ui

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// Палитра дашборда (static/style.css): фон #0a0e13, карточки #111820.
val Bg: Color = Color(0xFF0A0E13)
val BgDeep: Color = Color(0xFF070A0E)
val Card: Color = Color(0xFF111820)
val CardSoft: Color = Color(0xFF0E151D)
val Border: Color = Color(0xFF1E2A36)
val BorderHi: Color = Color(0xFF2B3D4F)
val TextPrimary: Color = Color(0xFFE9EFF6)
val TextSecondary: Color = Color(0xFFA7B7C9)
val TextMuted: Color = Color(0xFF71839A)
val AccentBlue: Color = Color(0xFF2EA6E0)
val AccentGreen: Color = Color(0xFF38D39F)
val WarnColor: Color = Color(0xFFF5B942)

val CardShape = RoundedCornerShape(16.dp)
val ChipShape = RoundedCornerShape(8.dp)

private val NetMonTypography = Typography(
    titleLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 22.sp,
        lineHeight = 28.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 16.sp,
        lineHeight = 22.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontSize = 14.sp,
        lineHeight = 20.sp,
    ),
    bodySmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontSize = 12.sp,
        lineHeight = 16.sp,
    ),
    labelSmall = TextStyle(
        fontFamily = FontFamily.Monospace,
        fontSize = 11.sp,
        lineHeight = 14.sp,
    ),
)

@Composable
fun NetMonTheme(content: @Composable () -> Unit) {
    val colorScheme = darkColorScheme(
        primary = AccentBlue,
        onPrimary = Color.White,
        secondary = AccentGreen,
        onSecondary = Color(0xFF04140E),
        background = Bg,
        onBackground = TextPrimary,
        surface = Card,
        onSurface = TextPrimary,
        surfaceVariant = CardSoft,
        onSurfaceVariant = TextSecondary,
        outline = Border,
        outlineVariant = BorderHi,
        error = Color(0xFFFF3B30),
        onError = Color.White,
    )
    MaterialTheme(
        colorScheme = colorScheme,
        typography = NetMonTypography,
        content = content,
    )
}
