package app.memedit

import android.app.Activity
import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

class MainActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var tvTarget: TextView
    private lateinit var tvSelf: TextView

    private val ticker = object : Runnable {
        override fun run() {
            val b = TestTarget.buf
            tvTarget.text = "HP（整數4B）　　= ${b.getInt(0)}\n" +
                "金幣（浮點4B）　= ${b.getFloat(8)}\n" +
                "經驗（整數8B）　= ${b.getLong(16)}\n" +
                "速度（雙精度）　= ${b.getDouble(24)}"
            ui.postDelayed(this, 300)
        }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun btn(text: String, onClick: () -> Unit) = Button(this).apply {
        this.text = text
        textSize = 12f
        isAllCaps = false
        minWidth = 0
        minimumWidth = 0
        setOnClickListener { onClick() }
    }

    private fun row(vararg v: View): LinearLayout {
        val r = LinearLayout(this)
        r.orientation = LinearLayout.HORIZONTAL
        for (x in v) r.addView(x, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        return r
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val col = LinearLayout(this)
        col.orientation = LinearLayout.VERTICAL
        col.setPadding(dp(10), dp(10), dp(10), dp(10))

        col.addView(TextView(this).apply {
            text = "改值工具（免 root）"
            textSize = 20f
        })
        col.addView(TextView(this).apply {
            text = "免 root 只能讀寫「本 App 自己」的記憶體。下方測試目標用來驗證搜尋/修改是否正常。"
            textSize = 12f
        })

        tvTarget = TextView(this).apply {
            typeface = Typeface.MONOSPACE
            textSize = 14f
            setPadding(0, dp(8), 0, dp(8))
        }
        col.addView(tvTarget)

        val b = TestTarget.buf
        col.addView(row(
            btn("受傷 HP-7") { b.putInt(0, b.getInt(0) - 7) },
            btn("賺錢 +12.5") { b.putFloat(8, b.getFloat(8) + 12.5f) },
            btn("經驗 +1000") { b.putLong(16, b.getLong(16) + 1000) },
            btn("速度×1.1") { b.putDouble(24, b.getDouble(24) * 1.1) }
        ))

        tvSelf = TextView(this).apply {
            textSize = 12f
            setPadding(0, dp(6), 0, dp(6))
        }
        col.addView(row(
            btn("開啟懸浮視窗") { openOverlay() },
            btn("引擎自檢") { selfTest() }
        ))
        col.addView(tvSelf)
        col.addView(ScannerPanel(this))

        setContentView(ScrollView(this).apply { addView(col) })
    }

    private fun openOverlay() {
        if (!Settings.canDrawOverlays(this)) {
            Toast.makeText(this, "請先允許「顯示在其他應用程式上層」", Toast.LENGTH_LONG).show()
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
            return
        }
        startForegroundService(Intent(this, OverlayService::class.java))
    }

    private fun selfTest() {
        tvSelf.text = "自檢中…"
        Thread {
            val s = try {
                MemEngine.selfTest()
            } catch (e: Throwable) {
                "❌ 自檢失敗：${e.javaClass.simpleName} ${e.message}"
            }
            ui.post { tvSelf.text = s }
        }.start()
    }

    override fun onResume() {
        super.onResume()
        ui.post(ticker)
    }

    override fun onPause() {
        ui.removeCallbacks(ticker)
        super.onPause()
    }
}
