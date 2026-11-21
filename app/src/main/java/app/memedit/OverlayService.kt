package app.memedit

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.PixelFormat
import android.os.Build
import android.os.IBinder
import android.view.ContextThemeWrapper
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/** 懸浮視窗：把 ScannerPanel 浮在畫面上，可拖曳、可收合。 */
class OverlayService : Service() {
    private lateinit var wm: WindowManager
    private var root: LinearLayout? = null
    private lateinit var lp: WindowManager.LayoutParams
    private var collapsed = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startFg()
        if (root == null) show()
        return START_NOT_STICKY
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun startFg() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(
            NotificationChannel("memedit", "改值工具", NotificationManager.IMPORTANCE_LOW)
        )
        val n = Notification.Builder(this, "memedit")
            .setSmallIcon(android.R.drawable.ic_menu_search)
            .setContentTitle("改值工具運作中")
            .setContentText("懸浮視窗已開啟")
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(1, n)
        }
    }

    private fun setFocusable(need: Boolean) {
        val r = root ?: return
        if (!need) {
            (getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager)
                .hideSoftInputFromWindow(r.windowToken, 0)
        }
        val f = lp.flags
        lp.flags = if (need) f and WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE.inv()
        else f or WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
        if (lp.flags != f) wm.updateViewLayout(r, lp)
    }

    private fun show() {
        wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val themed = ContextThemeWrapper(this, R.style.Theme_App)

        val panel = ScannerPanel(themed) { need -> setFocusable(need) }
        val body = ScrollView(themed)
        body.addView(panel)

        val title = TextView(themed).apply {
            text = "改值工具（拖曳移動）"
            textSize = 13f
            setTextColor(0xFFFFFFFF.toInt())
            setPadding(dp(8), dp(6), dp(8), dp(6))
        }
        val bMin = Button(themed).apply {
            text = "－"
            minWidth = 0
            minimumWidth = dp(40)
            minHeight = 0
            minimumHeight = dp(32)
        }
        val bClose = Button(themed).apply {
            text = "✕"
            minWidth = 0
            minimumWidth = dp(40)
            minHeight = 0
            minimumHeight = dp(32)
        }
        val header = LinearLayout(themed).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(0xFF3949AB.toInt())
            gravity = Gravity.CENTER_VERTICAL
            addView(title, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(bMin)
            addView(bClose)
        }

        val screenH = resources.displayMetrics.heightPixels
        val r = LinearLayout(themed).apply {
            orientation = LinearLayout.VERTICAL
            addView(header)
            addView(body, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, (screenH * 0.55).toInt()))
        }
        root = r

        lp = WindowManager.LayoutParams(
            dp(330),
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = dp(16)
            y = dp(80)
            softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_PAN
        }

        header.setOnTouchListener(object : View.OnTouchListener {
            var sx = 0
            var sy = 0
            var tx = 0f
            var ty = 0f
            override fun onTouch(v: View, e: MotionEvent): Boolean {
                when (e.action) {
                    MotionEvent.ACTION_DOWN -> {
                        sx = lp.x; sy = lp.y; tx = e.rawX; ty = e.rawY
                    }
                    MotionEvent.ACTION_MOVE -> {
                        lp.x = sx + (e.rawX - tx).toInt()
                        lp.y = sy + (e.rawY - ty).toInt()
                        wm.updateViewLayout(r, lp)
                    }
                }
                return true
            }
        })
        bMin.setOnClickListener {
            collapsed = !collapsed
            body.visibility = if (collapsed) View.GONE else View.VISIBLE
            title.text = if (collapsed) "改值工具" else "改值工具（拖曳移動）"
            lp.width = if (collapsed) WindowManager.LayoutParams.WRAP_CONTENT else dp(330)
            wm.updateViewLayout(r, lp)
        }
        bClose.setOnClickListener { stopSelf() }

        wm.addView(r, lp)
    }

    override fun onDestroy() {
        root?.let {
            try {
                wm.removeView(it)
            } catch (e: Exception) {
            }
        }
        root = null
        super.onDestroy()
    }
}
