package io.github.kamisugimizuki.veranima

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
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
                startAsForeground()  // MIUI 可划掉常驻通知：每轮重申，划掉 ≤30s 自动回来
                try {
                    val bridge = Python.getInstance().getModule("bridge")
                    val r = JSONObject(bridge.callAttr("drain_pending").toString())
                    val msgs = r.getJSONArray("messages")
                    for (i in 0 until msgs.length()) notifyProactive(msgs.getString(i))
                    // 前台应用感知（UsageStats→包名+app名→LLM 判断动作）：仅授权后生效，内部有冷却
                    foregroundApp()?.let { (pkg, label) ->
                        bridge.callAttr("visual_note", pkg, label)
                    }
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
            .setSmallIcon(R.drawable.ic_stat_veranima)
            .setColorized(true)
            .setColor(0xFF7B5EA7.toInt())
            .setLargeIcon(bigIcon())
            .setContentTitle("冬乃正在运行（保活通知）")
            .setContentText("用于维持主动发言与记忆后台；划掉会自动回来，退出应用才是真停")
            .setOngoing(true)
            .setContentIntent(pi)
            .build()
        startForeground(NOTIF_STATUS, notif)
    }

    private fun bigIcon(): android.graphics.Bitmap =
        BitmapFactory.decodeResource(resources, R.mipmap.ic_launcher)

    private fun notifyProactive(text: String) {
        val pi = PendingIntent.getActivity(
            this, 1, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        mgr().notify(System.currentTimeMillis().toInt(),
            NotificationCompat.Builder(this, CHANNEL_PROACTIVE)
                .setSmallIcon(R.drawable.ic_stat_veranima)
                .setColorized(true)
                .setColor(0xFF7B5EA7.toInt())
                .setLargeIcon(bigIcon())
                .setContentTitle("驹川冬乃")
                .setContentText(text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .setAutoCancel(true)
                .setContentIntent(pi)
                .build())
        // 横幅 → 应用内气泡同步：通知发出即广播，MainActivity 收到后 loadHistory
        // （core 已落库 record_proactive_message，UI 以 DB 为准，只差刷新钩子）
        runCatching { sendBroadcast(Intent(ACTION_PROACTIVE)) }
    }

    private fun mgr(): NotificationManager =
        getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    /** 前台应用 → (包名, app 显示名)（UsageStats；未授权/无前台返回 null）。
     *  动作判断交给 bridge 的 LLM（包名+app 名 → 动作短语），这里不维护映射表。 */
    private fun foregroundApp(): Pair<String, String>? {
        return try {
            if (android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.LOLLIPOP) return null
            val usm = getSystemService(Context.USAGE_STATS_SERVICE) as? android.app.usage.UsageStatsManager
                ?: return null
            val end = System.currentTimeMillis()
            val events = usm.queryEvents(end - 60_000, end)
            var pkg: String? = null
            val e = android.app.usage.UsageEvents.Event()
            while (events.hasNextEvent()) {
                events.getNextEvent(e)
                if (e.eventType == android.app.usage.UsageEvents.Event.ACTIVITY_RESUMED) pkg = e.packageName
            }
            if (pkg == null || pkg == packageName) return null  // 本应用自身不感知
            val pm = packageManager
            val label = try {
                pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
            } catch (e: Exception) { pkg }
            pkg to label
        } catch (e: Exception) {
            null
        }
    }

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            mgr().createNotificationChannel(
                NotificationChannel(CHANNEL_STATUS, "冬乃运行状态", NotificationManager.IMPORTANCE_MIN))
            // 主动消息=heads-up 横幅弹出（IMPORTANCE_DEFAULT 在部分 MIUI 上只进抽屉不弹）
            mgr().createNotificationChannel(
                NotificationChannel(CHANNEL_PROACTIVE, "冬乃的消息", NotificationManager.IMPORTANCE_HIGH)
                    .apply { enableLights(false); enableVibration(true) })
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
        /** 主动消息通知发出 → MainActivity 刷新气泡的广播 action */
        const val ACTION_PROACTIVE = "io.github.kamisugimizuki.veranima.PROACTIVE"
    }
}
