package io.github.kamisugimizuki.veranima

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
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

// 夜间模式（跟随系统，ANDROID_UI_VISUAL_NOVEL_SPEC 3.5）：画布藏青，面板提不透明度
internal val CanvasDark = Color(0xFF16161D)
internal val SurfaceCardDark = Color(0xFF1F1F27)
internal val SurfaceDarkNight = Color(0xFF0E0E13)
internal val InkDark = Color(0xFFE8E6E0)
internal val BodyDark = Color(0xFFC9C7C0)
internal val MutedDark = Color(0xFF9A988F)
internal val MutedSoftDark = Color(0xFF75736C)
internal val HairlineDark = Color(0xFF2E2E36)

// P2 情绪→环境光表（ANDROID_UI_VISUAL_NOVEL_SPEC 3.4）：中心白/奶油 → 边缘氛围色
// 夜（23:00-6:00 叠加）由 Kotlin 侧取 NightAmbient 降明度混合
internal val MoodAmbient = mapOf(
    "开心" to Color(0xFFF3E2C7),  // 香槟金
    "平静" to Color(0xFFE8E4DC),  // 米白→暖灰
    "低落" to Color(0xFFC9CDD6),  // 雾蓝灰
)
internal val ToneAmbient = mapOf(
    "喜悦" to Color(0xFFF0D3C0), "微笑" to Color(0xFFF0D3C0), "温柔" to Color(0xFFF2D8CC), "暧昧" to Color(0xFFEBC8C0),   // 暖
    "闲置" to Color(0xFFE8E4DC), "安静" to Color(0xFFE8E4DC), "认真" to Color(0xFFE8E4DC), "恭敬" to Color(0xFFE8E4DC),   // 静
    "毒舌" to Color(0xFFE6C3B8), "戏谑" to Color(0xFFE6C3B8), "调侃" to Color(0xFFE6C3B8), "不屑" to Color(0xFFE6C3B8),   // 锐
    "失落" to Color(0xFFC3C8DA), "悲伤" to Color(0xFFC3C8DA),                                                             // 沉
    "好奇" to Color(0xFFE8E4DC), "惊讶" to Color(0xFFE8E4DC), "愤怒" to Color(0xFFE0C0B8), "严肃" to Color(0xFFE8E4DC),   // 动
)
internal val NightAmbient = Color(0xFF1B1E2B)  // 夜间叠加：藏青→深紫灰
internal val ToneLabelColor = mapOf(
    // 暖：暖橙字；静：墨色；锐：珊瑚；沉：雾蓝；动：墨色
    "喜悦" to Color(0xFFC77B4A), "微笑" to Color(0xFFC77B4A), "温柔" to Color(0xFFC77B4A), "暧昧" to Color(0xFFC77B4A),
    "毒舌" to Coral, "戏谑" to Coral, "调侃" to Coral, "不屑" to Coral,
    "失落" to Color(0xFF7A87A8), "悲伤" to Color(0xFF7A87A8),
    "愤怒" to Coral,
)

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
    // snackbar 等"反色面"必须显式配对，否则 M3 默认值在本色板下浅字浅底
    inverseSurface = SurfaceDark, inverseOnSurface = OnDark,
)

private val SchemeDark = darkColorScheme(
    primary = Coral, onPrimary = Color.White,
    background = CanvasDark, onBackground = InkDark,
    surface = CanvasDark, onSurface = InkDark,
    surfaceVariant = SurfaceCardDark, onSurfaceVariant = MutedDark,
    outline = HairlineDark, outlineVariant = HairlineDark,
    secondary = CoralActive,
    inverseSurface = InkDark, inverseOnSurface = CanvasDark,  // 夜间反色=浅底深字
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
fun VeranimaTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (dark) SchemeDark else Scheme,
        typography = Type, shapes = Shapes, content = content)
}

@Composable
private fun isSystemInDarkTheme(): Boolean =
    androidx.compose.foundation.isSystemInDarkTheme()

