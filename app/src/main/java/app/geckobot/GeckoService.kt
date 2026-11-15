package app.geckobot

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
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
                while (buf.size > 80) buf.removeFirst()
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
    private var unknownSaved = 0

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

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}

    override fun onDestroy() {
        running = false
        removeOverlay()
        instance = null
        super.onDestroy()
    }

    // ------------------------------------------------------------ 開關
    fun toggle() {
        if (running) stop() else start()
    }

    private fun start() {
        if (running) return
        running = true
        updateOverlay()
        worker = Thread { loop() }.also { it.isDaemon = true; it.start() }
        log("開始")
    }

    private fun stop() {
        running = false
        updateOverlay()
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
        while (running) {
            try {
                val bmp = screenshot(rawFile)
                if (bmp == null) {
                    fails++
                    if (fails % 10 == 1) log("截圖失敗(${fails})，稍後重試")
                    Thread.sleep(600)
                    continue
                }
                fails = 0
                val res = brain.callAttr("step", rawFile.absolutePath, bmp.width, bmp.height).toString()
                val j = JSONObject(res)
                val msg = j.optString("msg")
                if (msg.isNotEmpty()) log(msg)
                if (j.optBoolean("unknown")) saveUnknown(bmp)
                bmp.recycle()
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
            x = (8 * d).toInt()
            y = (150 * d).toInt()
        }
        var downX = 0f; var downY = 0f; var startX = 0; var startY = 0; var moved = false
        tv.setOnTouchListener { _: View, e: MotionEvent ->
            when (e.action) {
                MotionEvent.ACTION_DOWN -> { downX = e.rawX; downY = e.rawY; startX = lp.x; startY = lp.y; moved = false }
                MotionEvent.ACTION_MOVE -> {
                    val dx = e.rawX - downX; val dy = e.rawY - downY
                    if (Math.abs(dx) > 12 || Math.abs(dy) > 12) moved = true
                    if (moved) { lp.x = startX + dx.toInt(); lp.y = startY + dy.toInt(); wm.updateViewLayout(tv, lp) }
                }
                MotionEvent.ACTION_UP -> if (!moved) toggle()
            }
            true
        }
        try {
            wm.addView(tv, lp)
            overlay = tv; overlayParams = lp
            updateOverlay()
        } catch (e: Throwable) {
            log("懸浮按鈕建立失敗: $e")
        }
    }

    private fun removeOverlay() {
        val tv = overlay ?: return
        try { (getSystemService(WINDOW_SERVICE) as WindowManager).removeView(tv) } catch (_: Throwable) {}
        overlay = null
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
