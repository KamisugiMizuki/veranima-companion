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

/**
 * Galaxy（Uiverse.io）黑白极简 · 全应用色板唯一源（2026-09-01 UI 重构指令）。
 *
 * 规范：主色黑白 #000/#FFF；点缀色仅低饱和三色——雾霾蓝 #7B8FA1 / 鼠尾草绿 #9CAF88 /
 * 暖灰褐 #C4A882。日间=纯白画布，夜间自动反色（近黑画布+白字）。
 * 语义色一律走 @Composable fun 访问器（调用点 `color = Canvas()` 形态、日夜自适应）。
 *
 * 历史：旧 Claude 奶油色板（2026-08-29 定）整体替换；情绪环境光层（radialGradient）
 * 按用户裁决砍除；立绘显示层背景仍固定纯白（旧裁决延续：白底消除立绘矩形边界，
 * 夜间以灯箱质感成立）。
 */

// ---- 基础原语（静态；日夜分支见下方访问器） ----
internal val GxWhite = Color(0xFFFFFFFF)
internal val GxBlack = Color(0xFF000000)
internal val GxDeepGray = Color(0xFF1A1A1A)      // 睡眠中胶囊底/夜间卡片
internal val GxNightCanvas = Color(0xFF121212)   // 夜间画布（纯中性黑，无蓝调）
internal val GxNightHairline = Color(0xFF3A3A3A) // 夜间次级描边
internal val GxDayHairline = Color(0xFFE5E5E5)   // 日间次级分隔线（比卡片黑边低一级）
internal val GxDayMuted = Color(0xFF666666)
internal val GxNightMuted = Color(0xFFB8B8B8)
internal val GxDayMutedSoft = Color(0xFF8A8A8A)
internal val GxNightMutedSoft = Color(0xFF8E8E8E)

// ---- 点缀三色（低饱和；日夜同值——两种底面都可辨） ----
internal val AccentBlue = Color(0xFF7B8FA1)      // 雾霾蓝
internal val AccentSage = Color(0xFF9CAF88)      // 鼠尾草绿
internal val AccentTaupe = Color(0xFFC4A882)     // 暖灰褐

// ---- 语义色访问器（@Composable：跟随系统日/夜） ----

/** 页面画布：日间纯白 #FFFFFF / 夜间 #121212 */
@Composable internal fun PageBg(): Color =
    if (androidx.compose.foundation.isSystemInDarkTheme()) GxNightCanvas else GxWhite

/** 卡片/气泡底：日间纯白（配 1dp 黑描边成卡）/ 夜间近黑 #1A1A1A */
@Composable internal fun CardBg(): Color =
    if (androidx.compose.foundation.isSystemInDarkTheme()) GxDeepGray else GxWhite

/** 反色面（我的气泡/主 CTA 底）：日间黑 / 夜间白（黑白关系整体翻转） */
@Composable internal fun InvertSurface(): Color =
    if (androidx.compose.foundation.isSystemInDarkTheme()) GxWhite else GxBlack

/** 反色面上的文字 */
@Composable internal fun OnInvert(): Color =
    if (androidx.compose.foundation.isSystemInDarkTheme()) GxBlack else GxWhite

/** 主文本/标题/主强调色：黑（夜间白）——原 Coral（珊瑚）的角色位由黑白接管 */
@Composable internal fun PrimaryInk(): Color =
    if (androidx.compose.foundation.isSystemInDarkTheme()) GxWhite else GxBlack

/** 正文：纯黑保证无障碍对比（夜间纯白） */
@Composable internal fun Body(): Color = PrimaryInk()

/** 次级文字 */
@Composable internal fun Muted(): Color =
    if (androidx.compose.foundation.isSystemInDarkTheme()) GxNightMuted else GxDayMuted

/** 三级（状态行/时间戳） */
@Composable internal fun MutedSoft(): Color =
    if (androidx.compose.foundation.isSystemInDarkTheme()) GxNightMutedSoft else GxDayMutedSoft

/** 反色面底（旧 SurfaceDark 语义：我的气泡底） */
@Composable internal fun SurfaceDark(): Color = InvertSurface()
@Composable internal fun OnDark(): Color = OnInvert()

/** 反色面上的次级文字（时间戳等） */
@Composable internal fun OnDarkSoft(): Color =
    if (androidx.compose.foundation.isSystemInDarkTheme()) Color(0xFF666666) else Color(0xFFBBBBBB)

/** 分隔线/输入框常态描边 */
@Composable internal fun Hairline(): Color =
    if (androidx.compose.foundation.isSystemInDarkTheme()) GxNightHairline else GxDayHairline

/** 卡片描边：设计稿=黑色细边框 1dp（夜间转白维持边界可读） */
@Composable internal fun CardBorder(): Color = PrimaryInk()

/** 环形/条形的底轨色（比 Hairline 更中性的填充感） */
@Composable internal fun TrackBg(): Color =
    if (androidx.compose.foundation.isSystemInDarkTheme()) GxNightHairline else Color(0xFFEFEFEF)

// 显示字体：Galaxy 标准全站无衬线（用户裁决 2026-09-01：衬线换无衬线）
internal val DisplayFont: FontFamily = FontFamily.SansSerif

@Composable
private fun Scheme() = if (androidx.compose.foundation.isSystemInDarkTheme()) {
    darkColorScheme(
        primary = PrimaryInk(), onPrimary = OnInvert(),
        background = PageBg(), onBackground = Body(),
        surface = PageBg(), onSurface = Body(),
        surfaceVariant = CardBg(), onSurfaceVariant = Muted(),
        outline = Hairline(), outlineVariant = Hairline(),
        secondary = MutedSoft(),
        inverseSurface = InvertSurface(), inverseOnSurface = OnInvert(),
    )
} else {
    lightColorScheme(
        primary = PrimaryInk(), onPrimary = OnInvert(),
        background = PageBg(), onBackground = Body(),
        surface = PageBg(), onSurface = Body(),
        surfaceVariant = CardBg(), onSurfaceVariant = Muted(),
        outline = Hairline(), outlineVariant = Hairline(),
        secondary = MutedSoft(),
        inverseSurface = InvertSurface(), inverseOnSurface = OnInvert(),
    )
}

// M3 组件（Button/Surface 等）内部吃 colorScheme 的静态场景：主题包装时预取一份
private val Type = Typography(
    headlineSmall = TextStyle(fontFamily = DisplayFont, fontSize = 24.sp,
        fontWeight = FontWeight.SemiBold, letterSpacing = (-0.2).sp),
    titleLarge = TextStyle(fontFamily = DisplayFont, fontSize = 20.sp,
        fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontWeight = FontWeight.Medium, fontSize = 18.sp),
    titleSmall = TextStyle(fontWeight = FontWeight.Medium, fontSize = 16.sp),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontSize = 15.sp, lineHeight = 23.sp),
    bodySmall = TextStyle(fontSize = 13.sp),
    labelLarge = TextStyle(fontWeight = FontWeight.Medium, fontSize = 14.sp),
)

private val Shapes = androidx.compose.material3.Shapes(
    extraSmall = RoundedCornerShape(6.dp),
    small = RoundedCornerShape(8.dp),      // 按钮/输入框 rounded.md
    medium = RoundedCornerShape(12.dp),    // 卡片/气泡 rounded.lg（设计稿统计卡=12dp）
    large = RoundedCornerShape(16.dp),
    extraLarge = RoundedCornerShape(24.dp),
)

@Composable
fun VeranimaTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = Scheme(),
        typography = Type, shapes = Shapes, content = content)
}
