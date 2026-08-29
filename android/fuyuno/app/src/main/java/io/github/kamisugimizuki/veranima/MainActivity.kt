package io.github.kamisugimizuki.veranima

import android.content.Intent
import android.os.Bundle
import android.provider.Settings as AndSettings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class Msg(val me: Boolean, val text: String)

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
            MaterialTheme {
                val msgs = remember { mutableStateListOf<Msg>() }
                val status = remember { mutableStateOf("启动核心…") }
                val input = remember { mutableStateOf("") }
                val busy = remember { mutableStateOf(false) }
                val showSettings = remember { mutableStateOf(false) }
                val scope = rememberCoroutineScope()
                val focusManager = androidx.compose.ui.platform.LocalFocusManager.current

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
                                msgs.add(Msg(m.getBoolean("me"), m.getString("text")))
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
                    if (q.isEmpty() || busy.value) return
                    msgs.add(Msg(true, q)); input.value = ""
                    focusManager.clearFocus()  // 发送后收起键盘，别挡消息
                    busy.value = true
                    scope.launch {
                        val r = withContext(Dispatchers.IO) { bridge.callAttr("chat", q).toString() }
                        val o = org.json.JSONObject(r)
                        if (o.optBoolean("ok")) msgs.add(Msg(false, o.getString("reply")))
                        else status.value = "chat 失败: ${o.optString("error")}"
                        busy.value = false
                    }
                }
                Column(Modifier.fillMaxSize().padding(12.dp)) {
                    Text(status.value, style = MaterialTheme.typography.bodySmall)
                    if (showSettings.value) {
                        SettingsScreen(onBack = { showSettings.value = false })
                        return@Column
                    }
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                        TextButton(onClick = { showSettings.value = true }) { Text("设置") }
                    }
                    val listState = androidx.compose.foundation.lazy.rememberLazyListState()
                    LaunchedEffect(msgs.size) { if (msgs.isNotEmpty()) listState.animateScrollToItem(msgs.size - 1) }
                    LazyColumn(Modifier.weight(1f), state = listState) {
                        items(msgs) { m ->
                            Box(if (m.me) Modifier.fillMaxWidth() else Modifier,
                                contentAlignment = if (m.me) Alignment.CenterEnd else Alignment.CenterStart) {
                                Surface(tonalElevation = 2.dp, shape = MaterialTheme.shapes.medium,
                                        modifier = Modifier.padding(vertical = 3.dp).widthIn(max = 300.dp)) {
                                    Text(m.text, Modifier.padding(10.dp))
                                }
                            }
                        }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(
                            input.value, { input.value = it },
                            Modifier.weight(1f), singleLine = true,
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                            keyboardActions = KeyboardActions(onSend = { send() })
                        )
                        Spacer(Modifier.width(8.dp))
                        if (busy.value) CircularProgressIndicator(Modifier.size(28.dp))
                        else Button(onClick = { send() }) { Text("发送") }
                    }
                }
            }
        }
    }
}
