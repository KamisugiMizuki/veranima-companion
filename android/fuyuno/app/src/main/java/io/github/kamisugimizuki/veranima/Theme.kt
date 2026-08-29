package io.github.kamisugimizuki.veranima

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// Claude.com 色板（UI 设计说明）：奶油画布 + 暖墨 + 珊瑚，深色面板 #181715
internal val Canvas = Color(0xFFFAF9F5)        // 页面底（非纯白，暖）
internal val SurfaceCard = Color(0xFFEFE9DE)   // 桌面气泡底（比画布深一档）
internal val SurfaceDark = Color(0xFF181715)   // 我的气泡底（深色面板）
internal val Ink = Color(0xFF141413)           // 标题/主文本
internal val Body = Color(0xFF3D3D3A)          // 正文
internal val Muted = Color(0xFF6C6A64)         // 次级
internal val MutedSoft = Color(0xFF8E8B82)     // 状态行
internal val OnDark = Color(0xFFFAF9F5)        // 深色面上的奶油白
internal val OnDarkSoft = Color(0xFFA09D96)    // 深色面上的次级
internal val Coral = Color(0xFFCC785C)         // 品牌珊瑚（仅主 CTA）
internal val CoralActive = Color(0xFFA9583E)   // 按压态
internal val Hairline = Color(0xFFE6DFD8)      // 1px 发丝边

// 显示衬线：CJK 标题（駒川/凛）用平台 Noto Serif 明朝体——Cormorant 无汉字字形会回退成黑体
internal val DisplayFont: FontFamily = FontFamily.Serif

private val Shapes = androidx.compose.material3.Shapes(
    extraSmall = RoundedCornerShape(6.dp),
    small = RoundedCornerShape(8.dp),      // 按钮/输入框 rounded.md
    medium = RoundedCornerShape(12.dp),    // 卡片/气泡 rounded.lg
    large = RoundedCornerShape(16.dp),
    extraLarge = RoundedCornerShape(24.dp),
)

private val Scheme = lightColorScheme(
    primary = Coral, onPrimary = Color.White,
    background = Canvas, onBackground = Ink,
    surface = Canvas, onSurface = Ink,
    surfaceVariant = SurfaceCard, onSurfaceVariant = Muted,
    outline = Hairline, outlineVariant = Hairline,
    secondary = CoralActive,
)

private val Type = Typography(
    headlineSmall = TextStyle(fontFamily = DisplayFont, fontSize = 28.sp,
        fontWeight = FontWeight.Normal, letterSpacing = (-0.3).sp, color = Ink),
    titleLarge = TextStyle(fontFamily = DisplayFont, fontSize = 22.sp,
        fontWeight = FontWeight.Normal, letterSpacing = (-0.2).sp, color = Ink),
    titleMedium = TextStyle(fontWeight = FontWeight.Medium, fontSize = 18.sp, color = Ink),
    titleSmall = TextStyle(fontWeight = FontWeight.Medium, fontSize = 16.sp, color = Ink),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 24.sp, color = Body),
    bodyMedium = TextStyle(fontSize = 15.sp, lineHeight = 23.sp, color = Body),
    bodySmall = TextStyle(fontSize = 13.sp, color = Muted),
    labelLarge = TextStyle(fontWeight = FontWeight.Medium, fontSize = 14.sp),
)

@Composable
fun VeranimaTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = Scheme, typography = Type, shapes = Shapes, content = content)
}
