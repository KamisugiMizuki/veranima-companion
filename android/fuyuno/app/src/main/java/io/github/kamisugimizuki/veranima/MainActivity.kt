package io.github.kamisugimizuki.veranima

import android.os.Bundle
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
        setContent {
            MaterialTheme {
                val msgs = remember { mutableStateListOf<Msg>() }
                val status = remember { mutableStateOf("启动核心…") }
                val input = remember { mutableStateOf("") }
                val busy = remember { mutableStateOf(false) }
                val scope = rememberCoroutineScope()

                LaunchedEffect(Unit) {
                    val files = applicationContext.filesDir.absolutePath
                    val native = applicationInfo.nativeLibraryDir
                    val r = withContext(Dispatchers.IO) { bridge.callAttr("boot", files, native).toString() }
                    status.value = "boot: $r"
                }

                Column(Modifier.fillMaxSize().padding(12.dp)) {
                    Text(status.value, style = MaterialTheme.typography.bodySmall)
                    LazyColumn(Modifier.weight(1f)) {
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
                            keyboardActions = KeyboardActions(onSend = {
                                val q = input.value.trim()
                                if (q.isNotEmpty() && !busy.value) {
                                    msgs.add(Msg(true, q)); input.value = ""
                                    busy.value = true; scope.launch {
                                        val r = withContext(Dispatchers.IO) {
                                            bridge.callAttr("chat", q).toString()
                                        }
                                        msgs.add(Msg(false, r))
                                        busy.value = false
                                    }
                                }
                            })
                        )
                        Spacer(Modifier.width(8.dp))
                        if (busy.value) CircularProgressIndicator(Modifier.size(28.dp))
                        else Button(onClick = {
                            val q = input.value.trim()
                            if (q.isNotEmpty()) {
                                msgs.add(Msg(true, q)); input.value = ""
                                busy.value = true; scope.launch {
                                    val r = withContext(Dispatchers.IO) { bridge.callAttr("chat", q).toString() }
                                    msgs.add(Msg(false, r)); busy.value = false
                                }
                            }
                        }) { Text("发送") }
                    }
                }
            }
        }
    }
}
