package io.github.kamisugimizuki.veranima

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Bundle
import android.provider.Settings as AndSettings
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.lifecycle.lifecycleScope
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import dev.chrisbanes.haze.ExperimentalHazeApi
import dev.chrisbanes.haze.HazeState
import dev.chrisbanes.haze.haze
import dev.chrisbanes.haze.hazeChild
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

data class Msg(val id: Long, val me: Boolean, val text: String, val images: List<String> = emptyList(),
               val time: String = "", val tone: String = "", val mood: String = "")

// 图片图标（手画 24dp：圆角相框+山+太阳；不引 material-icons-extended 整个包）
internal val PhotoIcon: androidx.compose.ui.graphics.vector.ImageVector by lazy {
    androidx.compose.ui.graphics.vector.ImageVector.Builder(
        defaultWidth = 24.dp, defaultHeight = 24.dp, viewportWidth = 24f, viewportHeight = 24f
    ).apply {
        // 相框（圆角矩形描边→用 fill 加内框）；Galaxy：静态 vector 用中性灰原语（GxDayMutedSoft）
        path(fill = androidx.compose.ui.graphics.SolidColor(GxDayMutedSoft)) {
            moveTo(4f, 5.5f)
            curveTo(4f, 4.67f, 4.67f, 4f, 5.5f, 4f)
            horizontalLineTo(18.5f)
            curveTo(19.33f, 4f, 20f, 4.67f, 20f, 5.5f)
            verticalLineTo(18.5f)
            curveTo(20f, 19.33f, 19.33f, 20f, 18.5f, 20f)
            horizontalLineTo(5.5f)
            curveTo(4.67f, 20f, 4f, 19.33f, 4f, 18.5f)
            close()
        }
        path(fill = androidx.compose.ui.graphics.SolidColor(GxWhite)) {
            moveTo(6.5f, 7.5f)
            curveTo(6.5f, 6.95f, 6.95f, 6.5f, 7.5f, 6.5f)
            horizontalLineTo(16.5f)
            curveTo(17.05f, 6.5f, 17.5f, 6.95f, 17.5f, 7.5f)
            verticalLineTo(16.5f)
            curveTo(17.5f, 17.05f, 17.05f, 17.5f, 16.5f, 17.5f)
            horizontalLineTo(7.5f)
            curveTo(6.95f, 17.5f, 6.5f, 17.05f, 6.5f, 16.5f)
            close()
        }
        // 山+太阳
        path(fill = androidx.compose.ui.graphics.SolidColor(GxDayMutedSoft)) {
            moveTo(9f, 9f)
            arcTo(1.6f, 1.6f, 0f, true, false, 12.2f, 9f)
            arcTo(1.6f, 1.6f, 0f, true, false, 9f, 9f)
            moveTo(8.4f, 14.6f)
            lineTo(10.8f, 11.9f)
            lineTo(13.4f, 14.4f)
            lineTo(16f, 11.5f)
            lineTo(18f, 16f)
            horizontalLineTo(6.6f)
            close()
        }
    }.build()
}

// ---------- 舞台部件 ----------

/** P2 情绪标签文案：tone 词表原样显示；回退 mood 三档图标+词。
 *  Galaxy 配色（2026-09-01）：tone 一律雾霾蓝/暖灰褐两档语义（暖组→褐、冷组→蓝），
 *  不再维护 19 词色表——黑白极简下情绪靠词本身，不靠彩虹。 */
private fun emotionLabel(tone: String, mood: String): Pair<String, Color> {
    if (tone.isNotEmpty()) return tone to AccentBlue
    return when (mood) {
        "开心" -> "✨ 心情不错" to AccentTaupe
        "低落" -> "🌧 有点闷" to AccentBlue
        else -> "💭 平静" to GxDayMutedSoft
    }
}

/** P2 情绪标签徽章（圆角小胶囊，随回复到达 α 淡入） */
@Composable
internal fun EmotionBadge(tone: String, mood: String) {
    val (label, color) = emotionLabel(tone, mood)
    if (label.isEmpty()) return
    val alpha by animateFloatAsState(if (label.isNotEmpty()) 1f else 0f,
        animationSpec = tween(220), label = "badge")
    Surface(
        color = color.copy(alpha = 0.12f),
        shape = RoundedCornerShape(999.dp),
        modifier = Modifier.graphicsLayer { this.alpha = alpha })
    {
        Text(label, style = MaterialTheme.typography.labelSmall,
            color = color, modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp))
    }
}

/** P3 思考粒子：3 粒子上抛 8dp 错峰 0.25s + 「正在酝酿…」（替代转圈） */
@Composable
internal fun ThinkingParticles() {
    val t = rememberInfiniteTransition(label = "thinking")
    Row(verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 2.dp)) {
        repeat(3) { i ->
            val y by t.animateFloat(0f, -8f, label = "dot$i",
                animationSpec = infiniteRepeatable(
                    tween(800, delayMillis = i * 250, easing = FastOutSlowInEasing),
                    RepeatMode.Reverse))
            Box(Modifier.size(5.dp).offset(y = y.dp).graphicsLayer { alpha = 0.6f - i * 0.15f }
                .background(PrimaryInk(), RoundedCornerShape(50)))
            Spacer(Modifier.width(5.dp))
        }
        Text("正在酝酿…", style = MaterialTheme.typography.bodySmall, color = MutedSoft())
    }
}

/** 解码图片并按目标边长降采样（历史缩略/点击放大共用） */
internal fun decodeSampled(path: String, targetPx: Int): Bitmap? = try {
    val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(path, opts)
    var ss = 1
    while (opts.outWidth / (ss * 2) > targetPx && opts.outHeight / (ss * 2) > targetPx) ss *= 2
    BitmapFactory.decodeFile(path, BitmapFactory.Options().apply { inSampleSize = ss })
} catch (e: Exception) { null }

@Composable
internal fun ImageThumb(path: String, maxW: androidx.compose.ui.unit.Dp, onClick: (() -> Unit)? = null) {
    var failed by remember(path) { mutableStateOf(false) }
    if (failed) {
        Text("[图片读取失败]", style = MaterialTheme.typography.bodySmall)
        return
    }
    // Coil：内存缓存 + 异步解码（LazyColumn 滚动/重组合不再反复磁盘解码）
    coil.compose.AsyncImage(
        model = java.io.File(path),
        contentDescription = "图片",
        contentScale = ContentScale.Fit,
        onError = { failed = true },
        modifier = Modifier.padding(bottom = 4.dp).widthIn(max = maxW).heightIn(max = maxW)
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier),
    )
}

/** 双击返回提示。 */
@android.annotation.SuppressLint("ShowToast")
internal fun toast(ctx: android.content.Context, msg: String) {
    android.widget.Toast.makeText(ctx, msg, android.widget.Toast.LENGTH_SHORT).show()
}

/** 点击放大：全屏暗底，点任意处关闭 */
@Composable
internal fun ZoomDialog(path: String, onDismiss: () -> Unit) {
    val bmp = remember(path) { decodeSampled(path, 2048) }
    Dialog(onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.92f))
                .clickable(onClick = onDismiss),
            contentAlignment = Alignment.Center) {
            if (bmp != null) Image(bmp.asImageBitmap(), "图片大图",
                Modifier.fillMaxSize().padding(12.dp), contentScale = ContentScale.Fit)
        }
    }
}

/** 打字机：每字时长 = clamp(字数×38ms, 1.2s, 5.5s)/字数；系统动画缩放=0 时直出 */
@Composable
private fun TypewriterText(text: String, animate: Boolean, animScale: Float, style: TextStyle) {
    var count by remember(text) { mutableIntStateOf(text.length) }
    LaunchedEffect(text) {
        if (!animate || animScale == 0f || text.length <= 1) { count = text.length; return@LaunchedEffect }
        count = 0
        val perChar = (((text.length * 38L).coerceIn(1200L, 5500L) * animScale / text.length)
            .coerceAtLeast(8f)).toInt().coerceAtLeast(1)
        while (count < text.length) { delay(perChar.toLong()); count++ }
    }
    Text(text.take(count), style = style)
}

@OptIn(ExperimentalMaterial3Api::class)
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!Python.isStarted()) Python.start(AndroidPlatform(this))
        val bridge = Python.getInstance().getModule("bridge")
        // Android 13+ 通知权限（不给则主动消息静默丢弃，故必须请求）
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) !=
                android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        androidx.core.content.ContextCompat.startForegroundService(
            this, android.content.Intent(this, CompanionService::class.java))
        // 核心 boot 挂进程起点（P1 重构：boot 曾孤儿化在会话页里，而列表页
        // 数据源依赖 boot → 死锁空列表）。异步起，不阻塞首帧。
        lifecycleScope.launch(Dispatchers.IO) {
            val files = filesDir.absolutePath
            val r = bridge.callAttr("boot", files).toString()
            android.util.Log.i("VeranimaBoot", "boot: $r")
            bridge.callAttr("start_ticks")
        }
        setContent {
            VeranimaTheme {
                AppRoot()
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun ChatScreen(role: String, onBack: () -> Unit, onOpenSpace: () -> Unit) {
    val bridge = remember { Python.getInstance().getModule("bridge") }
    val ctx = LocalContext.current
    val activity = ctx as? android.app.Activity
    val msgs = remember { mutableStateListOf<Msg>() }
    val status = remember { mutableStateOf("") }
    val charName = remember { mutableStateOf(role) }
    val portraitPath = remember { mutableStateOf("") }
    val input = remember { mutableStateOf("") }
    val busy = remember { mutableStateOf(false) }
    // P2 情绪：最新一条回复的 tone/mood（驱动情绪标签+环境光；空→默认平静）
    val lastTone = remember { mutableStateOf("") }
    val lastMood = remember { mutableStateOf("") }
    val pendingImages = remember { mutableStateOf(listOf<String>()) }
    val expanded = remember { mutableStateOf(false) }  // 面板两态：收起=最新一轮 / 展开=全历史
    val typedIds = remember { mutableSetOf<Long>() }   // 首载已有消息不做打字机
    val zoom = remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val focusManager = LocalFocusManager.current
    val haptic = LocalHapticFeedback.current
    // 横幅→气泡同步：CompanionService 发主动消息通知时广播，这里收后刷新历史
    // （core 已落库，UI 以 DB 为准；开着应用时通知弹出=气泡同步出现）
    val proactiveTick = remember { mutableStateOf(0) }
    val proactiveReceiver = remember {
        object : android.content.BroadcastReceiver() {
            override fun onReceive(c: android.content.Context?, i: android.content.Intent?) {
                if (i?.action == CompanionService.ACTION_PROACTIVE) {
                    proactiveTick.value++
                }
            }
        }
    }
    DisposableEffect(Unit) {
        androidx.core.content.ContextCompat.registerReceiver(
            ctx, proactiveReceiver,
            android.content.IntentFilter(CompanionService.ACTION_PROACTIVE),
            androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED)
        onDispose { runCatching { ctx.unregisterReceiver(proactiveReceiver) } }
    }
    val animScale = remember {
        try { AndSettings.Global.getFloat(ctx.contentResolver, AndSettings.Global.ANIMATOR_DURATION_SCALE, 1f) }
        catch (e: Exception) { 1f }
    }
    val conf = LocalConfiguration.current
    val screenW = conf.screenWidthDp
    val screenH = conf.screenHeightDp

    val showAlbumPicker = remember { mutableStateOf(false) }
    // 相册读取权限：33+ READ_MEDIA_IMAGES，否则 READ_EXTERNAL_STORAGE
    val albumPerm = if (android.os.Build.VERSION.SDK_INT >= 33)
        android.Manifest.permission.READ_MEDIA_IMAGES
    else android.Manifest.permission.READ_EXTERNAL_STORAGE
    val permLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission()
    ) { granted -> if (granted) showAlbumPicker.value = true }
    val saveToPhotos = fun(uris: List<android.net.Uri>) {
        if (uris.isEmpty()) return
        scope.launch(Dispatchers.IO) {
            val saved = uris.mapNotNull { uri ->
                try {  // 存 filesDir/photos：cacheDir 随时被系统清，历史图会变黑块
                    val bytes = ctx.contentResolver
                        .openInputStream(uri)?.use { it.readBytes() } ?: return@mapNotNull null
                    val dir = java.io.File(ctx.filesDir, "photos").apply { mkdirs() }
                    val f = java.io.File(dir, "img_${System.nanoTime()}.bin")
                    f.writeBytes(bytes); f.absolutePath
                } catch (e: Exception) { null }
            }
            withContext(Dispatchers.Main) { pendingImages.value = saved }
        }
    }

    // 聊天区以数据库为准（主动消息核心已落库），启动/回前台/收 reply 后刷新
    val loadHistory = fun() {
        scope.launch {
            val o = JSONObject(
                withContext(Dispatchers.IO) { bridge.callAttr("history", 80, role).toString() })
            if (o.optBoolean("ok")) {
                val arr = o.getJSONArray("messages")
                msgs.clear()
                for (i in 0 until arr.length()) {
                    val m = arr.getJSONObject(i)
                    val imgs = mutableListOf<String>()
                    m.optJSONArray("images")?.let { ia -> for (j in 0 until ia.length()) imgs.add(ia.getString(j)) }
                    msgs.add(Msg(m.getLong("id"), m.getBoolean("me"), m.getString("text"), imgs,
                        m.optString("time"), m.optString("tone"), m.optString("mood")))
                }
                if (typedIds.isEmpty()) msgs.forEach { typedIds.add(it.id) }  // 首载快照：旧消息不重演
                // P2：最新一条 assistant 的 tone/mood 驱动环境光（收起态展示）
                msgs.lastOrNull { !it.me }?.let { lastMood.value = it.mood; lastTone.value = it.tone }
            }
        }
    }
    LaunchedEffect(role) {
        charName.value = withContext(Dispatchers.IO) { bridge.callAttr("role_label", role).toString() }
        portraitPath.value = withContext(Dispatchers.IO) { bridge.callAttr("portrait_path", role).toString() }
        loadHistory()
        withContext(Dispatchers.IO) { bridge.callAttr("mark_read", role) }
    }

    val leOwner = androidx.lifecycle.compose.LocalLifecycleOwner.current
    DisposableEffect(leOwner) {
        val obs = androidx.lifecycle.LifecycleEventObserver { _, ev ->
            if (ev == androidx.lifecycle.Lifecycle.Event.ON_RESUME) loadHistory()
        }
        leOwner.lifecycle.addObserver(obs)
        onDispose { leOwner.lifecycle.removeObserver(obs) }
    }
    // 横幅→气泡：主动消息通知广播到达时刷新（避免 30s 轮询空窗）
    LaunchedEffect(proactiveTick.value) {
        if (proactiveTick.value > 0) loadHistory()
    }

    val send = fun() {
        val q = input.value.trim()
        val imgs = pendingImages.value
        if ((q.isEmpty() && imgs.isEmpty()) || busy.value) return
        msgs.add(Msg(-1, true, q, imgs)); input.value = ""
        pendingImages.value = emptyList()
        focusManager.clearFocus()  // 发送后收起键盘，别挡消息
        busy.value = true
        // P3 触感：发送成功一次轻震
        haptic.performHapticFeedback(HapticFeedbackType.LongPress)
        scope.launch {
            val r = withContext(Dispatchers.IO) {
                bridge.callAttr("chat", q, org.json.JSONArray(imgs).toString(), role).toString()
            }
            val o = JSONObject(r)
            if (o.optBoolean("ok")) {
                msgs.add(Msg(-2, false, o.getString("reply"), emptyList(), "", o.optString("tone"), ""))
                lastTone.value = o.optString("tone")
                // 触感：收到回复一次轻震（P3）
                haptic.performHapticFeedback(HapticFeedbackType.LongPress)
            }
            else status.value = "chat 失败: ${o.optString("error")}"
            busy.value = false
            loadHistory()  // 拿真实 id（打字机判定用）+ mood/tone 对齐
        }
    }

    val portraitBmp by produceState<Bitmap?>(initialValue = null, portraitPath.value) {
        value = if (portraitPath.value.isNotEmpty())
            withContext(Dispatchers.IO) { decodeSampled(portraitPath.value, 1024) } else null
    }
    // 微呼吸：±3dp 上下浮动 2.8s 往返（动画缩放=0 时静止）
    val transition = rememberInfiniteTransition(label = "stage")
    val breath by transition.animateFloat(0f, 1f, label = "breath",
        animationSpec = infiniteRepeatable(tween(2800, easing = FastOutSlowInEasing), RepeatMode.Reverse))
    val breathDp = if (animScale == 0f) 0f else (breath - 0.5f) * 6f

    // 面板高度：base(档位) + dragOff(手指偏移)；拖动中 0ms 动画=严格跟手，
    // 松手后 260ms 平滑归档（drag.y 是 px，screenH 是 dp——dragOff 必须转 dp，
    // 否则分界线移动量被 density 倍放大，2026-08-30 用户实测距离对不上）
    val density = LocalDensity.current.density
    var dragOff by remember { mutableFloatStateOf(0f) }
    var dragging by remember { mutableStateOf(false) }
    val baseH = if (expanded.value) screenH * 0.70f else screenH * 0.40f
    val panelH by animateFloatAsState(
        baseH + dragOff,
        animationSpec = tween(if (dragging) 0 else 260, easing = FastOutSlowInEasing),
        label = "panel")

    androidx.activity.compose.BackHandler { onBack() }

        val hazeState = remember { HazeState() }
        val panelShape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp)
        @OptIn(ExperimentalHazeApi::class)
        fun Modifier.stageHaze() = this.haze(hazeState)
        val panelTint = CardBg().copy(alpha = 0.92f)  // Composable 上下文预取（日夜自适应）
        @OptIn(ExperimentalHazeApi::class)
        fun Modifier.panelHaze() = this.hazeChild(
            state = hazeState,
            shape = panelShape,
            style = dev.chrisbanes.haze.HazeStyle(
                backgroundColor = panelTint,
                tints = emptyList(),
                blurRadius = 24.dp))
        Box(Modifier.fillMaxSize().background(PageBg())) {
            // ---- L1 立绘舞台（Galaxy：环境光层已按 2026-09-01 裁决砍除，纯色画布） ----
            // 立绘背后固定白（旧裁决延续）：白底图消除矩形边界；夜间以灯箱质感成立
            Column(Modifier.fillMaxSize().stageHaze(), horizontalAlignment = Alignment.CenterHorizontally) {
                if (portraitBmp != null) {
                    val imgW = (screenW * 0.86f).dp
                    // 舞台区 bottom padding 绑定 panelH：面板上滑时立绘随之整体上移并在
                    // 剩余可见区内自动缩放，永不被聊天框遮挡（2026-08-29 用户追加）
                    Box(Modifier.fillMaxWidth().weight(1f).padding(bottom = panelH.dp),
                        contentAlignment = Alignment.TopCenter) {
                        Box(Modifier.width(imgW).offset(y = breathDp.dp)) {
                            Image(portraitBmp!!.asImageBitmap(), charName.value,
                                Modifier.fillMaxWidth().fillMaxHeight(), contentScale = ContentScale.Fit)
                            // 脚部渐变遮罩：立绘融入画布，消除悬浮感
                            Box(Modifier.fillMaxWidth().height(90.dp).align(Alignment.BottomCenter)
                                .background(Brush.verticalGradient(listOf(Color.Transparent, Color.White))))
                        }
                    }
                } else {
                    Spacer(Modifier.weight(1f))
                }
            }
            // boot 诊断浮层（仅异常时显示；正常启动为空，不占舞台高度）
            if (status.value.isNotEmpty()) {
                Text(status.value, style = MaterialTheme.typography.bodySmall, color = MutedSoft(),
                    modifier = Modifier.align(Alignment.TopStart).padding(start = 12.dp, top = 4.dp))
            }
            // ---- L3 毛玻璃对话面板（两态；面板高度即立绘可见区，天然适配长宽比） ----
            var dragTotal by remember { mutableFloatStateOf(0f) }
            Box(Modifier.align(Alignment.BottomCenter).fillMaxWidth().height(panelH.dp)
                    .panelHaze()   // 背景由 hazeChild 画（含模糊+底色），不再单独 background
                    .pointerInput(Unit) {
                        detectDragGestures(
                            onDragStart = {
                                dragTotal = 0f
                                dragging = true
                            },
                            onDragEnd = {
                                dragging = false
                                dragOff = 0f
                                if (dragTotal < -60f) expanded.value = true
                                else if (dragTotal > 60f) expanded.value = false
                            },
                            onDragCancel = {
                                dragging = false
                                dragOff = 0f
                            },
                            onDrag = { _, drag ->
                                dragTotal += drag.y
                                // 上滑 drag.y<0 → 分界线随手指上移（高度增大）；限幅 25%-85%
                                dragOff = (dragOff - drag.y / density).coerceIn(
                                    -(baseH - screenH * 0.25f), screenH * 0.85f - baseH)
                            })
                    }) {
                Column(Modifier.fillMaxSize()) {
                    // 把手行：返回｜角色名 + 拖拽指示｜齿轮（P1：会话页可见返回入口）
                    Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically) {
                        TextButton(onClick = onBack) { Text("‹", fontSize = 22.sp, color = Muted()) }
                        Text(charName.value, style = MaterialTheme.typography.headlineSmall,
                            modifier = Modifier.weight(1f))
                        Box(Modifier.width(36.dp).height(4.dp)
                            .background(Hairline(), RoundedCornerShape(2.dp)))
                        IconButton(onClick = onOpenSpace) {
                            Icon(Icons.Filled.Settings,
                                contentDescription = "角色私产", tint = PrimaryInk())
                        }
                    }
                    if (expanded.value) {
                        // 展开态=现有 IM 历史（不可回退项：图片渲染/持久化行为原样）
                        val listState = androidx.compose.foundation.lazy.rememberLazyListState()
                        LaunchedEffect(msgs.size) { if (msgs.isNotEmpty()) listState.animateScrollToItem(msgs.size - 1) }
                        LazyColumn(Modifier.weight(1f).padding(horizontal = 12.dp), state = listState) {
                            items(msgs) { m ->
                                Box(if (m.me) Modifier.fillMaxWidth() else Modifier,
                                    contentAlignment = if (m.me) Alignment.CenterEnd else Alignment.CenterStart) {
                                    Surface(color = if (m.me) SurfaceDark() else PageBg(),
                                            contentColor = if (m.me) OnDark() else Body(),
                                            border = androidx.compose.foundation.BorderStroke(
                                                1.dp, if (m.me) Color.Transparent else CardBorder()),
                                            shape = RoundedCornerShape(
                                                topStart = 12.dp, topEnd = 12.dp,
                                                bottomStart = if (m.me) 12.dp else 4.dp,
                                                bottomEnd = if (m.me) 4.dp else 12.dp),
                                            modifier = Modifier.padding(vertical = 4.dp)
                                                .widthIn(max = (screenW * 0.78f).dp)) {
                                        Column(Modifier.padding(12.dp)) {
                                            m.images.forEach { p -> ImageThumb(p, (screenW * 0.6f).dp) { zoom.value = p } }
                                            if (m.text.isNotEmpty()) Text(m.text,
                                                style = MaterialTheme.typography.bodyMedium,
                                                color = if (m.me) OnDark() else Body())
                                            // 展开态=IM 历史原样（spec 3.2）：不挂逐条情绪徽章，
                                            // 标签只属于收起态最新一轮——历史 mood_at 是全局静态值，逐条显示全是同一标签=噪音
                                            // 时间戳（ISO→本地 HH:mm；解析失败静默不显示）
                                            if (m.time.isNotEmpty()) {
                                                val hhmm = remember(m.time) {
                                                    runCatching {
                                                        java.text.SimpleDateFormat("MM-dd HH:mm", java.util.Locale.getDefault()).format(
                                                            java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX",
                                                                java.util.Locale.US).parse(m.time))
                                                    }.getOrDefault("")
                                                }
                                                if (hhmm.isNotEmpty()) Text(hhmm,
                                                    style = MaterialTheme.typography.labelSmall,
                                                    color = if (m.me) OnDarkSoft() else MutedSoft(),
                                                    modifier = Modifier.align(
                                                        if (m.me) Alignment.End else Alignment.Start))
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    } else {
                        // 收起态=最新一轮（视觉小说式）：她最新回复（打字机）+ 我最近一句摘要
                        val lastHer = msgs.lastOrNull { !it.me && it.text.isNotEmpty() }
                        val lastMe = msgs.lastOrNull { it.me }
                        Column(Modifier.weight(1f).padding(horizontal = 20.dp)
                                .clickable { expanded.value = true }) {
                            if (lastHer != null) {
                                // P2 情绪标签徽章（tone→词表原样；无 tone→mood 三档图标）
                                EmotionBadge(lastHer.tone, lastHer.mood)
                                TypewriterText(lastHer.text,
                                    animate = lastHer.id !in typedIds && animScale > 0f,
                                    animScale = animScale,
                                    style = MaterialTheme.typography.bodyLarge)
                            } else {
                                Text("……", style = MaterialTheme.typography.bodyLarge, color = MutedSoft())
                            }
                            if (lastMe != null) {
                                Text("你：" + lastMe.text.ifEmpty { "[图片]" },
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MutedSoft(), maxLines = 1,
                                    modifier = Modifier.padding(top = 6.dp))
                            }
                        }
                    }
                    // P3 思考粒子：busy 时替代转圈（每轮回复有真实 1-2s LLM 延迟）
                    if (busy.value) ThinkingParticles()
                    // 待发图片预览（拍立得卡片：白边+随机轻旋；再点 📷 重选即覆盖）
                    if (pendingImages.value.isNotEmpty()) {
                        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            pendingImages.value.forEachIndexed { i, p ->
                                androidx.compose.foundation.layout.Box(
                                    Modifier.size(56.dp).background(Color.White,
                                        RoundedCornerShape(6.dp)).padding(2.dp)
                                        .graphicsLayer {
                                            rotationZ = if (i % 2 == 0) -3f else 2f
                                        }) {
                                    coil.compose.AsyncImage(
                                        model = java.io.File(p),
                                        contentDescription = "待发送图片",
                                        contentScale = ContentScale.Crop,
                                        modifier = Modifier.fillMaxSize()
                                            .clip(RoundedCornerShape(4.dp))
                                            .clickable { pendingImages.value = pendingImages.value - p })
                                }
                            }
                        }
                    }
                    Row(Modifier.padding(horizontal = 8.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        IconButton(onClick = {
                            if (activity?.checkSelfPermission(albumPerm) ==
                                android.content.pm.PackageManager.PERMISSION_GRANTED) {
                                showAlbumPicker.value = true
                            } else {
                                permLauncher.launch(albumPerm)
                            }
                        }) {
                            // 图片图标（material "image"：相框+山），替代原回形针 emoji
                            // tint=Unspecified：vector 内自带相框灰/内芯奶油，任何 tint 都会整体染成一色
                            Icon(PhotoIcon, contentDescription = "选择图片",
                                tint = Color.Unspecified, modifier = Modifier.size(24.dp))
                        }
                        OutlinedTextField(
                            input.value, { input.value = it },
                            Modifier.weight(1f), singleLine = true,
                            colors = OutlinedTextFieldDefaults.colors(
                                focusedBorderColor = PrimaryInk(),
                                unfocusedBorderColor = Hairline(),
                                cursorColor = PrimaryInk()),
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                            keyboardActions = KeyboardActions(onSend = { send() })
                        )
                        Spacer(Modifier.width(8.dp))
                        if (busy.value) CircularProgressIndicator(Modifier.size(28.dp), color = PrimaryInk())
                        else Button(onClick = { send() },
                                colors = ButtonDefaults.buttonColors(InvertSurface(), OnInvert()),
                                shape = MaterialTheme.shapes.small) { Text("发送") }
                    }
                }
            }
            zoom.value?.let { p -> ZoomDialog(p) { zoom.value = null } }
            if (showAlbumPicker.value) {
                AlbumPicker(maxPick = 4,
                    onPick = { uris ->
                        showAlbumPicker.value = false
                        saveToPhotos(uris)
                    },
                    onDismiss = { showAlbumPicker.value = false })
            }
        }
}
