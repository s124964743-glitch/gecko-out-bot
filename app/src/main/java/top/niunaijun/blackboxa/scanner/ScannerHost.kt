package top.niunaijun.blackboxa.scanner

import android.app.Activity
import android.app.Application
import android.os.Bundle
import android.util.Log
import android.view.ViewGroup

/** 在「遊戲進程」啟動後註冊，讓每個遊戲 Activity 都掛上搜改視窗。 */
object ScannerHost {
    private const val TAG = "ScannerHost"
    private var inited = false

    @Synchronized
    fun init(app: Application) {
        if (inited) return
        inited = true
        app.registerActivityLifecycleCallbacks(object : Application.ActivityLifecycleCallbacks {
            override fun onActivityResumed(activity: Activity) {
                try {
                    attach(activity)
                } catch (e: Throwable) {
                    Log.e(TAG, "attach failed", e)
                }
            }

            override fun onActivityCreated(a: Activity, s: Bundle?) {}
            override fun onActivityStarted(a: Activity) {}
            override fun onActivityPaused(a: Activity) {}
            override fun onActivityStopped(a: Activity) {}
            override fun onActivitySaveInstanceState(a: Activity, s: Bundle) {}
            override fun onActivityDestroyed(a: Activity) {}
        })
        Log.d(TAG, "scanner host registered in ${Application.getProcessName()}")
    }

    private fun attach(activity: Activity) {
        val decor = activity.window?.decorView as? ViewGroup ?: return
        if (decor.findViewWithTag<android.view.View>(ScannerOverlay.TAG_ROOT) != null) return
        ScannerOverlay(activity).attachTo(activity)
    }
}
