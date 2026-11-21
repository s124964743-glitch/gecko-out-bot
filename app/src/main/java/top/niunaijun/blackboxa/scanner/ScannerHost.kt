package top.niunaijun.blackboxa.scanner

import android.app.Activity
import android.app.Application
import android.os.Bundle
import android.util.Log

/** 在「遊戲進程」啟動後註冊，讓每個遊戲 Activity 都掛上搜改視窗。 */
object ScannerHost {
    private const val TAG = "ScannerHost"
    private var inited = false
    private val windows = HashMap<Activity, ScannerWindow>()

    @Synchronized
    fun init(app: Application) {
        if (inited) return
        inited = true
        app.registerActivityLifecycleCallbacks(object : Application.ActivityLifecycleCallbacks {
            override fun onActivityResumed(activity: Activity) {
                val decor = activity.window?.decorView ?: return
                decor.post {
                    try {
                        if (!activity.isFinishing && !activity.isDestroyed) {
                            windows.getOrPut(activity) { ScannerWindow(activity) }.show()
                        }
                    } catch (e: Throwable) {
                        Log.e(TAG, "show failed", e)
                    }
                }
            }

            override fun onActivityDestroyed(activity: Activity) {
                windows.remove(activity)?.dismiss()
            }

            override fun onActivityCreated(a: Activity, s: Bundle?) {}
            override fun onActivityStarted(a: Activity) {}
            override fun onActivityPaused(a: Activity) {}
            override fun onActivityStopped(a: Activity) {}
            override fun onActivitySaveInstanceState(a: Activity, s: Bundle) {}
        })
        Log.d(TAG, "scanner host registered")
    }
}
