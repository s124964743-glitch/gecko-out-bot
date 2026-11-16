package app.geckobot

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.view.Display
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityWindowInfo
import android.widget.TextView
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * 無障礙服務：
 *  - takeScreenshot() 截圖（Android 11+，不需要螢幕錄影授權）
 *  - dispatchGesture() 以多點路徑模擬拖曳
 *  - Python(Chaquopy) 裡的 brain.step() 負責辨識、求解、關閉彈窗
 */
class GeckoService : AccessibilityService() {

    companion object {
        @Volatile var instance: GeckoService? = null
        private val buf = ArrayDeque<String>()
        private val fmt = SimpleDateFormat("HH:mm:ss", Locale.US)

        fun log(s: String) {
            synchronized(buf) {
                buf.addLast("${fmt.format(Date())} $s")
                while (buf.size > 300) buf.removeFirst()
            }
        }

        fun logLines(): List<String> = synchronized(buf) { buf.toList().reversed() }
    }

    @Volatile var running = false
    @Volatile var moves = 0
    @Volatile var levels = 0

    private var worker: Thread? = null
    private val exec = Executors.newSingleThreadExecutor()
    private var overlay: TextView? = null
    private var overlayParams: WindowManager.LayoutParams? = null
    private var statusTv: TextView? = null
    private var statusParams: WindowManager.LayoutParams? = null
    private var unknownSaved = 0
    @Volatile private var fgPkg: String? = null
    @Volatile private var lastGameMs = 0L
    private val launchers: Set<String> by lazy {
        try {
            packageManager.queryIntentActivities(
                Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME), 0
            ).map { it.activityInfo.packageName }.toSet()
        } catch (_: Throwable) { emptySet() }
    }

    /** 目標遊戲套件：使用者選的；沒選就自動找名稱含 gecko 的 App */
    fun gamePkg(): String? {
        Prefs.gamePkg(this)?.let { return it }
        return try {
            val pm = packageManager
            pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0)
                .firstOrNull { it.loadLabel(pm).toString().contains("gecko", true) && it.activityInfo.packageName != packageName }
                ?.activityInfo?.packageName
        } catch (_: Throwable) { null }
    }

    /** 即時查詢「最上層的應用程式視窗」是哪個 App（比只靠視窗事件可靠，回到桌面立刻知道）。 */
    private fun livePkg(): String? {
        try {
            var best: AccessibilityWindowInfo? = null
            for (w in windows) {
                if (w.type != AccessibilityWindowInfo.TYPE_APPLICATION) continue
                if (best == null || w.layer > best.layer) best = w
            }
            val p = best?.root?.packageName?.toString()
            if (p != null) return p
        } catch (_: Throwable) {}
        return try { rootInActiveWindow?.packageName?.toString() } catch (_: Throwable) { null }
    }

    private fun inGame(): Boolean {
        val g = gamePkg() ?: return false
        // 以即時查詢為準；查不到才退回事件記錄。只要查得到而不是遊戲，就一律視為不在遊戲裡。
        val now = livePkg() ?: fgPkg
        return now == g
    }

    /** 不在遊戲裡就不操作、不顯示懸浮按鈕 */
    private fun isIdleScreen(): Boolean = !inGame()

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        try {
            if (!Python.isStarted()) Python.start(AndroidPlatform(applicationContext))
            log("服務已連線，Python 已啟動")
        } catch (e: Throwable) {
            log("Python 啟動失敗: $e")
        }
        applyOverlay()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.eventType == AccessibilityEvent.TYPE_WINDOWS_CHANGED) {
            updateOverlayVisibility()
            return
        }
        if (event?.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return
        val p = event.packageName?.toString() ?: return
        // 本 App 只有 MainActivity 才算「在本 App 裡」；懸浮按鈕自己的視窗事件要忽略
        if (p == packageName) {
            if (event.className?.toString()?.endsWith("MainActivity") != true) return
        }
        // 系統介面、輸入法、ROG 遊戲工具列(Game Genie)等浮動視窗不代表「目前的 App」
        // （ROG 桌面的套件名也是 com.asus.launcher，不能一概略過，否則回到桌面時會以為還在遊戲裡）
        if (p == "com.android.systemui" || p.contains("inputmethod") || (p.startsWith("com.asus") && p !in launchers)) return
        fgPkg = p
        updateOverlayVisibility()
    }

    override fun onInterrupt() {}

    override fun onDestroy() {
        running = false
        removeOverlay()
        instance = null
        super.onDestroy()
    }

    // ------------------------------------------------------------ 狀態提示（懸浮按鈕旁的小字）
    fun setStatus(t: String) {
        val tv = statusTv ?: return
        tv.post { tv.text = t }
    }

    // ------------------------------------------------------------ 開關
    fun toggle() {
        if (running) stop() else start()
    }

    private fun start() {
        if (running) return
        running = true
        lastGameMs = System.currentTimeMillis()   // 給 60 秒緩衝，讓使用者從本 App 按開始後切到遊戲
        updateOverlay()
        worker = Thread { loop() }.also { it.isDaemon = true; it.start() }
        setStatus("開始…")
        log("開始")
    }

    private fun stop() {
        running = false
        updateOverlay()
        setStatus("已暫停")
        log("暫停")
    }

    // ------------------------------------------------------------ 主迴圈
    private fun loop() {
        val rawFile = File(cacheDir, "shot.raw")
        val brain = try {
            Python.getInstance().getModule("brain").also {
                it.callAttr("configure", Prefs.joinEvents(this))
            }
        } catch (e: Throwable) {
            log("載入 brain 失敗: $e"); running = false; updateOverlay(); return
        }
        var fails = 0
        var idleLogged = false
        var backs = 0
        var idleSince = 0L
        while (running) {
            try {
                if (isIdleScreen()) {
                    val nowMs = System.currentTimeMillis()
                    if (!idleLogged) {
                        log("目前不在 Gecko Out，暫停操作，回到遊戲後自動繼續")
                        setStatus("不在遊戲裡，暫停操作")
                        idleLogged = true; idleSince = nowMs
                    }
                    // 只有「剛從遊戲跳出去」(例如廣告開了別的 App) 才幫忙按返回，最多 3 次；
                    // 使用者自己回桌面或開別的 App，一律不碰。
                    val f = livePkg() ?: fgPkg
                    if (nowMs - lastGameMs < 15000 && backs < 3 &&
                        f != null && f != packageName && f !in launchers && f != "com.android.systemui") {
                        if (nowMs - idleSince > 4000) { backs++; idleSince = nowMs; performGlobalAction(GLOBAL_ACTION_BACK) }
                    }
                    // 離開遊戲超過 60 秒就自動停止，避免忘了關、之後亂操作
                    if (nowMs - lastGameMs > 60000) {
                        log("離開遊戲超過 60 秒，已自動停止（回到遊戲後再按懸浮按鈕開始）")
                        running = false
                        break
                    }
                    Thread.sleep(1000)
                    continue
                }
                lastGameMs = System.currentTimeMillis()
                backs = 0
                idleLogged = false
                val bmp = screenshot(rawFile)
                if (bmp == null) {
                    fails++
                    if (fails % 10 == 1) log("截圖失敗(${fails})，稍後重試")
                    setStatus("截圖失敗($fails)，重試中")
                    Thread.sleep(600)
                    continue
                }
                fails = 0
                setStatus("辨識/思考中…（大關卡要幾秒）")
                val t0 = System.currentTimeMillis()
                val res = brain.callAttr("step", rawFile.absolutePath, bmp.width, bmp.height).toString()
                val dt = System.currentTimeMillis() - t0
                val j = JSONObject(res)
                val msg = j.optString("msg")
                if (msg.isNotEmpty()) { log(msg); setStatus(msg + (if (dt > 1500) "  (思考 ${dt / 1000}s)" else "")) }
                if (j.optBoolean("unknown")) saveUnknown(bmp)
                bmp.recycle()
                // 解題/辨識可能花好幾秒；送手勢前再確認一次還在遊戲裡，否則整個動作丟掉
                if (!inGame()) { log("已離開遊戲，取消這次操作"); continue }
                when (j.getString("a")) {
                    "tap" -> gesture(listOf(j.getInt("x") to j.getInt("y")), 60)
                    "drag" -> {
                        val arr = j.getJSONArray("pts")
                        val pts = (0 until arr.length()).map { arr.getJSONArray(it).let { p -> p.getInt(0) to p.getInt(1) } }
                        gesture(pts, ((pts.size - 1) * 140L + 200L))
                    }
                    "back" -> performGlobalAction(GLOBAL_ACTION_BACK)
                }
                try {
                    val st = JSONObject(Python.getInstance().getModule("brain").callAttr("stats").toString())
                    moves = st.getInt("moves"); levels = st.getInt("levels")
                } catch (_: Throwable) {}
                Thread.sleep((j.optDouble("sleep", 1.0) * 1000).toLong())
            } catch (e: InterruptedException) {
                break
            } catch (e: Throwable) {
                log("錯誤: ${e.message ?: e}")
                setStatus("錯誤: ${e.message ?: e}")
                try { Thread.sleep(1500) } catch (_: InterruptedException) {}
            }
        }
        updateOverlay()
    }

    // ------------------------------------------------------------ 截圖
    private fun screenshot(raw: File): Bitmap? {
        val latch = CountDownLatch(1)
        var out: Bitmap? = null
        takeScreenshot(Display.DEFAULT_DISPLAY, exec, object : TakeScreenshotCallback {
            override fun onSuccess(r: ScreenshotResult) {
                try {
                    val hb = r.hardwareBuffer
                    val hw = Bitmap.wrapHardwareBuffer(hb, r.colorSpace)
                    if (hw != null) {
                        val bmp = hw.copy(Bitmap.Config.ARGB_8888, false)
                        hw.recycle()
                        val bb = ByteBuffer.allocateDirect(bmp.width * bmp.height * 4)
                        bmp.copyPixelsToBuffer(bb)
                        bb.rewind()
                        FileOutputStream(raw).channel.use { ch -> while (bb.hasRemaining()) ch.write(bb) }
                        out = bmp
                    }
                    hb.close()
                } catch (e: Throwable) {
                    log("截圖處理失敗: $e")
                }
                latch.countDown()
            }

            override fun onFailure(errorCode: Int) {
                latch.countDown()
            }
        })
        latch.await(6, TimeUnit.SECONDS)
        return out
    }

    private fun saveUnknown(bmp: Bitmap) {
        if (unknownSaved >= 12) return
        try {
            val dir = getExternalFilesDir(null) ?: return
            val f = File(dir, "unknown_${System.currentTimeMillis() / 1000}.png")
            FileOutputStream(f).use { bmp.compress(Bitmap.CompressFormat.PNG, 90, it) }
            unknownSaved++
            log("已存下未知畫面：${f.name}")
        } catch (_: Throwable) {}
    }

    // ------------------------------------------------------------ 手勢
    private fun gesture(pts: List<Pair<Int, Int>>, durMs: Long): Boolean {
        val p = Path()
        p.moveTo(pts[0].first.toFloat(), pts[0].second.toFloat())
        for (i in 1 until pts.size) p.lineTo(pts[i].first.toFloat(), pts[i].second.toFloat())
        val g = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(p, 0, durMs.coerceAtLeast(1)))
            .build()
        val latch = CountDownLatch(1)
        var ok = false
        val accepted = dispatchGesture(g, object : GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) { ok = true; latch.countDown() }
            override fun onCancelled(gestureDescription: GestureDescription?) { latch.countDown() }
        }, null)
        if (!accepted) return false
        latch.await(durMs + 3000, TimeUnit.MILLISECONDS)
        return ok
    }

    // ------------------------------------------------------------ 懸浮按鈕
    fun applyOverlay() {
        if (Prefs.overlay(this)) addOverlay() else removeOverlay()
    }

    private fun addOverlay() {
        if (overlay != null) return
        val wm = getSystemService(WINDOW_SERVICE) as WindowManager
        val d = resources.displayMetrics.density
        val size = (52 * d).toInt()
        val tv = TextView(this).apply {
            text = "▶"
            textSize = 20f
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(Color.parseColor("#CC2E7D32"))
            }
            alpha = 0.85f
        }
        val lp = WindowManager.LayoutParams(
            size, size,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = (6 * d).toInt()
            // 放在畫面最下方的空白處（遊戲道具列下面、主畫面導覽列上面），不遮到棋盤
            y = (resources.displayMetrics.heightPixels * 0.915).toInt()
        }
        var downX = 0f; var downY = 0f; var startX = 0; var startY = 0; var moved = false
        tv.setOnTouchListener { _: View, e: MotionEvent ->
            when (e.action) {
                MotionEvent.ACTION_DOWN -> { downX = e.rawX; downY = e.rawY; startX = lp.x; startY = lp.y; moved = false }
                MotionEvent.ACTION_MOVE -> {
                    val dx = e.rawX - downX; val dy = e.rawY - downY
                    if (Math.abs(dx) > 12 || Math.abs(dy) > 12) moved = true
                    if (moved) {
                        lp.x = startX + dx.toInt(); lp.y = startY + dy.toInt(); wm.updateViewLayout(tv, lp)
                        val sp = statusParams; val sv = statusTv
                        if (sp != null && sv != null) {
                            sp.x = lp.x + size + (4 * d).toInt(); sp.y = lp.y + (8 * d).toInt()
                            try { wm.updateViewLayout(sv, sp) } catch (_: Throwable) {}
                        }
                    }
                }
                MotionEvent.ACTION_UP -> if (!moved) toggle()
            }
            true
        }
        try {
            wm.addView(tv, lp)
            overlay = tv; overlayParams = lp
            // 狀態小字：貼在按鈕右邊，跟著按鈕一起移動，不攔截觸控
            val st = TextView(this).apply {
                textSize = 12f
                setTextColor(Color.WHITE)
                maxLines = 2
                maxWidth = (230 * d).toInt()
                setPadding((8 * d).toInt(), (4 * d).toInt(), (8 * d).toInt(), (4 * d).toInt())
                gravity = Gravity.CENTER_VERTICAL
                background = GradientDrawable().apply { cornerRadius = 10 * d; setColor(Color.parseColor("#B3000000")) }
                text = if (running) "執行中" else "點 ▶ 開始"
            }
            val slp = WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT, (36 * d).toInt(),
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT
            ).apply {
                gravity = Gravity.TOP or Gravity.START
                x = lp.x + size + (4 * d).toInt()
                y = lp.y + (8 * d).toInt()
            }
            try { wm.addView(st, slp); statusTv = st; statusParams = slp } catch (_: Throwable) {}
            updateOverlay()
            updateOverlayVisibility()
        } catch (e: Throwable) {
            log("懸浮按鈕建立失敗: $e")
        }
    }

    private fun removeOverlay() {
        val tv = overlay ?: return
        val wm = getSystemService(WINDOW_SERVICE) as WindowManager
        try { wm.removeView(tv) } catch (_: Throwable) {}
        statusTv?.let { try { wm.removeView(it) } catch (_: Throwable) {} }
        statusTv = null; statusParams = null
        overlay = null
    }

    fun updateOverlayVisibility() {
        val tv = overlay ?: return
        val show = !isIdleScreen()
        tv.post {
            tv.visibility = if (show) View.VISIBLE else View.GONE
            statusTv?.visibility = if (show) View.VISIBLE else View.GONE
        }
    }

    private fun updateOverlay() {
        val tv = overlay ?: return
        tv.post {
            tv.text = if (running) "⏸" else "▶"
            (tv.background as? GradientDrawable)?.setColor(
                Color.parseColor(if (running) "#CCC62828" else "#CC2E7D32")
            )
        }
    }
}
