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
import androidx.compose.foundation.combinedClickable
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
            // 即时投递钩子：Python 侧每条主动消息入队即广播（落库先于入队，
            // ChatScreen 收到 loadHistory 必可见）。broadcast 线程安全，任意线程可调。
            bridge.callAttr("set_flush_hook",
                com.chaquo.python.PyObject.fromJava(Runnable {
                    runCatching { sendBroadcast(android.content.Intent(CompanionService.ACTION_PROACTIVE)) }
                }))
            bridge.callAttr("start_ticks")
        }
        setContent {
            VeranimaTheme {
                AppRoot()
            }
        }
        // M1 验收驱动器（仅 debug APK；真机裁决=测试内容绝不装机）：
        // adb am start --esa drive_b64 <base64(utf8消息)> → 走 bridge.chat 全链
        // （judges/线程/落库/回复），处理完 finish() 不扰动现有 UI 栈。
        // 生产 UI 链路（ChatScreen/adb input）与此无关。
        val drive = intent?.getStringArrayExtra("drive_b64")?.firstOrNull()
        if (drive != null) {
            lifecycleScope.launch(Dispatchers.IO) {
                try {
                    bridge.callAttr("boot", filesDir.absolutePath).toString()
                    val msg = String(android.util.Base64.decode(drive, android.util.Base64.DEFAULT), Charsets.UTF_8)
                    val r = bridge.callAttr("chat", msg, "[]", "").toString()
                    android.util.Log.i("VeranimaDrive", "drive(${msg.take(12)}): ${r.take(160)}")
                } catch (e: Exception) {
                    android.util.Log.e("VeranimaDrive", "drive failed: $e")
                }
                finish()
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class, androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
internal fun ChatScreen(role: String, onBack: () -> Unit, onOpenSpace: () -> Unit) {
    // 2026-09-01 用户裁决：会话页去立绘舞台，纯 IM 聊天框（分界线以上整体删除；
    // 视觉小说两态面板/微呼吸/haze 毛玻璃退役，立绘仅留桌宠壳消费）
    val bridge = remember { Python.getInstance().getModule("bridge") }
    val ctx = LocalContext.current
    val activity = ctx as? android.app.Activity
    val msgs = remember { mutableStateListOf<Msg>() }
    val status = remember { mutableStateOf("") }
    val charName = remember { mutableStateOf(role) }
    val input = remember { mutableStateOf("") }
    // 连发合并 worker（TURN_MERGE_SPEC）：进程级单例，切页不丢队列；
    // busy 只做「正在酝酿」指示器，不再是发送闸
    val worker = remember(role) { TurnQueue.of(role) }
    val busy = worker.thinking.collectAsState()
    val pendingImages = remember { mutableStateOf(listOf<String>()) }
    val zoom = remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val focusManager = LocalFocusManager.current
    val haptic = LocalHapticFeedback.current
    val conf = LocalConfiguration.current
    val screenW = conf.screenWidthDp

    // 横幅→气泡同步：主动消息通知发出时 core 已落库，UI 以 DB 为准
    val proactiveTick = remember { mutableStateOf(0) }
    val proactiveReceiver = remember {
        object : android.content.BroadcastReceiver() {
            override fun onReceive(c: android.content.Context?, i: android.content.Intent?) {
                if (i?.action == CompanionService.ACTION_PROACTIVE) proactiveTick.value++
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

    val showAlbumPicker = remember { mutableStateOf(false) }
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

    val listState = androidx.compose.foundation.lazy.rememberLazyListState()
    fun msgFromJson(m: org.json.JSONObject): Msg {
        val imgs = mutableListOf<String>()
        m.optJSONArray("images")?.let { ia -> for (j in 0 until ia.length()) imgs.add(ia.getString(j)) }
        return Msg(m.getLong("id"), m.getBoolean("me"), m.getString("text"), imgs,
            m.optString("time"), m.optString("tone"), m.optString("mood"))
    }
    val loadHistory = fun() {
        scope.launch {
            val o = JSONObject(
                withContext(Dispatchers.IO) { bridge.callAttr("history", 80, role).toString() })
            if (o.optBoolean("ok")) {
                val arr = o.getJSONArray("messages")
                msgs.clear()
                for (i in 0 until arr.length()) msgs.add(msgFromJson(arr.getJSONObject(i)))
            }
        }
    }
    LaunchedEffect(role) {
        charName.value = withContext(Dispatchers.IO) { bridge.callAttr("role_label", role).toString() }
        loadHistory()
        withContext(Dispatchers.IO) { bridge.callAttr("mark_read", role) }
    }
    val leOwner = androidx.lifecycle.compose.LocalLifecycleOwner.current
    DisposableEffect(leOwner) {
        val obs = androidx.lifecycle.LifecycleEventObserver { _, ev ->
            if (ev == androidx.lifecycle.Lifecycle.Event.ON_RESUME) {
                loadHistory()
                scope.launch(Dispatchers.IO) { bridge.callAttr("mark_read", role) }
            }
        }
        leOwner.lifecycle.addObserver(obs)
        onDispose { leOwner.lifecycle.removeObserver(obs) }
    }
    LaunchedEffect(proactiveTick.value) {
        if (proactiveTick.value > 0) loadHistory()
    }

    // 长按菜单/多选/引用态（09-07 微信式消息动作）。临时气泡用递减负 id——
    // key=m.id 下固定 -1/-2 会撞（09-07 翻页引入 key 的连带修正）。
    val quoted = remember { mutableStateOf<Msg?>(null) }
    val tmpId = remember { longArrayOf(-1L) }
    val menuFor = remember { mutableStateOf<Msg?>(null) }
    val selectMode = remember { mutableStateOf(false) }
    val selected = remember { mutableStateOf(setOf<Long>()) }
    val clipboard = androidx.compose.ui.platform.LocalClipboardManager.current
    fun fmtShort(t: String) = runCatching {
        java.text.SimpleDateFormat("MM-dd HH:mm", java.util.Locale.getDefault()).format(
            java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", java.util.Locale.US).parse(t))
    }.getOrDefault("")
    // 合并转发=聊天记录卡片（09-07 用户裁决：卡头要角色名+日期）。纯文本协议
    // [聊天记录] 开头行=卡片标记——core 零感知（它收到就是段普通文本，「你翻
    // 出来的东西」进历史/检索全走现成链）；UI 按标记换行渲染成卡片气泡。
    fun buildChatCard(picked: List<Msg>): String {
        val ts = picked.map { it.time }.filter { it.isNotEmpty() }.sorted()
        val span = if (ts.isEmpty()) "" else {
            val a = fmtShort(ts.first()); val b = fmtShort(ts.last())
            if (a.take(5) == b.take(5)) " ${a.take(5)}" else " ${a.take(5)} ~ ${b.take(5)}"
        }
        val name = charName.value
        val head = "——— 聊天记录 ———\n$name 与 你（${picked.size} 条$span）"
        val body = picked.joinToString("\n") { p ->
            val who = if (p.me) "你" else name
            val t = if (p.time.isNotEmpty()) fmtShort(p.time) + " " else ""
            val txt = p.text.lineSequence().first().take(60)
            "$t$who：$txt"
        }
        return "[聊天记录]\n$head\n$body\n———————"
    }
    // 真正发送（引用/合并转发/普通输入共用一个出口）——入队即返回，永不阻塞输入
    val sendText = fun(text: String, imgs: List<String>) {
        if (text.isEmpty() && imgs.isEmpty()) return
        msgs.add(Msg(tmpId[0]--, true, text, imgs))
        focusManager.clearFocus()
        haptic.performHapticFeedback(HapticFeedbackType.LongPress)
        worker.enqueue(TurnQueue.Item(text, imgs))
    }
    // 回复不直接回到本页（页面可能已销毁）：worker 每轮结束 revision+1 → 重载历史 + 已读
    LaunchedEffect(worker) {
        worker.revision.collect { if (it > 0L) {
            loadHistory()
            withContext(Dispatchers.IO) { bridge.callAttr("mark_read", role) }
        } }
    }
    LaunchedEffect(worker) {
        worker.error.collect { if (it.isNotEmpty()) status.value = it }
    }
    val send = fun() {
        val typed = input.value.trim()
        val imgs = pendingImages.value
        val quote = quoted.value
        val text = when {
            quote != null && typed.isNotEmpty() -> "「${quote.text}」\n———\n$typed"
            quote != null -> "「${quote.text}」"
            else -> typed
        }
        quoted.value = null
        input.value = ""
        pendingImages.value = emptyList()
        sendText(text, imgs)
    }
    // 动作表=数据驱动（09-07 用户裁决「菜单要支持后续扩展」）：加功能=加一行
    // (标签→动作)；长按弹层与多选操作条共用同一声明形态，UI 挂载点不改动。
    val msgActions = fun(m: Msg): List<Pair<String, () -> Unit>> = listOf(
        "复制" to {
            clipboard.setText(androidx.compose.ui.text.AnnotatedString(m.text))
            status.value = "已复制"
            menuFor.value = null
        },
        "引用" to { quoted.value = m; menuFor.value = null },
        "多选" to {
            selectMode.value = true; selected.value = setOf(m.id); menuFor.value = null
        },
    )
    val multiActions = fun(): List<Pair<String, () -> Unit>> {
        val picked = msgs.filter { selected.value.contains(it.id) }.sortedBy { it.id }
        if (picked.isEmpty()) return emptyList()
        return listOf(
            "复制" to {
                clipboard.setText(androidx.compose.ui.text.AnnotatedString(
                    picked.joinToString("\n") { (if (it.me) "我" else charName.value) + "：" + it.text }))
                status.value = "已复制 ${picked.size} 条"
            },
            "合并转发" to {
                sendText(buildChatCard(picked), emptyList())
                selectMode.value = false; selected.value = emptySet()
            },
        )
    }

    androidx.activity.compose.BackHandler {
        // 弹层/多选优先关，其次退页
        when {
            menuFor.value != null -> menuFor.value = null
            selectMode.value -> { selectMode.value = false; selected.value = emptySet() }
            else -> onBack()
        }
    }

    Column(Modifier.fillMaxSize().background(PageBg()).statusBarsPadding()) {
        // 顶栏：返回｜角色名｜齿轮
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) { Text("‹", fontSize = 22.sp, color = Muted()) }
            Text(charName.value, style = MaterialTheme.typography.headlineSmall,
                modifier = Modifier.weight(1f))
            IconButton(onClick = onOpenSpace) {
                Icon(Icons.Filled.Settings, contentDescription = "角色私产", tint = PrimaryInk())
            }
        }
        if (status.value.isNotEmpty()) {
            Text(status.value, style = MaterialTheme.typography.bodySmall, color = MutedSoft(),
                modifier = Modifier.padding(horizontal = 12.dp))
        }
    val listState = androidx.compose.foundation.lazy.rememberLazyListState()
    val olderBusy = remember { mutableStateOf(false) }
    val noMore = remember { mutableStateOf(false) }
    val loadOlder = fun() {
        if (olderBusy.value || noMore.value || msgs.isEmpty()) return
        olderBusy.value = true
        scope.launch {
            val first = msgs.first().id
            val o = JSONObject(withContext(Dispatchers.IO) {
                bridge.callAttr("history", 80, role, first).toString() })
            val arr = if (o.optBoolean("ok")) o.getJSONArray("messages") else org.json.JSONArray()
            if (arr.length() == 0) noMore.value = true
            val older = (0 until arr.length()).map { msgFromJson(arr.getJSONObject(it)) }
            // 不触尾 id → 不弹回底部；items key=m.id → 视口锚在原首条不跳页
            msgs.addAll(0, older)
            olderBusy.value = false
        }
    }
    // 只在尾消息变化（新消息/自己发送）时滚到底；prepend 旧页保持阅读位置
    LaunchedEffect(msgs.lastOrNull()?.id) {
        if (msgs.isNotEmpty()) listState.animateScrollToItem(msgs.size - 1)
    }
    LaunchedEffect(listState.firstVisibleItemIndex == 0, listState.isScrollInProgress) {
        // 上滑到顶且停手 → 翻旧页（isScrolling 进 key=同位置连刷两页可触发）
        if (listState.firstVisibleItemIndex == 0 && !listState.isScrollInProgress) loadOlder()
    }
        LazyColumn(Modifier.weight(1f).padding(horizontal = 12.dp), state = listState,
            contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 6.dp)) {
            items(msgs, key = { m -> m.id }) { m ->
                Box(if (m.me) Modifier.fillMaxWidth() else Modifier,
                    contentAlignment = if (m.me) Alignment.CenterEnd else Alignment.CenterStart) {
                    val selectedHere = selected.value.contains(m.id)
                    Surface(color = if (m.me) SurfaceDark() else PageBg(),
                            contentColor = if (m.me) OnDark() else Body(),
                            border = androidx.compose.foundation.BorderStroke(
                                if (selectedHere) 2.dp else 1.dp,
                                when {
                                    selectedHere -> PrimaryInk()
                                    m.me -> Color.Transparent
                                    else -> CardBorder()
                                }),
                            shape = RoundedCornerShape(
                                topStart = 12.dp, topEnd = 12.dp,
                                bottomStart = if (m.me) 12.dp else 4.dp,
                                bottomEnd = if (m.me) 4.dp else 12.dp),
                            modifier = Modifier.padding(vertical = 4.dp)
                                .widthIn(max = (screenW * 0.78f).dp)
                                // 长按=动作菜单（menuFor 驱动，菜单实体在列表外层单份）；
                                // 多选态点击=勾选/取消，态外点击=无操作（保留图片查看）
                                .combinedClickable(
                                    onClick = {
                                        if (selectMode.value) selected.value =
                                            if (selectedHere) selected.value - m.id
                                            else selected.value + m.id
                                    },
                                    onLongClick = {
                                        if (!selectMode.value) {
                                            haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                                            menuFor.value = m
                                        }
                                    }
                                )) {
                        Column(Modifier.padding(12.dp)) {
                            m.images.forEach { p2 -> ImageThumb(p2, (screenW * 0.6f).dp) { zoom.value = p2 } }
                            if (m.text.isNotEmpty()) {
                                if (m.text.startsWith("[聊天记录]")) {
                                    // 合并转发卡片：白底黑边内框 + 灰字（Galaxy 卡片语言）
                                    Surface(color = CardBg(), contentColor = Body(),
                                        border = androidx.compose.foundation.BorderStroke(1.dp, CardBorder()),
                                        shape = RoundedCornerShape(8.dp),
                                        modifier = Modifier.widthIn(max = (screenW * 0.66f).dp)) {
                                        Column(Modifier.padding(10.dp)) {
                                            Text(m.text.removePrefix("[聊天记录]\n"),
                                                style = MaterialTheme.typography.bodySmall)
                                        }
                                    }
                                } else Text(m.text,
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = if (m.me) OnDark() else Body())
                            }
                            if (m.time.isNotEmpty()) {
                                val hhmm = remember(m.time) { fmtShort(m.time) }
                                if (hhmm.isNotEmpty()) Text(hhmm,
                                    style = MaterialTheme.typography.labelSmall,
                                    color = if (m.me) OnDarkSoft() else MutedSoft(),
                                    modifier = Modifier.align(if (m.me) Alignment.End else Alignment.Start))
                            }
                        }
                    }
                }
            }
        }
        if (busy.value) ThinkingParticles()
        if (pendingImages.value.isNotEmpty()) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                pendingImages.value.forEachIndexed { i2, p2 ->
                    androidx.compose.foundation.layout.Box(
                        Modifier.size(56.dp).background(Color.White,
                            RoundedCornerShape(6.dp)).padding(2.dp)) {
                        coil.compose.AsyncImage(
                            model = java.io.File(p2),
                            contentDescription = "待发送图片",
                            contentScale = ContentScale.Crop,
                            modifier = Modifier.fillMaxSize()
                                .clip(RoundedCornerShape(4.dp))
                                .clickable { pendingImages.value = pendingImages.value - p2 })
                    }
                }
            }
        }
        // 引用条（09-07 长按菜单）：引用态显示在输入上方，× 取消
        quoted.value?.let { q ->
            Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 2.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Surface(color = CardBg(), contentColor = Muted(),
                    border = androidx.compose.foundation.BorderStroke(1.dp, CardBorder()),
                    shape = RoundedCornerShape(6.dp),
                    modifier = Modifier.weight(1f)) {
                    Text("引用：“${q.text.take(40)}”",
                        maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp))
                }
                TextButton(onClick = { quoted.value = null }) { Text("×", color = PrimaryInk()) }
            }
        }
        // 多选操作条：动作同样走动作表（复制/合并转发/取消）
        if (selectMode.value) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 2.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Text("已选 ${selected.value.size} 条", style = MaterialTheme.typography.bodySmall,
                    color = Muted())
                multiActions().forEach { (label, act) ->
                    TextButton(onClick = act) { Text(label, color = PrimaryInk()) }
                }
                TextButton(onClick = {
                    selectMode.value = false; selected.value = emptySet()
                }) { Text("取消", color = Muted()) }
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
            Button(onClick = { send() },
                    colors = ButtonDefaults.buttonColors(InvertSurface(), OnInvert()),
                    shape = MaterialTheme.shapes.small) { Text("发送") }
        }
    }
    zoom.value?.let { pz -> ZoomDialog(pz) { zoom.value = null } }
    // 长按动作菜单（微信式底部弹层：动作表驱动，一行一项）
    menuFor.value?.let { target ->
        Dialog(onDismissRequest = { menuFor.value = null }) {
            Surface(color = CardBg(), contentColor = Body(),
                border = androidx.compose.foundation.BorderStroke(1.dp, CardBorder()),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.fillMaxWidth(0.62f)) {
                Column {
                    msgActions(target).forEach { (label, act) ->
                        TextButton(onClick = act,
                            modifier = Modifier.fillMaxWidth()) {
                            Text(label, color = PrimaryInk(),
                                modifier = Modifier.fillMaxWidth())
                        }
                    }
                }
            }
        }
    }
    if (showAlbumPicker.value) {
        AlbumPicker(maxPick = 4,
            onPick = { uris -> showAlbumPicker.value = false; saveToPhotos(uris) },
            onDismiss = { showAlbumPicker.value = false })
    }
}
