package io.github.kamisugimizuki.veranima

import android.content.ContentUris
import android.net.Uri
import android.provider.MediaStore
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties

/** QQ/微信式本地相册多选：MediaStore 查询 + Coil 缩略图 + 3 列网格 + 选中编号。
 *  系统 PhotoPicker 在无 Google 模块的 MuMu 上会降级成 DocumentsUI（文件浏览器而非相册），
 *  故自绘。调用前需已授权 READ_MEDIA_IMAGES(33+) / READ_EXTERNAL_STORAGE(<33)。 */
@Composable
fun AlbumPicker(maxPick: Int = 4, onPick: (List<Uri>) -> Unit, onDismiss: () -> Unit) {
    val ctx = LocalContext.current
    val uris = remember { mutableStateListOf<Uri>() }
    val selected = remember { mutableStateMapOf<Uri, Int>() }  // uri → 选择顺序(1-based)
    var loaded by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        runCatching {
            ctx.contentResolver.query(
                MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                arrayOf(MediaStore.Images.Media._ID),
                null, null,
                MediaStore.Images.Media.DATE_ADDED + " DESC"
            )?.use { cur ->
                val idCol = cur.getColumnIndexOrThrow(MediaStore.Images.Media._ID)
                while (cur.moveToNext()) {
                    uris.add(ContentUris.withAppendedId(
                        MediaStore.Images.Media.EXTERNAL_CONTENT_URI, cur.getLong(idCol)))
                }
            }
        }
        loaded = true
    }

    Dialog(onDismissRequest = onDismiss,
           properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Box(Modifier.fillMaxSize().background(Canvas)) {
            Column(Modifier.fillMaxSize()) {
                // 顶栏：取消 | 相册 | 发送(N)
                Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    TextButton(onClick = onDismiss) { Text("取消", color = Muted) }
                    Text("相册", style = MaterialTheme.typography.headlineSmall)
                    Spacer(Modifier.weight(1f))
                    if (selected.isNotEmpty()) {
                        Button(onClick = {
                            val ordered = selected.entries.sortedBy { it.value }.map { it.key }
                            onPick(ordered)
                        }, colors = ButtonDefaults.buttonColors(Coral),
                            shape = MaterialTheme.shapes.small) { Text("发送(${selected.size})") }
                    }
                }
                if (!loaded) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator(color = Coral)
                    }
                } else if (uris.isEmpty()) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text("相册是空的", color = Muted)
                    }
                } else {
                    LazyVerticalGrid(GridCells.Fixed(3), Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(horizontal = 6.dp, vertical = 6.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        items(uris) { uri ->
                            val order = selected[uri]
                            Box(Modifier.aspectRatio(1f).clickable {
                                if (order == null) {
                                    if (selected.size < maxPick) selected[uri] = selected.size + 1
                                } else {
                                    // 重选：后续编号顺移（QQ/微信行为）
                                    val removed = order
                                    selected.remove(uri)
                                    selected.entries.filter { it.value > removed }
                                        .forEach { selected[it.key] = it.value - 1 }
                                }
                            }) {
                                coil.compose.AsyncImage(
                                    model = uri,
                                    contentDescription = null,
                                    contentScale = ContentScale.Crop,
                                    modifier = Modifier.fillMaxSize()
                                        .then(if (order != null) Modifier.border(3.dp, Coral) else Modifier)
                                )
                                if (order != null) {
                                    Box(Modifier.align(Alignment.TopEnd).padding(4.dp)
                                        .size(22.dp).background(Coral, CircleShape),
                                        contentAlignment = Alignment.Center) {
                                        Text("$order", color = Color.White,
                                            style = MaterialTheme.typography.bodySmall)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
