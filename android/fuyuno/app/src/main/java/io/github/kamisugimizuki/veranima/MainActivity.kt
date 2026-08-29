package io.github.kamisugimizuki.veranima

import android.content.Intent
import android.os.Bundle
import android.provider.Settings as AndSettings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
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
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class Msg(val me: Boolean, val text: String, val images: List<String> = emptyList())

@Composable
private fun ImageThumb(path: String) {
    val bmp = remember(path) {
        try {  // BitmapFactory 直解，省一个 coil 依赖（ponytail：图多了再换加载库）
            val opts = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
            android.graphics.BitmapFactory.decodeFile(path, opts)
            var ss = 1
            while (opts.outWidth / (ss * 2) > 512 && opts.outHeight / (ss * 2) > 512) ss *= 2
            android.graphics.BitmapFactory.decodeFile(path, android.graphics.BitmapFactory.Options().apply { inSampleSize = ss })
        } catch (e: Exception) { null }
    }
    if (bmp != null) {
        Image(bmp.asImageBitmap(), contentDescription = "图片",
            modifier = Modifier.padding(bottom = 4.dp).widthIn(max = 220.dp).heightIn(max = 220.dp),
            contentScale = androidx.compose.ui.layout.ContentScale.Fit)
    } else {
        Text("[图片读取失败]", style = MaterialTheme.typography.bodySmall)
    }
}

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
            this, Intent(this, CompanionService::class.java))
        setContent {
            VeranimaTheme {
                val msgs = remember { mutableStateListOf<Msg>() }
                val status = remember { mutableStateOf("启动核心…") }
                val input = remember { mutableStateOf("") }
                val busy = remember { mutableStateOf(false) }
                val pendingImages = remember { mutableStateOf(listOf<String>()) }
                val showSettings = remember { mutableStateOf(false) }
                val scope = rememberCoroutineScope()
                val focusManager = androidx.compose.ui.platform.LocalFocusManager.current
                // 无依赖图片选择（GetMultipleContents 全版本可用，PhotoPicker 依赖不新鲜）
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
                        val o = org.json.JSONObject(
                            withContext(Dispatchers.IO) { bridge.callAttr("history").toString() })
                        if (o.optBoolean("ok")) {
                            val arr = o.getJSONArray("messages")
                            msgs.clear()
                            for (i in 0 until arr.length()) {
                                val m = arr.getJSONObject(i)
                                val imgs = mutableListOf<String>()
                                m.optJSONArray("images")?.let { ia -> for (j in 0 until ia.length()) imgs.add(ia.getString(j)) }
                                msgs.add(Msg(m.getBoolean("me"), m.getString("text"), imgs))
                            }
                        }
                    }
                }
                LaunchedEffect(Unit) {
                    val files = applicationContext.filesDir.absolutePath
                    val r = withContext(Dispatchers.IO) { bridge.callAttr("boot", files).toString() }
                    status.value = "boot: $r"
                    withContext(Dispatchers.IO) { bridge.callAttr("start_ticks") }
                    loadHistory()
                }
                // 回前台=拉一次（后台期间产生的主动消息由此进对话流）
                val leOwner = androidx.compose.ui.platform.LocalLifecycleOwner.current
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
                    msgs.add(Msg(true, q, imgs)); input.value = ""
                    pendingImages.value = emptyList()
                    focusManager.clearFocus()  // 发送后收起键盘，别挡消息
                    busy.value = true
                    scope.launch {
                        val r = withContext(Dispatchers.IO) {
                            bridge.callAttr("chat", q, org.json.JSONArray(imgs).toString()).toString()
                        }
                        val o = org.json.JSONObject(r)
                        if (o.optBoolean("ok")) msgs.add(Msg(false, o.getString("reply")))
                        else status.value = "chat 失败: ${o.optString("error")}"
                        busy.value = false
                    }
                }
                Column(Modifier.fillMaxSize().background(Canvas).padding(12.dp)) {
                    Text(status.value, style = MaterialTheme.typography.bodySmall,
                        color = MutedSoft)
                    if (showSettings.value) {
                        SettingsScreen(onBack = { showSettings.value = false })
                        return@Column
                    }
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically) {
                        // 显示衬线 + 负字距（设计稿：serif 是品牌声音，不 bold）
                        Text("駒川 冬乃", style = MaterialTheme.typography.headlineSmall)
                        TextButton(onClick = { showSettings.value = true }) { Text("设置", color = Muted) }
                    }
                    val listState = androidx.compose.foundation.lazy.rememberLazyListState()
                    LaunchedEffect(msgs.size) { if (msgs.isNotEmpty()) listState.animateScrollToItem(msgs.size - 1) }
                    LazyColumn(Modifier.weight(1f).padding(vertical = 8.dp), state = listState) {
                        items(msgs) { m ->
                            Box(if (m.me) Modifier.fillMaxWidth() else Modifier,
                                contentAlignment = if (m.me) Alignment.CenterEnd else Alignment.CenterStart) {
                                // 我=深色面板（surface-dark 上的奶油字），她=奶油卡片（surface-card 色块即层级，无阴影）
                                Surface(color = if (m.me) SurfaceDark else SurfaceCard,
                                        contentColor = if (m.me) OnDark else Ink,
                                        shape = RoundedCornerShape(
                                            topStart = 12.dp, topEnd = 12.dp,
                                            bottomStart = if (m.me) 12.dp else 4.dp,
                                            bottomEnd = if (m.me) 4.dp else 12.dp),
                                        modifier = Modifier.padding(vertical = 4.dp).widthIn(max = 320.dp)) {
                                    Column(Modifier.padding(12.dp)) {
                                        m.images.forEach { p -> ImageThumb(p) }
                                        if (m.text.isNotEmpty()) Text(m.text, style = MaterialTheme.typography.bodyMedium)
                                    }
                                }
                            }
                        }
                    }
                    // 待发图片预览（发送前可反悔：再点 📎 重选即覆盖）
                    if (pendingImages.value.isNotEmpty()) {
                        Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                            pendingImages.value.forEach { Text("[${it.substringAfterLast('/')}] ", style = MaterialTheme.typography.bodySmall) }
                        }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
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
        }
    }
}
