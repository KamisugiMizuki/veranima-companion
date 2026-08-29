package io.github.kamisugimizuki.veranima

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Bundle
import android.provider.Settings as AndSettings
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
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
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.activity.ComponentActivity
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

data class Msg(val id: Long, val me: Boolean, val text: String, val images: List<String> = emptyList())

// ---------- 舞台部件 ----------

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
                val status = remember { mutableStateOf("启动核心…") }
                val charName = remember { mutableStateOf("凛") }
                val portraitPath = remember { mutableStateOf("") }
                val input = remember { mutableStateOf("") }
                val busy = remember { mutableStateOf(false) }
                val pendingImages = remember { mutableStateOf(listOf<String>()) }
                val showSettings = remember { mutableStateOf(false) }
                val expanded = remember { mutableStateOf(false) }  // 面板两态：收起=最新一轮 / 展开=全历史
                val typedIds = remember { mutableSetOf<Long>() }   // 首载已有消息不做打字机
                val zoom = remember { mutableStateOf<String?>(null) }
                val scope = rememberCoroutineScope()
                val focusManager = LocalFocusManager.current
                val animScale = remember {
                    try { AndSettings.Global.getFloat(contentResolver, AndSettings.Global.ANIMATOR_DURATION_SCALE, 1f) }
                    catch (e: Exception) { 1f }
                }
                val conf = LocalConfiguration.current
                val screenW = conf.screenWidthDp
                val screenH = conf.screenHeightDp

                val pickImages = androidx.activity.compose.rememberLauncherForActivityResult(
                    androidx.activity.result.contract.ActivityResultContracts.GetMultipleContents()
                ) { uris: List<android.net.Uri> ->
                    if (uris.isEmpty()) return@rememberLauncherForActivityResult
                    scope.launch(Dispatchers.IO) {
                        val saved = uris.take(4).mapNotNull { uri ->
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
                                msgs.add(Msg(m.getLong("id"), m.getBoolean("me"), m.getString("text"), imgs))
                            }
                            if (typedIds.isEmpty()) msgs.forEach { typedIds.add(it.id) }  // 首载快照：旧消息不重演
                        }
                    }
                }
                LaunchedEffect(Unit) {
                    val files = applicationContext.filesDir.absolutePath
                    val r = withContext(Dispatchers.IO) { bridge.callAttr("boot", files).toString() }
                    status.value = "boot: $r"
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

                val send = fun() {
                    val q = input.value.trim()
                    val imgs = pendingImages.value
                    if ((q.isEmpty() && imgs.isEmpty()) || busy.value) return
                    msgs.add(Msg(-1, true, q, imgs)); input.value = ""
                    pendingImages.value = emptyList()
                    focusManager.clearFocus()  // 发送后收起键盘，别挡消息
                    busy.value = true
                    scope.launch {
                        val r = withContext(Dispatchers.IO) {
                            bridge.callAttr("chat", q, org.json.JSONArray(imgs).toString()).toString()
                        }
                        val o = JSONObject(r)
                        if (o.optBoolean("ok")) msgs.add(Msg(-2, false, o.getString("reply")))
                        else status.value = "chat 失败: ${o.optString("error")}"
                        busy.value = false
                        loadHistory()  // 拿真实 id（打字机判定用）
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

                val panelH by animateDpAsState(
                    if (expanded.value) (screenH * 0.70f).dp else (screenH * 0.40f).dp,
                    animationSpec = tween(260, easing = FastOutSlowInEasing), label = "panel")

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
                    Box(Modifier.fillMaxSize().background(Canvas)) {
                        // L0 舞台晕影：立绘是白底图，白色径向渐变垫底消除矩形边界
                        // （只画背景层，不改立绘颜色；P2 环境光将接管此层）
                        Box(Modifier.fillMaxSize().background(Brush.radialGradient(
                            colors = listOf(Color.White, Canvas))))
                        // ---- L1 立绘舞台（宽适配+顶对齐：任意长宽比不裁脸；无图则整层消失=纯色舞台） ----
                        // haze 挂这里：hazeChild（面板）是兄弟层级，不能是子孙（Haze 硬约束）
                        Column(Modifier.fillMaxSize().stageHaze(), horizontalAlignment = Alignment.CenterHorizontally) {
                            if (portraitBmp != null) {
                                val imgW = (screenW * 0.86f).dp
                                // 舞台区 bottom padding 绑定 panelH：面板上滑时立绘随之整体上移并在
                                // 剩余可见区内自动缩放，永不被聊天框遮挡（2026-08-29 用户追加）
                                Box(Modifier.fillMaxWidth().weight(1f).padding(bottom = panelH),
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
                        // boot 诊断浮层（不占舞台高度，防立绘顶裁切）
                        Text(status.value, style = MaterialTheme.typography.bodySmall, color = MutedSoft,
                            modifier = Modifier.align(Alignment.TopStart).padding(start = 12.dp, top = 4.dp))
                        // ---- L3 毛玻璃对话面板（两态；面板高度即立绘可见区，天然适配长宽比） ----
                        var dragTotal by remember { mutableFloatStateOf(0f) }
                        Box(Modifier.align(Alignment.BottomCenter).fillMaxWidth().height(panelH)
                                .panelHaze()   // 背景由 hazeChild 画（含模糊+底色），不再单独 background
                                .pointerInput(Unit) {
                                    detectDragGestures(
                                        onDragStart = { dragTotal = 0f },
                                        onDragEnd = {
                                            if (dragTotal < -60f) expanded.value = true
                                            else if (dragTotal > 60f) expanded.value = false
                                        },
                                        onDragCancel = {},
                                        onDrag = { _, drag -> dragTotal += drag.y })
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
                                // 待发图片预览（发送前可反悔：再点 📎 重选即覆盖）
                                if (pendingImages.value.isNotEmpty()) {
                                    Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp)) {
                                        pendingImages.value.forEach { Text("[${it.substringAfterLast('/')}] ",
                                            style = MaterialTheme.typography.bodySmall) }
                                    }
                                }
                                Row(Modifier.padding(horizontal = 8.dp, vertical = 6.dp),
                                    verticalAlignment = Alignment.CenterVertically) {
                                    IconButton(onClick = { pickImages.launch("image/*") }) {
                                        Text("📎", fontSize = MaterialTheme.typography.bodyLarge.fontSize)
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
                    }
                }
            }
        }
    }
}
