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
import androidx.compose.animation.animateColorAsState
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
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
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
private val PhotoIcon: androidx.compose.ui.graphics.vector.ImageVector by lazy {
    androidx.compose.ui.graphics.vector.ImageVector.Builder(
        defaultWidth = 24.dp, defaultHeight = 24.dp, viewportWidth = 24f, viewportHeight = 24f
    ).apply {
        // 相框（圆角矩形描边→用 fill 加内框）
        path(fill = androidx.compose.ui.graphics.SolidColor(Muted)) {
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
        path(fill = androidx.compose.ui.graphics.SolidColor(Canvas)) {
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
        path(fill = androidx.compose.ui.graphics.SolidColor(Muted)) {
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

/** P2 图片主色：16×16 降采样求平均色（纯 SDK，无 Palette 依赖；失败→null 不参与混合） */
private fun averageColor(path: String): Color? {
    return try {
        val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(path, opts)
        var ss = 1
        while (opts.outWidth / (ss * 2) > 16 && opts.outHeight / (ss * 2) > 16) ss *= 2
        val bmp = BitmapFactory.decodeFile(path, BitmapFactory.Options().apply { inSampleSize = ss })
            ?: return null
        val w = bmp.width; val h = bmp.height
        var r = 0L; var g = 0L; var b = 0L
        for (y in 0 until h step 2) for (x in 0 until w step 2) {
            val c = bmp.getPixel(x, y)
            r += (c shr 16) and 0xFF; g += (c shr 8) and 0xFF; b += c and 0xFF
        }
        val n = (((w + 1) / 2) * ((h + 1) / 2)).toLong().coerceAtLeast(1)
        bmp.recycle()
        Color(android.graphics.Color.rgb((r / n).toInt(), (g / n).toInt(), (b / n).toInt()))
    } catch (e: Exception) {
        null
    }
}

/** P2 情绪→环境光边缘色：tone 优先（19 词表→6 组），回退 mood 三档，再回退米白 */
private fun ambientEdge(tone: String, mood: String, dark: Boolean, image: Color?): Color {
    var c = ToneAmbient[tone] ?: MoodAmbient[mood] ?: Color(0xFFE8E4DC)
    if (dark) {
        // 夜间：整体降明度（混合藏青 55%）
        c = androidx.compose.ui.graphics.lerp(c, NightAmbient, 0.55f)
    }
    if (image != null) c = androidx.compose.ui.graphics.lerp(c, image, 0.3f)  // 图片主色混入 30%
    return c
}

/** P2 情绪标签文案：tone 词表原样显示；回退 mood 三档图标+词 */
private fun emotionLabel(tone: String, mood: String): Pair<String, Color> {
    if (tone.isNotEmpty()) return tone to (ToneLabelColor[tone] ?: Muted)
    return when (mood) {
        "开心" -> "✨ 心情不错" to Color(0xFFC77B4A)
        "低落" -> "🌧 有点闷" to Color(0xFF7A87A8)
        else -> "💭 平静" to Muted
    }
}

/** P2 情绪标签徽章（圆角小胶囊，随回复到达 α 淡入） */
@Composable
private fun EmotionBadge(tone: String, mood: String) {
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
private fun ThinkingParticles() {
    val t = rememberInfiniteTransition(label = "thinking")
    Row(verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 2.dp)) {
        repeat(3) { i ->
            val y by t.animateFloat(0f, -8f, label = "dot$i",
                animationSpec = infiniteRepeatable(
                    tween(800, delayMillis = i * 250, easing = FastOutSlowInEasing),
                    RepeatMode.Reverse))
            Box(Modifier.size(5.dp).offset(y = y.dp).graphicsLayer { alpha = 0.6f - i * 0.15f }
                .background(Coral, RoundedCornerShape(50)))
            Spacer(Modifier.width(5.dp))
        }
        Text("正在酝酿…", style = MaterialTheme.typography.bodySmall, color = MutedSoft)
    }
}

/** 解码图片并按目标边长降采样（历史缩略/点击放大共用） */
private fun decodeSampled(path: String, targetPx: Int): Bitmap? = try {
    val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(path, opts)
    var ss = 1
    while (opts.outWidth / (ss * 2) > targetPx && opts.outHeight / (ss * 2) > targetPx) ss *= 2
    BitmapFactory.decodeFile(path, BitmapFactory.Options().apply { inSampleSize = ss })
} catch (e: Exception) { null }

@Composable
private fun ImageThumb(path: String, maxW: androidx.compose.ui.unit.Dp, onClick: (() -> Unit)? = null) {
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
private fun toast(ctx: android.content.Context, msg: String) {
    android.widget.Toast.makeText(ctx, msg, android.widget.Toast.LENGTH_SHORT).show()
}

/** 点击放大：全屏暗底，点任意处关闭 */
@Composable
private fun ZoomDialog(path: String, onDismiss: () -> Unit) {
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
        setContent {
            VeranimaTheme {
                val msgs = remember { mutableStateListOf<Msg>() }
                val status = remember { mutableStateOf("") }
                val charName = remember { mutableStateOf("凛") }
                val portraitPath = remember { mutableStateOf("") }
                val input = remember { mutableStateOf("") }
                val busy = remember { mutableStateOf(false) }
                // P2 情绪：最新一条回复的 tone/mood（驱动情绪标签+环境光；空→默认平静）
                val lastTone = remember { mutableStateOf("") }
                val lastMood = remember { mutableStateOf("") }
                // P3 图片主色：用户最近发图的平均色（16×16 降采样），混入环境光 30%
                val imageAmbient = remember { mutableStateOf<Color?>(null) }
                val pendingImages = remember { mutableStateOf(listOf<String>()) }
                val showSettings = remember { mutableStateOf(false) }
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
                        applicationContext, proactiveReceiver,
                        android.content.IntentFilter(CompanionService.ACTION_PROACTIVE),
                        androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED)
                    onDispose { runCatching { applicationContext.unregisterReceiver(proactiveReceiver) } }
                }
                val animScale = remember {
                    try { AndSettings.Global.getFloat(contentResolver, AndSettings.Global.ANIMATOR_DURATION_SCALE, 1f) }
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
                                val bytes = applicationContext.contentResolver
                                    .openInputStream(uri)?.use { it.readBytes() } ?: return@mapNotNull null
                                val dir = java.io.File(applicationContext.filesDir, "photos").apply { mkdirs() }
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
                            withContext(Dispatchers.IO) { bridge.callAttr("history").toString() })
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
                LaunchedEffect(Unit) {
                    val files = applicationContext.filesDir.absolutePath
                    val r = withContext(Dispatchers.IO) { bridge.callAttr("boot", files).toString() }
                    val bootOk = runCatching { JSONObject(r).optBoolean("ok", false) }.getOrDefault(false)
                    // boot 诊断行：正常启动不显示，异常/失败才在顶部浮层显示 log
                    if (!bootOk) status.value = "boot: $r"
                    runCatching { JSONObject(r).optString("role").takeIf { it.isNotEmpty() }?.let { charName.value = it } }
                    withContext(Dispatchers.IO) { bridge.callAttr("start_ticks") }
                    // 立绘：assets → filesDir/portraits（Kotlin 侧解包，bridge.portrait_path 只报路径）
                    withContext(Dispatchers.IO) {
                        val dir = java.io.File(applicationContext.filesDir, "portraits").apply { mkdirs() }
                        runCatching {
                            applicationContext.assets.list("portraits")?.forEach { name ->
                                val dst = java.io.File(dir, name)
                                if (!dst.exists()) applicationContext.assets.open("portraits/$name").use { inp ->
                                    dst.outputStream().use { out -> inp.copyTo(out) }
                                }
                            }
                        }
                        portraitPath.value = bridge.callAttr("portrait_path").toString()
                    }
                    loadHistory()
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
                    // P2 图片主色：首图 16×16 降采样求均值（纯 SDK，无 Palette 依赖）
                    imgs.firstOrNull()?.let { p ->
                        scope.launch(Dispatchers.IO) {
                            imageAmbient.value = averageColor(p)
                        }
                    }
                    scope.launch {
                        val r = withContext(Dispatchers.IO) {
                            bridge.callAttr("chat", q, org.json.JSONArray(imgs).toString()).toString()
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

                // 面板高度=Animatable（Float px：拖动中实时 snap 跟手，松手动画到目标档）
                val panelH = remember { Animatable(screenH * 0.40f) }
                val panelMin = screenH * 0.25f
                val panelMax = screenH * 0.85f
                fun snapPanel(to: Boolean) {
                    scope.launch {
                        panelH.animateTo(
                            if (to) screenH * 0.70f else screenH * 0.40f,
                            animationSpec = tween(260, easing = FastOutSlowInEasing))
                    }
                }

                // 返回键：设置页→回聊天；聊天页→3 秒内双击退出
                val backAt = remember { mutableStateOf(0L) }
                val ctx = LocalContext.current
                BackHandler(enabled = showSettings.value) { showSettings.value = false }
                BackHandler(enabled = !showSettings.value) {
                    val now = System.currentTimeMillis()
                    if (now - backAt.value < 3000) {
                        finish()
                    } else {
                        backAt.value = now
                        toast(ctx, "再按一次返回退出")
                    }
                }

                if (showSettings.value) {
                    SettingsScreen(onBack = { showSettings.value = false })
                } else {
                    val hazeState = remember { HazeState() }
                    val panelShape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp)
                    @OptIn(ExperimentalHazeApi::class)
                    fun Modifier.stageHaze() = this.haze(hazeState)
                    @OptIn(ExperimentalHazeApi::class)
                    fun Modifier.panelHaze() = this.hazeChild(
                        state = hazeState,
                        shape = panelShape,
                        style = dev.chrisbanes.haze.HazeStyle(
                            backgroundColor = SurfaceCard.copy(alpha = 0.92f),
                            tints = emptyList(),
                            blurRadius = 24.dp))
                    val dark = androidx.compose.foundation.isSystemInDarkTheme()
                    // P2 环境光：中心固定白（立绘背后白不变，用户裁决），边缘色随
                    // 最新回复情绪 tone/mood + 用户图片主色；夜间整体降明度
                    val ambient by animateColorAsState(
                        ambientEdge(lastTone.value, lastMood.value, dark, imageAmbient.value),
                        animationSpec = tween(1200), label = "ambient")
                    Box(Modifier.fillMaxSize().background(Canvas)) {
                        // L0 环境光背景：径向渐变（中心=立绘区白，矩形边界不可见；边缘=情绪氛围色）
                        // 立绘显示层背景固定白（用户裁决）：渐变中心白覆盖立绘背后，氛围色只露在四周
                        Box(Modifier.fillMaxSize().background(Brush.radialGradient(
                            colors = listOf(Color.White, Color.White, ambient),
                            center = Offset(screenW / 2f, screenH * 0.30f),
                            radius = screenH * 0.62f)))
                        // ---- L1 立绘舞台（宽适配+顶对齐：任意长宽比不裁脸；无图则整层消失=纯色舞台） ----
                        // haze 挂这里：hazeChild（面板）是兄弟层级，不能是子孙（Haze 硬约束）
                        Column(Modifier.fillMaxSize().stageHaze(), horizontalAlignment = Alignment.CenterHorizontally) {
                            if (portraitBmp != null) {
                                val imgW = (screenW * 0.86f).dp
                                // 舞台区 bottom padding 绑定 panelH：面板上滑时立绘随之整体上移并在
                                // 剩余可见区内自动缩放，永不被聊天框遮挡（2026-08-29 用户追加）
                                Box(Modifier.fillMaxWidth().weight(1f).padding(bottom = panelH.value.dp),
                                    contentAlignment = Alignment.TopCenter) {
                                    Box(Modifier.width(imgW).offset(y = breathDp.dp)) {
                                        Image(portraitBmp!!.asImageBitmap(), charName.value,
                                            Modifier.fillMaxWidth().fillMaxHeight(), contentScale = ContentScale.Fit)
                                        // 脚部渐变遮罩：立绘融入背景/面板，消除悬浮感
                                        Box(Modifier.fillMaxWidth().height(90.dp).align(Alignment.BottomCenter)
                                            .background(Brush.verticalGradient(listOf(Color.Transparent, Canvas))))
                                    }
                                }
                            } else {
                                Spacer(Modifier.weight(1f))
                            }
                        }
                        // boot 诊断浮层（仅异常时显示；正常启动为空，不占舞台高度）
                        if (status.value.isNotEmpty()) {
                            Text(status.value, style = MaterialTheme.typography.bodySmall, color = MutedSoft,
                                modifier = Modifier.align(Alignment.TopStart).padding(start = 12.dp, top = 4.dp))
                        }
                        // ---- L3 毛玻璃对话面板（两态；面板高度即立绘可见区，天然适配长宽比） ----
                        var dragTotal by remember { mutableFloatStateOf(0f) }
                        Box(Modifier.align(Alignment.BottomCenter).fillMaxWidth().height(panelH.value.dp)
                                .panelHaze()   // 背景由 hazeChild 画（含模糊+底色），不再单独 background
                                .pointerInput(Unit) {
                                    detectDragGestures(
                                        onDragStart = { dragTotal = 0f },
                                        onDragEnd = {
                                            if (dragTotal < -60f) { expanded.value = true; snapPanel(true) }
                                            else if (dragTotal > 60f) { expanded.value = false; snapPanel(false) }
                                            else snapPanel(expanded.value)
                                        },
                                        onDragCancel = { snapPanel(expanded.value) },
                                        onDrag = { _, drag ->
                                            dragTotal += drag.y
                                            scope.launch {
                                                panelH.snapTo((panelH.value + drag.y).coerceIn(panelMin, panelMax))
                                            }
                                        })
                                }) {
                            Column(Modifier.fillMaxSize()) {
                                // 把手行：角色名（衬线）+ 拖拽指示 + 设置
                                Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically) {
                                    Text(charName.value, style = MaterialTheme.typography.headlineSmall)
                                    Box(Modifier.width(36.dp).height(4.dp)
                                        .background(Hairline, RoundedCornerShape(2.dp)))
                                    TextButton(onClick = { showSettings.value = true }) { Text("设置", color = Muted) }
                                }
                                if (expanded.value) {
                                    // 展开态=现有 IM 历史（不可回退项：图片渲染/持久化行为原样）
                                    val listState = androidx.compose.foundation.lazy.rememberLazyListState()
                                    LaunchedEffect(msgs.size) { if (msgs.isNotEmpty()) listState.animateScrollToItem(msgs.size - 1) }
                                    LazyColumn(Modifier.weight(1f).padding(horizontal = 12.dp), state = listState) {
                                        items(msgs) { m ->
                                            Box(if (m.me) Modifier.fillMaxWidth() else Modifier,
                                                contentAlignment = if (m.me) Alignment.CenterEnd else Alignment.CenterStart) {
                                                Surface(color = if (m.me) SurfaceDark else Canvas,
                                                        contentColor = if (m.me) OnDark else Ink,
                                                        shape = RoundedCornerShape(
                                                            topStart = 12.dp, topEnd = 12.dp,
                                                            bottomStart = if (m.me) 12.dp else 4.dp,
                                                            bottomEnd = if (m.me) 4.dp else 12.dp),
                                                        modifier = Modifier.padding(vertical = 4.dp)
                                                            .widthIn(max = (screenW * 0.78f).dp)) {
                                                    Column(Modifier.padding(12.dp)) {
                                                        m.images.forEach { p -> ImageThumb(p, (screenW * 0.6f).dp) { zoom.value = p } }
                                                        if (m.text.isNotEmpty()) Text(m.text, style = MaterialTheme.typography.bodyMedium)
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
                                                                color = MutedSoft,
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
                                            .clickable { expanded.value = true; snapPanel(true) }) {
                                        if (lastHer != null) {
                                            // P2 情绪标签徽章（tone→词表原样；无 tone→mood 三档图标）
                                            EmotionBadge(lastHer.tone, lastHer.mood)
                                            TypewriterText(lastHer.text,
                                                animate = lastHer.id !in typedIds && animScale > 0f,
                                                animScale = animScale,
                                                style = MaterialTheme.typography.bodyLarge)
                                        } else {
                                            Text("……", style = MaterialTheme.typography.bodyLarge, color = MutedSoft)
                                        }
                                        if (lastMe != null) {
                                            Text("你：" + lastMe.text.ifEmpty { "[图片]" },
                                                style = MaterialTheme.typography.bodySmall,
                                                color = MutedSoft, maxLines = 1,
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
                                        if (checkSelfPermission(albumPerm) ==
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
                                            focusedBorderColor = Coral,
                                            unfocusedBorderColor = Hairline,
                                            cursorColor = Coral),
                                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                                        keyboardActions = KeyboardActions(onSend = { send() })
                                    )
                                    Spacer(Modifier.width(8.dp))
                                    if (busy.value) CircularProgressIndicator(Modifier.size(28.dp), color = Coral)
                                    else Button(onClick = { send() },
                                            colors = ButtonDefaults.buttonColors(Coral),
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
            }
        }
    }
}
