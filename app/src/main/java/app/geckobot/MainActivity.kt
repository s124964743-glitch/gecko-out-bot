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
    private lateinit var gameBtn: Button
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

        gameBtn = Button(this).apply {
            setOnClickListener { pickGame() }
        }
        root.addView(gameBtn)
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

        root.addView(Button(this).apply {
            text = "複製紀錄（出問題時貼給 Claude）"
            setOnClickListener {
                val cm = getSystemService(CLIPBOARD_SERVICE) as android.content.ClipboardManager
                cm.setPrimaryClip(android.content.ClipData.newPlainText("gecko-log", GeckoService.logLines().reversed().joinToString("\n")))
                toast("已複製 ${GeckoService.logLines().size} 行紀錄")
            }
        })
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
            text = "用法：先在最上方選好遊戲，再切到 Gecko Out，畫面下方會出現懸浮按鈕（只在該遊戲裡顯示，主畫面與其他 App 不會出現）；按它即開始/暫停。" +
                "App 會自動辨識棋盤、拖曳壁虎，過關後自動關閉 Island Quest / Ancient Climb 等彈窗並按 PLAY 進下一關。再按一次即暫停。"
            textSize = 13f
            setPadding(0, pad / 2, 0, pad / 2)
        })
        logView = TextView(this).apply { textSize = 12f; typeface = Typeface.MONOSPACE }
        root.addView(ScrollView(this).apply { addView(logView) },
            LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)
    }

    private fun pickGame() {
        val pm = packageManager
        val apps = pm.queryIntentActivities(
            Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0
        ).filter { it.activityInfo.packageName != packageName }
            .map { it.loadLabel(pm).toString() to it.activityInfo.packageName }
            .distinctBy { it.second }.sortedBy { it.first.lowercase() }
        android.app.AlertDialog.Builder(this)
            .setTitle("選擇要自動操作的遊戲")
            .setItems(apps.map { it.first }.toTypedArray()) { _, i ->
                Prefs.setGamePkg(this, apps[i].second); refresh()
            }.show()
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()

    private fun refresh() {
        val s = GeckoService.instance
        status.text = when {
            s == null -> "狀態：無障礙服務尚未開啟"
            s.running -> "狀態：執行中 ▶（步數 ${s.moves}，過關 ${s.levels}）"
            else -> "狀態：已就緒，暫停中 ⏸"
        }
        val g = s?.gamePkg() ?: Prefs.gamePkg(this)
        gameBtn.text = "目標遊戲：" + (g?.let { pk ->
            try { packageManager.getApplicationLabel(packageManager.getApplicationInfo(pk, 0)).toString() } catch (_: Throwable) { pk }
        } ?: "未選擇（點此選 Gecko Out）")
        logView.text = GeckoService.logLines().joinToString("\n")
    }

    override fun onResume() { super.onResume(); handler.post(ticker) }
    override fun onPause() { super.onPause(); handler.removeCallbacks(ticker) }
}
