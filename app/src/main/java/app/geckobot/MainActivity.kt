package app.geckobot

import android.app.Activity
import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.widget.*

class MainActivity : Activity() {
    private lateinit var status: TextView
    private lateinit var logView: TextView
    private lateinit var toggle: Button
    private val handler = Handler(Looper.getMainLooper())
    private val ticker = object : Runnable {
        override fun run() {
            refresh()
            handler.postDelayed(this, 700)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (16 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad * 2, pad, pad)
        }
        root.addView(TextView(this).apply {
            text = "壁虎自動過關 (Gecko Out)"
            textSize = 22f
            typeface = Typeface.DEFAULT_BOLD
        })
        status = TextView(this).apply { textSize = 15f; setPadding(0, pad / 2, 0, pad / 2) }
        root.addView(status)

        root.addView(Button(this).apply {
            text = "① 開啟無障礙設定（找到「壁虎自動過關」並開啟）"
            setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
        })
        toggle = Button(this).apply {
            text = "② 開始 / 暫停"
            setOnClickListener {
                val s = GeckoService.instance
                if (s == null) toast("請先開啟無障礙服務") else s.toggle()
            }
        }
        root.addView(toggle)

        root.addView(CheckBox(this).apply {
            text = "顯示畫面上的懸浮開始/暫停按鈕"
            isChecked = Prefs.overlay(this@MainActivity)
            setOnCheckedChangeListener { _, v ->
                Prefs.setOverlay(this@MainActivity, v)
                GeckoService.instance?.applyOverlay()
            }
        })
        root.addView(CheckBox(this).apply {
            text = "Ancient Climb 彈窗按 START 參加（預設是按 X 關閉）"
            isChecked = Prefs.joinEvents(this@MainActivity)
            setOnCheckedChangeListener { _, v -> Prefs.setJoinEvents(this@MainActivity, v) }
        })
        root.addView(TextView(this).apply {
            text = "用法：先把 Gecko Out 開到任何畫面（遊戲中、過關畫面、主畫面都行），按懸浮按鈕或這裡的「開始」。" +
                "App 會自動辨識棋盤、拖曳壁虎，過關後自動關閉 Island Quest / Ancient Climb 等彈窗並按 PLAY 進下一關。再按一次即暫停。"
            textSize = 13f
            setPadding(0, pad / 2, 0, pad / 2)
        })
        logView = TextView(this).apply { textSize = 12f; typeface = Typeface.MONOSPACE }
        root.addView(ScrollView(this).apply { addView(logView) },
            LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()

    private fun refresh() {
        val s = GeckoService.instance
        status.text = when {
            s == null -> "狀態：無障礙服務尚未開啟"
            s.running -> "狀態：執行中 ▶（步數 ${s.moves}，過關 ${s.levels}）"
            else -> "狀態：已就緒，暫停中 ⏸"
        }
        logView.text = GeckoService.logLines().joinToString("\n")
    }

    override fun onResume() { super.onResume(); handler.post(ticker) }
    override fun onPause() { super.onPause(); handler.removeCallbacks(ticker) }
}
