package io.github.kamisugimizuki.veranima

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
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

/** 设置页：表单直连 bridge（写 config.yaml，改动后点右上"重启生效"）。
 *  导入/导出走 SAF 系统文件选择器（GetContent/OpenDocument），不再依赖 inbox 约定。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(onBack: () -> Unit) {
    val bridge = remember { Python.getInstance().getModule("bridge") }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    var s by remember { mutableStateOf<JSONObject?>(null) }
    var dirty by remember { mutableStateOf(false) }
    var activeChar by remember { mutableStateOf("") }
    var chars by remember { mutableStateOf(listOf<String>()) }

    suspend fun reload() {
        val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("get_settings").toString() })
        if (!o.optBoolean("ok")) { snackbar.showSnackbar("读取失败: ${o.optString("error")}"); return }
        s = o
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
    // SAF：导出=用户选完目标后，bridge 现场生成 inbox 文件再拷过去（顺序=选→生成→拷贝，无竞态）
    val cr = ctx.contentResolver
    var pendingRole by remember { mutableStateOf<String?>(null) }
    fun copyOut(name: String, uri: android.net.Uri): Long {
        val src = java.io.File(ctx.filesDir, "inbox/$name")
        cr.openOutputStream(uri)?.use { out -> src.inputStream().use { inp -> inp.copyTo(out) } }
        return src.length()
    }
    fun stageIn(uri: android.net.Uri, name: String) {
        val dst = java.io.File(ctx.filesDir, "inbox"); dst.mkdirs()
        cr.openInputStream(uri)?.use { inp -> dst.resolve(name).outputStream().use { out -> inp.copyTo(out) } }
    }
    val exportBackup = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        if (uri != null) scope.launch {
            val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("backup_export").toString() })
            if (!o.optBoolean("ok")) { snackbar.showSnackbar("备份生成失败: ${o.optString("error")}"); return@launch }
            val n = withContext(Dispatchers.IO) { copyOut("backup_out.zip", uri) }
            snackbar.showSnackbar("记忆备份已导出 ${n / 1024}KB")
        }
    }
    val importBackup = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) scope.launch {
            withContext(Dispatchers.IO) { stageIn(uri, "backup.zip") }
            act("backup_import")
        }
    }
    val exportRole = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        if (uri != null) scope.launch {
            val role = pendingRole ?: return@launch
            val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("role_export", role).toString() })
            if (!o.optBoolean("ok")) { snackbar.showSnackbar("角色包生成失败: ${o.optString("error")}"); return@launch }
            val n = withContext(Dispatchers.IO) { copyOut("role_pending.char", uri) }
            snackbar.showSnackbar("角色包已导出 ${n / 1024}KB")
        }
    }
    val importRole = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) scope.launch {
            withContext(Dispatchers.IO) { stageIn(uri, "pending.char") }
            act("role_import")
        }
    }

    Scaffold(snackbarHost = { SnackbarHost(snackbar) },
             containerColor = Canvas,
             topBar = {
        // 聊天页风格统一（2026-08-29）：衬线页头、无灰底；珊瑚只留给「重启生效」
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) { Text("返回", color = Muted) }
            Text("设置", style = MaterialTheme.typography.headlineSmall)
            Spacer(Modifier.weight(1f))
            if (dirty) Button(onClick = { restartApp(ctx) },
                colors = ButtonDefaults.buttonColors(Coral),
                shape = MaterialTheme.shapes.small) { Text("重启生效") }
        }
    }) { pad ->
        Column(Modifier.padding(pad).padding(horizontal = 16.dp).verticalScroll(rememberScrollState())) {
            val st = s
            if (st == null) { Text("读取设置中…", color = Muted); return@Column }
            val f = st.getJSONObject("fields")

            SectionCard("LLM") {
                CommitRow("API Key（当前 ${f.getString("llm_api_key")}）", "", true) { set("llm_api_key", it) }
                CommitRow("Base URL", f.getString("llm_base_url")) { set("llm_base_url", it) }
                CommitRow("模型名", f.getString("llm_model")) { set("llm_model", it) }
                CommitRow("视觉模型名（发图时用；留空=不支持发图）", f.getString("llm_vision_model")) { set("llm_vision_model", it) }
            }

            SectionCard("Embedding（记忆召回的语义向量，安卓走远程 API，必填）") {
                CommitRow("API Key（当前 ${f.getString("embedding_api_key")}）", "", true) { set("embedding_api_key", it) }
                CommitRow("Base URL", f.getString("embedding_base_url")) { set("embedding_base_url", it) }
                CommitRow("模型名", f.getString("embedding_model")) { set("embedding_model", it) }
            }

            SectionCard("联网搜索（博查 Bocha，安卓唯一后端）") {
                CommitRow("API Key（当前 ${f.getString("search_api_key")}）", "", true) { set("search_api_key", it) }
                CommitRow("Base URL", f.getString("search_base_url")) { set("search_base_url", it) }
            }

            SectionCard("角色（点名字切换；导出=轻量 .char 不含立绘语音）") {
                chars.forEach { c ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(selected = activeChar == c, onClick = { activeChar = c; set("active_character", c) })
                        Text(c, Modifier.padding(end = 8.dp))
                        TextButton(onClick = { pendingRole = c; exportRole.launch("veranima-role-$c.char") }) { Text("导出…", color = Muted) }
                    }
                }
                TextButton(onClick = { importRole.launch("*/*") }) { Text("导入角色包（.char）", color = Muted) }
            }

            SectionCard("共享记忆备份（导出=全部角色的共同记忆 zip；导入=全量覆盖）") {
                Row {
                    Button(onClick = { exportBackup.launch("veranima-backup.zip") },
                        colors = ButtonDefaults.buttonColors(Coral), shape = MaterialTheme.shapes.small) { Text("导出…") }
                    Spacer(Modifier.width(12.dp))
                    Button(onClick = { importBackup.launch("application/zip") },
                        colors = ButtonDefaults.buttonColors(Coral), shape = MaterialTheme.shapes.small) { Text("导入…") }
                }
            }
            TextButton(onClick = { openBatterySettings(ctx) }) { Text("电池优化白名单", color = Muted) }
            Spacer(Modifier.height(24.dp))
        }
    }
}

/** 设置页分区卡：surface-card 色块即层级（无阴影），hairline 边 + 圆角 12（聊天页同款） */
@Composable
private fun SectionCard(title: String, content: @Composable ColumnScope.() -> Unit) {
    Surface(color = SurfaceCard, shape = RoundedCornerShape(12.dp),
            border = androidx.compose.foundation.BorderStroke(1.dp, Hairline),
            modifier = Modifier.fillMaxWidth().padding(bottom = 14.dp)) {
        Column(Modifier.padding(16.dp)) {
            Text(title, style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.height(8.dp))
            content()
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
