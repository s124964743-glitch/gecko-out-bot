package top.niunaijun.blackboxa.scanner

import android.content.Context
import android.graphics.Typeface
import android.text.InputType
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView

/**
 * 搜尋/修改面板。可嵌在 Activity，也可放進懸浮視窗（focusHook 用來讓懸浮視窗切換可取得焦點）。
 */
class ScannerPanel(
    ctx: Context,
    private val focusHook: ((Boolean) -> Unit)? = null
) : LinearLayout(ctx) {

    private var mode = 0 // 0 精確 1 範圍 2 未知初始值
    private val modeNames = arrayOf("精確值", "範圍", "未知初始值")
    private var busy = false
    private var note: String? = null

    private val btnType: Button
    private val btnMode: Button
    private val etV: EditText
    private val etV2: EditText
    private val etNew: EditText
    private val tvStatus: TextView
    private val list: LinearLayout

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun hideKb() {
        (context.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager)
            .hideSoftInputFromWindow(windowToken, 0)
        focusHook?.invoke(false)
        clearFocus()
    }

    private fun btn(text: String, onClick: () -> Unit) = Button(context).apply {
        this.text = text
        textSize = 12f
        isAllCaps = false
        minWidth = 0
        minimumWidth = 0
        minHeight = 0
        minimumHeight = dp(36)
        setPadding(dp(6), dp(2), dp(6), dp(2))
        setOnClickListener {
            hideKb()
            onClick()
        }
    }

    /** 輸入框不叫出系統鍵盤（會搶走遊戲焦點、讓遊戲無法操作），改用面板內建的數字鍵。 */
    private fun edit(hint: String) = EditText(context).apply {
        this.hint = hint
        textSize = 13f
        setSingleLine()
        isFocusable = false
        isFocusableInTouchMode = false
        isCursorVisible = false
        setOnClickListener { setActive(this) }
    }

    private var active: EditText? = null

    private fun setActive(e: EditText) {
        active = e
        for (x in listOf(etV, etV2, etNew)) {
            x.setBackgroundColor(if (x === e) 0x663949AB else 0x22FFFFFF)
        }
    }

    private fun press(c: String) {
        val e = active ?: etV
        e.setText(e.text.toString() + c)
    }

    private fun keypad(): View {
        val c = LinearLayout(context)
        c.orientation = VERTICAL
        c.addView(row(*"1234567".map { ch -> btn(ch.toString()) { press(ch.toString()) } }.toTypedArray()))
        c.addView(row(
            btn("8") { press("8") }, btn("9") { press("9") }, btn("0") { press("0") },
            btn(".") { press(".") }, btn("-") { press("-") },
            btn("⌫") {
                val e = active ?: etV
                val t = e.text.toString()
                if (t.isNotEmpty()) e.setText(t.dropLast(1))
            },
            btn("C") { (active ?: etV).setText("") }
        ))
        return c
    }

    private fun row(vararg views: View): LinearLayout {
        val r = LinearLayout(context)
        r.orientation = HORIZONTAL
        for (v in views) r.addView(v, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        return r
    }

    init {
        orientation = VERTICAL
        setPadding(dp(8), dp(8), dp(8), dp(8))
        setBackgroundColor(0xF0202124.toInt())

        btnType = btn(MemEngine.type.label) { cycleType() }
        btnMode = btn(modeNames[0]) { cycleMode() }
        etV = edit("數值 / 範圍下限")
        etV2 = edit("範圍上限")
        etNew = edit("要改成的新值")
        tvStatus = TextView(context).apply {
            textSize = 12f
            setTextColor(0xFFB0BEC5.toInt())
            setPadding(0, dp(4), 0, dp(4))
        }
        list = LinearLayout(context).apply { orientation = VERTICAL }

        addView(row(btnType, btnMode))
        addView(row(etV, etV2))
        addView(keypad())
        setActive(etV)
        addView(row(btn("新搜尋") { newScan() }, btn("清除結果") { clearAll() }))
        addView(row(btn("再搜:=值") { refine(Op.MATCH, false) }, btn("再搜:範圍") { refine(Op.MATCH, true) }))
        addView(row(
            btn("變大") { refine(Op.INC, false) },
            btn("變小") { refine(Op.DEC, false) },
            btn("有變") { refine(Op.CHANGED, false) },
            btn("不變") { refine(Op.SAME, false) }
        ))
        addView(tvStatus)
        addView(etNew)
        addView(row(
            btn("全改") { writeAll(false) },
            btn("全鎖") { writeAll(true) },
            btn("解全鎖") { unlockAll() },
            btn("刷新") { refreshList() }
        ))
        addView(row(
            btn("自動縮小") { autoShrink() },
            btn("探測計時器+秒") { probe() }
        ))
        addView(row(btn("一鍵找計時器（填目前秒數範圍）") { findTimer() }))
        addView(row(btn("復原上一步") {
            if (MemEngine.undo()) refreshList() else status("沒有可復原的步驟")
        }))
        addView(row(btn("引擎自檢（驗證本進程可讀寫）") { runBg("自檢中") { note = MemEngine.selfTest() } }))
        addView(list)
        refreshList()
    }

    private fun status(s: String) {
        tvStatus.text = s
    }

    private fun cycleType() {
        MemEngine.type = MemEngine.type.next()
        MemEngine.clear()
        btnType.text = MemEngine.type.label
        refreshList()
    }

    private fun cycleMode() {
        mode = (mode + 1) % 3
        btnMode.text = modeNames[mode]
    }

    private fun clearAll() {
        MemEngine.clear()
        refreshList()
    }

    private fun runBg(label: String, work: () -> Unit) {
        if (busy) return
        busy = true
        status("$label…")
        Thread {
            try {
                work()
            } catch (e: Throwable) {
                post { status("錯誤：${e.message}") }
            }
            post {
                busy = false
                refreshList()
                note?.let {
                    status(it)
                    note = null
                }
            }
        }.start()
    }

    private fun newScan() {
        val t = MemEngine.type
        val m: Matcher? = when (mode) {
            0 -> t.exact(etV.text.toString())
            1 -> t.range(etV.text.toString(), etV2.text.toString())
            else -> Matcher { true }
        }
        if (m == null) {
            status("數值格式不正確")
            return
        }
        runBg("搜尋中") {
            MemEngine.firstScan(t, m) { p -> post { if (busy) status("搜尋中 $p%") } }
        }
    }

    private fun refine(op: Op, asRange: Boolean) {
        if (MemEngine.count == 0) {
            status("目前沒有結果，請先「新搜尋」")
            return
        }
        val t = MemEngine.type
        var m: Matcher? = null
        if (op == Op.MATCH) {
            m = if (asRange) t.range(etV.text.toString(), etV2.text.toString())
            else t.exact(etV.text.toString())
            if (m == null) {
                status("數值格式不正確")
                return
            }
        }
        runBg("篩選中") {
            val before = MemEngine.count
            val n = MemEngine.refine(op, m)
            if (n == 0 && MemEngine.undo()) {
                note = "篩選後 0 筆，已自動復原上一組（$before 筆）。請換個條件再搜"
            }
        }
    }

    private fun autoShrink() {
        if (MemEngine.count == 0) {
            status("請先「新搜尋」")
            return
        }
        runBg("自動縮小中（請讓計時保持進行）") {
            val n = MemEngine.autoShrink(6, 1200) { i, c -> post { status("自動縮小 $i/6：剩 $c 筆") } }
            note = "自動縮小完成：剩 $n 筆"
        }
    }

    private fun probe() {
        val d = etNew.text.toString().toDoubleOrNull() ?: 60.0
        runBg("探測中") { note = MemEngine.probeTimer(d) }
    }

    private fun findTimer() {
        val t = MemEngine.type
        if (!t.isFloat) {
            status("請先把類型切成「浮點4B」")
            return
        }
        val m = t.range(etV.text.toString(), etV2.text.toString())
        if (m == null) {
            status("請在「數值」填目前畫面秒數的下限、「範圍上限」填上限（例如 57 與 59）")
            return
        }
        val delta = etNew.text.toString().toDoubleOrNull() ?: 60.0
        runBg("一鍵尋找計時器") {
            MemEngine.firstScan(t, m) { p -> post { if (busy) status("掃描 $p%") } }
            if (MemEngine.count == 0) {
                note = "範圍內沒有任何值，請確認秒數範圍與類型"
                return@runBg
            }
            val n = MemEngine.autoShrink(6, 1200) { i, c -> post { status("自動縮小 $i/6：剩 $c 筆") } }
            note = if (n in 1..60) MemEngine.probeTimer(delta) else "縮小後還有 $n 筆，請再按「自動縮小」"
        }
    }

    private fun writeAll(freeze: Boolean) {
        val t = MemEngine.type
        val bits = t.parse(etNew.text.toString())
        if (bits == null) {
            status("新值格式不正確")
            return
        }
        val n = MemEngine.count
        if (n == 0) {
            status("沒有結果可修改")
            return
        }
        if (n > 500) {
            status("結果有 $n 筆，超過 500 筆，請先再搜尋縮小範圍")
            return
        }
        var ok = 0
        for (i in 0 until n) {
            val a = MemEngine.addrs[i]
            if (MemEngine.write(a, t, bits)) ok++
            if (freeze) MemEngine.setFrozen(a, t, bits, true)
        }
        refreshList()
        status((if (freeze) "已鎖定 " else "已修改 ") + "$ok / $n 筆")
    }

    private fun unlockAll() {
        MemEngine.frozen.clear()
        refreshList()
    }

    private fun refreshList() {
        val t = MemEngine.type
        list.removeAllViews()
        val n = MemEngine.count
        status(
            "類型：${t.label}　結果：$n 筆" +
                (if (MemEngine.truncated) "（已達上限 ${MemEngine.CAP}）" else "") +
                (if (MemEngine.frozen.isNotEmpty()) "　鎖定 ${MemEngine.frozen.size}" else "") +
                (if (n > 100) "\n只顯示前 100 筆，請再搜尋縮小" else "")
        )
        val show = minOf(n, 100)
        for (i in 0 until show) {
            val a = MemEngine.addrs[i]
            val cur = MemEngine.peek(a, t)
            val locked = MemEngine.frozen.containsKey(a)
            val tv = TextView(context).apply {
                typeface = Typeface.MONOSPACE
                textSize = 11f
                setTextColor(0xFFFFFFFF.toInt())
                text = "%x\n= %s".format(a, if (cur == null) "(不可讀)" else t.show(cur))
            }
            val bSet = btn("改") {
                val bits = t.parse(etNew.text.toString())
                if (bits == null) {
                    status("新值格式不正確")
                } else {
                    MemEngine.write(a, t, bits)
                    refreshList()
                }
            }
            val bLock = btn(if (locked) "解鎖" else "鎖定") {
                if (locked) {
                    MemEngine.setFrozen(a, t, 0, false)
                } else {
                    val bits = t.parse(etNew.text.toString())
                    if (bits == null) {
                        status("請先填新值")
                        return@btn
                    }
                    MemEngine.write(a, t, bits)
                    MemEngine.setFrozen(a, t, bits, true)
                }
                refreshList()
            }
            val r = LinearLayout(context)
            r.orientation = HORIZONTAL
            r.addView(tv, LayoutParams(0, LayoutParams.WRAP_CONTENT, 2f))
            r.addView(bSet, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
            r.addView(bLock, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
            list.addView(r)
        }
    }
}
