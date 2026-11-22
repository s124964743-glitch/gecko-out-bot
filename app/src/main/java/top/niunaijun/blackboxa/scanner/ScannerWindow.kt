package top.niunaijun.blackboxa.scanner

import android.app.Activity
import android.content.Context
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.LayerDrawable
import android.util.Log
import android.view.ContextThemeWrapper
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewOutlineProvider
import android.view.WindowManager
import android.os.Build
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * 以「獨立子視窗」掛在遊戲 Activity 上的搜改視窗。
 * 它有自己的輸入通道：視窗內的觸控不會傳給遊戲，視窗外的觸控照常交給遊戲，
 * 也不會被遊戲自己的畫面（SurfaceView）重繪或裁切。
 */
class ScannerWindow(private val activity: Activity) {

    companion object {
        private const val TAG = "ScannerWindow"
        private var placed = false
        private var posX = 0
        private var posY = 0
        private var expanded = false
    }

    private val ctx = ContextThemeWrapper(activity, android.R.style.Theme_Material)
    private val wm: WindowManager = activity.windowManager
    private val root = FrameLayout(ctx)
    private val bubble: View
    private val box: LinearLayout
    private var shown = false

    private val lp = WindowManager.LayoutParams(
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.TYPE_APPLICATION_PANEL,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
            WindowManager.LayoutParams.FLAG_BLUR_BEHIND,
        PixelFormat.TRANSLUCENT
    ).apply {
        gravity = Gravity.TOP or Gravity.START
        softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_PAN
    }

    private fun dm() = activity.resources.displayMetrics
    private fun dp(v: Int) = (v * dm().density).toInt()

    private fun clamp() {
        val w = if (root.width > 0) root.width else dp(52)
        val h = if (root.height > 0) root.height else dp(52)
        lp.x = lp.x.coerceIn(0, maxOf(0, dm().widthPixels - w))
        lp.y = lp.y.coerceIn(0, maxOf(0, dm().heightPixels - h))
        posX = lp.x
        posY = lp.y
    }

    private fun update() {
        if (!shown) return
        try {
            wm.updateViewLayout(root, lp)
        } catch (e: Throwable) {
            Log.e(TAG, "update failed", e)
        }
    }

    private fun setFocusable(need: Boolean) {
        if (!shown) return
        if (!need) {
            (ctx.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager)
                .hideSoftInputFromWindow(root.windowToken, 0)
        }
        val f = lp.flags
        lp.flags = if (need) f and WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE.inv()
        else f or WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
        if (lp.flags != f) update()
    }

    private fun draggable(handle: View, onTap: (() -> Unit)?, snap: Boolean = false) {
        handle.setOnTouchListener(object : View.OnTouchListener {
            var sx = 0
            var sy = 0
            var tx = 0f
            var ty = 0f
            var moved = false
            override fun onTouch(v: View, e: MotionEvent): Boolean {
                when (e.actionMasked) {
                    MotionEvent.ACTION_DOWN -> {
                        sx = lp.x; sy = lp.y; tx = e.rawX; ty = e.rawY; moved = false
                    }
                    MotionEvent.ACTION_MOVE -> {
                        val dx = (e.rawX - tx).toInt()
                        val dy = (e.rawY - ty).toInt()
                        if (Math.abs(dx) > dp(3) || Math.abs(dy) > dp(3)) moved = true
                        if (moved) {
                            lp.x = sx + dx
                            lp.y = sy + dy
                            clamp()
                            update()
                        }
                    }
                    MotionEvent.ACTION_UP -> if (!moved) {
                        onTap?.invoke()
                    } else if (snap) {
                        // 像 iPhone 小白點：放開後吸附到左右邊緣
                        val w = if (root.width > 0) root.width else dp(52)
                        lp.x = if (lp.x + w / 2 < dm().widthPixels / 2) 0 else dm().widthPixels - w
                        clamp()
                        update()
                    }
                }
                return true
            }
        })
    }

    /** iPhone「輔助觸控」小白點樣式：深灰半透明圓角底 + 三層同心圓。 */
    private fun assistiveDot(): LayerDrawable {
        fun oval(color: Int) = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(color)
        }
        val base = GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = dp(15).toFloat()
            setColor(0x993A3A3C.toInt())
        }
        val ld = LayerDrawable(arrayOf(base, oval(0xB3FFFFFF.toInt()), oval(0xF03A3A3C.toInt()), oval(0xE6FFFFFF.toInt())))
        ld.setLayerInset(1, dp(9), dp(9), dp(9), dp(9))
        ld.setLayerInset(2, dp(14), dp(14), dp(14), dp(14))
        ld.setLayerInset(3, dp(18), dp(18), dp(18), dp(18))
        return ld
    }

    private fun setExpanded(on: Boolean) {
        expanded = on
        bubble.visibility = if (on) View.GONE else View.VISIBLE
        box.visibility = if (on) View.VISIBLE else View.GONE
        if (Build.VERSION.SDK_INT >= 31) lp.blurBehindRadius = if (on) dp(30) else 0
        root.post {
            clamp()
            update()
        }
    }

    init {
        bubble = View(ctx).apply {
            background = assistiveDot()
            alpha = 0.9f
        }
        draggable(bubble, { setExpanded(true) }, snap = true)
        root.addView(bubble, FrameLayout.LayoutParams(dp(52), dp(52)))

        val panel = ScannerPanel(ctx) { need -> setFocusable(need) }
        panel.background = null
        val scroll = ScrollView(ctx)
        scroll.addView(panel)

        val title = TextView(ctx).apply {
            text = "改值工具（拖曳移動）"
            textSize = 13f
            setTextColor(0xFFFFFFFF.toInt())
            setPadding(dp(8), dp(6), dp(8), dp(6))
        }
        val bMin = Button(ctx).apply {
            text = "－"
            minWidth = 0
            minimumWidth = dp(44)
            minHeight = 0
            minimumHeight = dp(32)
            background = Glass.button(ctx)
            setTextColor(0xFFFFFFFF.toInt())
            stateListAnimator = null
            setOnClickListener {
                setFocusable(false)
                setExpanded(false)
            }
        }
        val header = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = Glass.header(ctx)
            addView(title, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(bMin)
        }
        draggable(title, null)

        val w = minOf(dp(330), dm().widthPixels - dp(16))
        val h = (dm().heightPixels * 0.6).toInt()
        box = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            background = Glass.panel(ctx)
            clipToOutline = true
            outlineProvider = ViewOutlineProvider.BACKGROUND
            addView(header)
            addView(scroll, LinearLayout.LayoutParams(w, h))
        }
        root.addView(box, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT
        ))
        bubble.visibility = if (expanded) View.GONE else View.VISIBLE
        box.visibility = if (expanded) View.VISIBLE else View.GONE
    }

    fun show() {
        if (shown) return
        if (!placed) {
            posX = dp(8)
            posY = dp(80)
            placed = true
        }
        lp.x = posX
        lp.y = posY
        if (Build.VERSION.SDK_INT >= 31) lp.blurBehindRadius = if (expanded) dp(30) else 0
        try {
            wm.addView(root, lp)
            shown = true
            root.post {
                clamp()
                update()
            }
        } catch (e: Throwable) {
            Log.e(TAG, "addView failed", e)
        }
    }

    fun dismiss() {
        if (!shown) return
        shown = false
        try {
            wm.removeViewImmediate(root)
        } catch (e: Throwable) {
            Log.e(TAG, "removeView failed", e)
        }
    }
}
