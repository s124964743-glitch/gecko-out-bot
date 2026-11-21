package top.niunaijun.blackboxa.scanner

import android.app.Activity
import android.view.ContextThemeWrapper
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import kotlin.math.abs

/**
 * 掛在遊戲 Activity 畫面上的搜改視窗：平時是一顆可拖曳的「改」按鈕，點開變成完整面板。
 * 因為這個 View 活在「遊戲所在的進程」裡，MemEngine 讀到的就是遊戲的記憶體。
 */
class ScannerOverlay(activity: Activity) :
    FrameLayout(ContextThemeWrapper(activity, android.R.style.Theme_Material)) {

    companion object {
        const val TAG_ROOT = "memedit_root"
        private var posX = 0f
        private var posY = 0f
        private var expanded = false
    }

    private val bubble: Button
    private val box: LinearLayout

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun draggable(handle: View, onTap: (() -> Unit)?) {
        handle.setOnTouchListener(object : OnTouchListener {
            var dx = 0f
            var dy = 0f
            var moved = false
            override fun onTouch(v: View, e: MotionEvent): Boolean {
                when (e.actionMasked) {
                    MotionEvent.ACTION_DOWN -> {
                        dx = e.rawX - translationX
                        dy = e.rawY - translationY
                        moved = false
                    }
                    MotionEvent.ACTION_MOVE -> {
                        val nx = e.rawX - dx
                        val ny = e.rawY - dy
                        if (abs(nx - translationX) > dp(3) || abs(ny - translationY) > dp(3)) moved = true
                        if (moved) {
                            translationX = nx
                            translationY = ny
                            posX = nx
                            posY = ny
                        }
                    }
                    MotionEvent.ACTION_UP -> if (!moved) onTap?.invoke()
                }
                return true
            }
        })
    }

    private fun setExpanded(on: Boolean) {
        expanded = on
        bubble.visibility = if (on) GONE else VISIBLE
        box.visibility = if (on) VISIBLE else GONE
    }

    init {
        tag = TAG_ROOT
        translationX = posX
        translationY = posY

        bubble = Button(context).apply {
            text = "改"
            textSize = 14f
            minWidth = 0
            minimumWidth = 0
            minHeight = 0
            minimumHeight = 0
            setPadding(dp(10), dp(6), dp(10), dp(6))
            setBackgroundColor(0xCC3949AB.toInt())
            setTextColor(0xFFFFFFFF.toInt())
        }
        draggable(bubble) { setExpanded(true) }
        addView(bubble, LayoutParams(dp(44), dp(44)))

        val panel = ScannerPanel(context)
        val scroll = ScrollView(context)
        scroll.addView(panel)

        val title = TextView(context).apply {
            text = "改值工具（拖曳移動）"
            textSize = 13f
            setTextColor(0xFFFFFFFF.toInt())
            setPadding(dp(8), dp(6), dp(8), dp(6))
        }
        val bMin = Button(context).apply {
            text = "－"
            minWidth = 0
            minimumWidth = dp(44)
            minHeight = 0
            minimumHeight = dp(32)
            setOnClickListener { setExpanded(false) }
        }
        val header = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(0xFF3949AB.toInt())
            addView(title, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(bMin)
        }
        draggable(title, null)

        val dm = resources.displayMetrics
        val w = minOf(dp(330), dm.widthPixels - dp(16))
        val h = (dm.heightPixels * 0.6).toInt()
        box = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            addView(header)
            addView(scroll, LinearLayout.LayoutParams(w, h))
        }
        addView(box, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT))
        setExpanded(expanded)
    }

    /** 掛到 Activity 的內容區上方。 */
    fun attachTo(activity: Activity) {
        val decor = activity.window?.decorView as? ViewGroup ?: return
        val content = decor.findViewById<ViewGroup>(android.R.id.content) ?: decor
        val lp = if (content is FrameLayout) {
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.START
            )
        } else {
            ViewGroup.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }
        if (lp is FrameLayout.LayoutParams) {
            lp.leftMargin = dp(8)
            lp.topMargin = dp(80)
        }
        content.addView(this, lp)
        bringToFront()
    }
}
