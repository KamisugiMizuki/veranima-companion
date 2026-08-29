package io.github.kamisugimizuki.veranima

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.chaquo.python.Python
import kotlinx.coroutines.*
import org.json.JSONObject

/** 前台服务：保活核心 + 主动消息通知出口。
 *  循环 = bridge.drain_pending() → 逐条发通知（同 QQ adapter 的 tick 线程模式）。 */
class CompanionService : Service() {

    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startAsForeground()
        scope.launch {
            while (isActive) {
                try {
                    val bridge = Python.getInstance().getModule("bridge")
                    val r = JSONObject(bridge.callAttr("drain_pending").toString())
                    val msgs = r.getJSONArray("messages")
                    for (i in 0 until msgs.length()) notifyProactive(msgs.getString(i))
                } catch (e: Exception) {
                    // 核心未 boot / drain 失败：下轮再试，服务不死
                }
                delay(30_000)
            }
        }
        return START_STICKY
    }

    private fun startAsForeground() {
        val pi = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val notif: Notification = NotificationCompat.Builder(this, CHANNEL_STATUS)
            .setSmallIcon(android.R.drawable.sym_call_incoming)
            .setContentTitle("駒川 冬乃")
            // 安卓政策：前台服务必须挂常驻通知，去不掉；占位压到最低（MIN 通道、空文本、不折叠）
            .setContentText(" ")
            .setOngoing(true)
            .setContentIntent(pi)
            .build()
        startForeground(NOTIF_STATUS, notif)
    }

    private fun notifyProactive(text: String) {
        val pi = PendingIntent.getActivity(
            this, 1, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        mgr().notify(System.currentTimeMillis().toInt(),
            NotificationCompat.Builder(this, CHANNEL_PROACTIVE)
                .setSmallIcon(android.R.drawable.sym_call_incoming)
                .setContentTitle("驹川冬乃")
                .setContentText(text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .setAutoCancel(true)
                .setContentIntent(pi)
                .build())
    }

    private fun mgr(): NotificationManager =
        getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            mgr().createNotificationChannel(
                NotificationChannel(CHANNEL_STATUS, "冬乃运行状态", NotificationManager.IMPORTANCE_MIN))
            mgr().createNotificationChannel(
                NotificationChannel(CHANNEL_PROACTIVE, "冬乃的消息", NotificationManager.IMPORTANCE_DEFAULT))
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL_STATUS = "status"
        private const val CHANNEL_PROACTIVE = "proactive"
        private const val NOTIF_STATUS = 1
    }
}
