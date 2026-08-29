package io.github.kamisugimizuki.veranima

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.chaquo.python.Python
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** 设置页（spike 级：表单直连 bridge，保存=写 config.yaml，改动后点右上"重启生效"）。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(onBack: () -> Unit) {
    val bridge = remember { Python.getInstance().getModule("bridge") }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    var s by remember { mutableStateOf<JSONObject?>(null) }
    var dirty by remember { mutableStateOf(false) }
    var provider by remember { mutableStateOf("bocha") }
    var minGap by remember { mutableStateOf("30") }
    var maxDay by remember { mutableStateOf("6") }
    var activeChar by remember { mutableStateOf("") }
    var chars by remember { mutableStateOf(listOf<String>()) }

    suspend fun reload() {
        val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("get_settings").toString() })
        if (!o.optBoolean("ok")) { snackbar.showSnackbar("读取失败: ${o.optString("error")}"); return }
        s = o
        provider = o.getString("search_provider")
        minGap = o.getInt("proactive_min_gap").toString()
        maxDay = o.getInt("proactive_max_per_day").toString()
        activeChar = o.getString("active_character")
        chars = o.getJSONArray("characters").let { a -> (0 until a.length()).map { a.getString(it) } }
    }
    LaunchedEffect(Unit) { reload() }

    fun report(r: JSONObject, name: String) {
        scope.launch {
            snackbar.showSnackbar(if (r.optBoolean("ok")) "$name ✓ ${r.optString("detail")}"
                                  else "$name 失败: ${r.optString("error")}")
        }
        if (r.optBoolean("restart_required")) dirty = true
    }
    fun set(key: String, value: String) = scope.launch {
        report(JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("set_setting", key, value).toString() }), key)
    }
    fun act(name: String, arg: String? = null) = scope.launch {
        val o = JSONObject(withContext(Dispatchers.IO) {
            if (arg == null) bridge.callAttr(name) else bridge.callAttr(name, arg)
        }.toString())
        if (o.has("path")) o.put("detail", o.getString("path"))
        if (o.has("memories")) o.put("detail", "memories=" + o.getInt("memories"))
        report(o, name)
    }

    Scaffold(snackbarHost = { SnackbarHost(snackbar) }, topBar = {
        TopAppBar(title = { Text("设置") },
            navigationIcon = { TextButton(onClick = onBack) { Text("返回") } },
            actions = { if (dirty) TextButton(onClick = { restartApp(ctx) }) { Text("重启生效") } })
    }) { pad ->
        Column(Modifier.padding(pad).padding(16.dp).verticalScroll(rememberScrollState())) {
            val st = s
            if (st == null) { Text("读取设置中…"); return@Column }
            Text("API Keys（输入后按回车保存；留空不动）", style = MaterialTheme.typography.titleSmall)
            CommitRow("LLM（当前 ${st.getJSONObject("keys").getString("llm_api_key")}）", "", secret = true) {
                set("llm_api_key", it) }
            CommitRow("Embedding（当前 ${st.getJSONObject("keys").getString("embedding_api_key")}）", "", secret = true) {
                set("embedding_api_key", it) }
            CommitRow("搜索（当前 ${st.getJSONObject("keys").getString("search_api_key")}）", "", secret = true) {
                set("search_api_key", it) }

            Spacer(Modifier.height(16.dp))
            Text("搜索 provider", style = MaterialTheme.typography.titleSmall)
            Row {
                listOf("bocha", "searxng").forEach {
                    Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                        RadioButton(selected = provider == it, onClick = { provider = it; set("search_provider", it) })
                        Text(it, Modifier.padding(end = 16.dp))
                    }
                }
            }

            Spacer(Modifier.height(16.dp))
            Text("主动发言", style = MaterialTheme.typography.titleSmall)
            CommitRow("最小间隔（分钟）", minGap) { minGap = it; set("proactive_min_gap", it) }
            CommitRow("每日上限（条）", maxDay) { maxDay = it; set("proactive_max_per_day", it) }
            Spacer(Modifier.height(16.dp))
            Text("角色（点名字切换；导出=轻量 .char 进 inbox）", style = MaterialTheme.typography.titleSmall)
            chars.forEach { c ->
                Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    RadioButton(selected = activeChar == c, onClick = { activeChar = c; set("active_character", c) })
                    Text(c, Modifier.padding(end = 8.dp))
                    TextButton(onClick = { act("role_export", c) }) { Text("导出") }
                }
            }
            TextButton(onClick = { act("role_import") }) { Text("导入 inbox/*.char") }

            Spacer(Modifier.height(16.dp))
            Text("共享记忆备份（adb: push backup.zip 进 inbox 再导入；导出后 pull backup_out.zip）",
                style = MaterialTheme.typography.titleSmall)
            Row {
                Button(onClick = { act("backup_export") }) { Text("导出") }
                Spacer(Modifier.width(12.dp))
                Button(onClick = { act("backup_import") }) { Text("导入") }
            }
            Spacer(Modifier.height(16.dp))
            TextButton(onClick = { openBatterySettings(ctx) }) { Text("电池优化白名单") }
        }
    }
}

@Composable
private fun CommitRow(label: String, initial: String, secret: Boolean = false, onCommit: (String) -> Unit) {
    var value by remember(initial) { mutableStateOf(initial) }
    Column {
        Text(label, style = MaterialTheme.typography.bodySmall)
        OutlinedTextField(
            value, { value = it }, Modifier.fillMaxWidth(), singleLine = true,
            visualTransformation = if (secret) PasswordVisualTransformation()
                                   else androidx.compose.ui.text.input.VisualTransformation.None,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { onCommit(value.trim()) }),
        )
    }
}

private fun restartApp(ctx: Context) {
    ctx.startActivity(Intent(ctx, MainActivity::class.java)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
    (ctx as? Activity)?.finishAndRemoveTask()
    Runtime.getRuntime().exit(0)  // chaquopy 进程内模块态必须真杀才干净
}

private fun openBatterySettings(ctx: Context) {
    try {
        ctx.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (e: Exception) {
        ctx.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
            .setData(android.net.Uri.fromParts("package", ctx.packageName, null))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
