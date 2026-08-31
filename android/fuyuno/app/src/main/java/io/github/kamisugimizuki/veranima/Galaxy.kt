package io.github.kamisugimizuki.veranima

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Galaxy（Uiverse.io）黑白极简 · 复用组件库（2026-09-01 UI 重构设计稿 §四）。
 * 色板唯一源在 Theme.kt（全站黑白 + 低饱和三点缀，日夜自适应）；本文件只放组件。
 * 动画克制：仅呼吸脉冲（睡眠胶囊）与环形进度加载，其余一律静态。
 */

// 兼容别名（部分组件按传入 dark 布尔分支时使用的纯函数）
internal fun gxTextPrimary(dark: Boolean) = if (dark) GxWhite else GxBlack
internal fun gxTextSecondary(dark: Boolean) = if (dark) GxNightMuted else GxDayMuted
internal fun gxHairline(dark: Boolean) = if (dark) GxNightHairline else GxBlack

/** 页面骨架：左上角统一「<」返回 + Galaxy 主标题（20sp SemiBold） */
@Composable
internal fun GalaxyPage(
    title: String,
    onBack: () -> Unit,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        Modifier
            .fillMaxSize()
            .background(PageBg())
            .statusBarsPadding()
    ) {
        Row(verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(horizontal = 4.dp)) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.KeyboardArrowLeft, contentDescription = "返回",
                    tint = PrimaryInk())
            }
            Text(title, fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = PrimaryInk())
        }
        content()
    }
}

/** 统计卡片容器：设计稿规范——纯白卡 / 黑色细边框 1dp / 圆角 12dp / 零阴影（夜间反色） */
@Composable
internal fun GalaxyCard(
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier
            .background(CardBg(), RoundedCornerShape(12.dp))
            .border(1.dp, CardBorder(), RoundedCornerShape(12.dp))
            .padding(16.dp),
        content = content,
    )
}

/** 数值大号展示（设计稿：32sp Bold；长值自动降档防三格挤压截断） */
@Composable
internal fun GalaxyBigNumber(value: String, label: String = "", modifier: Modifier = Modifier) {
    val size = when {
        value.length <= 5 -> 32.sp
        value.length <= 8 -> 24.sp
        else -> 19.sp
    }
    Column(modifier) {
        Text(value, fontSize = size, fontWeight = FontWeight.Bold,
            color = PrimaryInk(), maxLines = 1,
            overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
        if (label.isNotEmpty()) {
            Text(label, fontSize = 12.sp, color = Muted())
        }
    }
}

/** 点缀色标签：半透明低饱和底 + 同色系文字（设计稿组件规范第 4 行） */
@Composable
internal fun GalaxyTag(text: String, accent: Color, modifier: Modifier = Modifier) {
    Text(
        text,
        fontSize = 11.sp,
        color = accent,
        modifier = modifier
            .background(accent.copy(alpha = 0.2f), RoundedCornerShape(4.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

/** 环形进度：弧段填充 + 中央百分比 + 下方名称。加载时从 0 扫到目标值一次（克制动效） */
@Composable
internal fun GalaxyRing(
    fraction: Float,
    label: String,
    accent: Color,
    size: androidx.compose.ui.unit.Dp = 96.dp,
    centerText: String = "${(fraction * 100).toInt()}%",
) {
    val track = TrackBg()
    val progress = androidx.compose.animation.core.animateFloatAsState(
        targetValue = fraction.coerceIn(0f, 1f),
        animationSpec = tween(900, easing = LinearEasing), label = "ring",
    ).value
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(contentAlignment = Alignment.Center, modifier = Modifier.size(size)) {
            Canvas(Modifier.size(size)) {
                val stroke = size.toPx() * 0.075f
                val inset = stroke / 2 + 2f
                drawArc(
                    color = track,
                    startAngle = 0f, sweepAngle = 360f, useCenter = false,
                    topLeft = androidx.compose.ui.geometry.Offset(inset, inset),
                    size = Size(size.toPx() - inset * 2, size.toPx() - inset * 2),
                    style = Stroke(width = stroke),
                )
                drawArc(
                    color = accent,
                    startAngle = -90f, sweepAngle = 360f * progress, useCenter = false,
                    topLeft = androidx.compose.ui.geometry.Offset(inset, inset),
                    size = Size(size.toPx() - inset * 2, size.toPx() - inset * 2),
                    style = Stroke(width = stroke, cap = StrokeCap.Round),
                )
            }
            Text(centerText, fontSize = (size.value * 0.19f).sp, fontWeight = FontWeight.Bold,
                color = PrimaryInk())
        }
        Spacer(Modifier.height(6.dp))
        Text(label, fontSize = 12.sp, color = Muted(), textAlign = TextAlign.Center)
    }
}

/** 环形分布图（分段占比）：多段弧 + 间隙；无动画（数据图） */
@Composable
internal fun GalaxyDonut(
    segments: List<Pair<String, Float>>,  // (名称, 数量)
    colors: List<Color>,
    size: androidx.compose.ui.unit.Dp = 140.dp,
    centerTop: String = "",
    centerBottom: String = "",
) {
    val total = segments.sumOf { it.second.toDouble() }.toFloat()
    val emptyTrack = TrackBg()
    Box(contentAlignment = Alignment.Center, modifier = Modifier.size(size)) {
        Canvas(Modifier.size(size)) {
            val stroke = size.toPx() * 0.16f
            val inset = stroke / 2 + 2f
            val arcSize = Size(size.toPx() - inset * 2, size.toPx() - inset * 2)
            if (total <= 0f) {
                drawArc(color = emptyTrack, 0f, 360f,
                    false, androidx.compose.ui.geometry.Offset(inset, inset), arcSize,
                    style = Stroke(width = stroke))
            } else {
                var start = -90f
                segments.forEachIndexed { i, (_, v) ->
                    if (v > 0f) {
                        val sweep = (v / total) * 360f
                        drawArc(colors.getOrElse(i) { GxBlack }, start, (sweep - 2f).coerceAtLeast(0.5f),
                            false, androidx.compose.ui.geometry.Offset(inset, inset), arcSize,
                            style = Stroke(width = stroke, cap = StrokeCap.Butt))
                        start += sweep
                    }
                }
            }
        }
        if (centerTop.isNotEmpty() || centerBottom.isNotEmpty()) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                if (centerTop.isNotEmpty())
                    Text(centerTop, fontSize = 24.sp, fontWeight = FontWeight.Bold, color = PrimaryInk())
                if (centerBottom.isNotEmpty())
                    Text(centerBottom, fontSize = 11.sp, color = Muted())
            }
        }
    }
}

/** 图例：低饱和小圆点 + 文字 */
@Composable
internal fun GalaxyLegend(name: String, value: String, color: Color, modifier: Modifier = Modifier) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = modifier) {
        Box(Modifier.size(8.dp).background(color, CircleShape))
        Spacer(Modifier.width(6.dp))
        Text(name, fontSize = 12.sp, color = Muted())
        if (value.isNotEmpty()) {
            Spacer(Modifier.width(4.dp))
            Text(value, fontSize = 12.sp, fontWeight = FontWeight.Medium, color = PrimaryInk())
        }
    }
}

/** 趋势微标：↑鼠尾草绿 ↓灰（设计稿：绿/灰小箭头+数字；无对比数据=「—」不编造） */
@Composable
internal fun GalaxyTrend(delta: Int?, modifier: Modifier = Modifier) {
    if (delta == null) {
        Text("较昨日 —", fontSize = 11.sp, color = Muted(), modifier = modifier)
        return
    }
    val (arrow, c) = when {
        delta > 0 -> "↑" to AccentSage
        delta < 0 -> "↓" to Muted()
        else -> "→" to Muted()
    }
    Text("较昨日 ${if (delta > 0) "+" else ""}$delta% $arrow",
        fontSize = 11.sp, color = c, modifier = modifier)
}

/** 横向分段进度条（睡眠阶段分布）：色块按占比拼接，播放器式分色 */
@Composable
internal fun GalaxySegmentedBar(
    segments: List<Pair<String, Float>>,  // (阶段名, 占比 0..1；内部归一)
    colors: List<Color>,
    height: androidx.compose.ui.unit.Dp = 14.dp,
) {
    val track = TrackBg()
    val total = segments.sumOf { it.second.toDouble() }.toFloat()
    Row(
        Modifier.fillMaxWidth().height(height)
            .background(track, RoundedCornerShape(height / 2))
            .clip(RoundedCornerShape(height / 2)),
    ) {
        if (total <= 0f) return@Row
        segments.forEachIndexed { i, (_, v) ->
            if (v > 0f) {
                Box(Modifier.weight(v).fillMaxHeight()
                    .background(colors.getOrElse(i) { GxBlack }))
            }
        }
    }
}

/** 极简折线（7 日趋势；仅黑白灰线条，设计稿 §三页面2） */
@Composable
internal fun GalaxySparkline(
    values: List<Float>,       // 每项 0..1
    modifier: Modifier = Modifier,
) {
    val dark = androidx.compose.foundation.isSystemInDarkTheme()
    val lineColor = if (dark) Color(0xFF9AA0A6) else Color(0xFF666666)
    Canvas(modifier.fillMaxWidth().height(48.dp)) {
        if (values.size < 2) return@Canvas
        val stepX = size.width / (values.size - 1)
        val pad = size.height * 0.12f
        val strokeW = 3f
        val pts = values.mapIndexed { i, v ->
            androidx.compose.ui.geometry.Offset(i * stepX,
                size.height - pad - v.coerceIn(0f, 1f) * (size.height - pad * 2))
        }
        val line = Path().apply {
            moveTo(pts.first().x, pts.first().y)
            pts.drop(1).forEach { lineTo(it.x, it.y) }
        }
        drawPath(line, color = lineColor, style = Stroke(width = strokeW, cap = StrokeCap.Round))
        // 末点标记（黑/白实心+灰环）
        drawCircle(lineColor, radius = 6f, center = pts.last())
        drawCircle(if (dark) GxWhite else GxBlack, radius = 4f, center = pts.last())
    }
}

/** 呼吸脉冲胶囊（睡眠状态）：缩放 0.96~1.04 缓慢往复——设计稿唯一保留的循环动效 */
@Composable
internal fun BreathingPill(text: String, night: Boolean, modifier: Modifier = Modifier) {
    val transition = rememberInfiniteTransition(label = "breath")
    val scale by transition.animateFloat(
        initialValue = 0.96f, targetValue = 1.04f,
        animationSpec = infiniteRepeatable(tween(2600, easing = LinearEasing), RepeatMode.Reverse),
        label = "breath-scale",
    )
    val alphaVal by transition.animateFloat(
        initialValue = 0.82f, targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(2600, easing = LinearEasing), RepeatMode.Reverse),
        label = "breath-alpha",
    )
    Box(
        modifier
            .scale(scale)
            .alpha(alphaVal)
            .background(if (night) GxDeepGray else CardBg(), RoundedCornerShape(999.dp))
            .border(1.dp, CardBorder(), RoundedCornerShape(999.dp))
            .padding(horizontal = 20.dp, vertical = 10.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, fontSize = 14.sp, fontWeight = FontWeight.Medium,
            color = if (night) GxWhite else PrimaryInk(), maxLines = 1)
    }
}

/** 设置页跳转用的可点击卡片行：左图标 + 标题 + 右「>」箭头（设计稿 §二.2） */
@Composable
internal fun GalaxyNavRow(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    title: String,
    subtitle: String,
    onClick: () -> Unit,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .background(CardBg(), RoundedCornerShape(12.dp))
            .border(1.dp, CardBorder(), RoundedCornerShape(12.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 14.dp),
    ) {
        Icon(icon, contentDescription = null, tint = PrimaryInk(), modifier = Modifier.size(22.dp))
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 15.sp, fontWeight = FontWeight.Medium, color = PrimaryInk())
            if (subtitle.isNotEmpty())
                Text(subtitle, fontSize = 12.sp, color = Muted())
        }
        Text("›", fontSize = 20.sp, color = Muted())
    }
}
