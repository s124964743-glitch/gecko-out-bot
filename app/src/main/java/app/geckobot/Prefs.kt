package app.geckobot

import android.content.Context

object Prefs {
    private fun sp(c: Context) = c.getSharedPreferences("gecko", Context.MODE_PRIVATE)
    fun joinEvents(c: Context) = sp(c).getBoolean("join_events", false)
    fun setJoinEvents(c: Context, v: Boolean) = sp(c).edit().putBoolean("join_events", v).apply()
    fun overlay(c: Context) = sp(c).getBoolean("overlay", true)
    fun setOverlay(c: Context, v: Boolean) = sp(c).edit().putBoolean("overlay", v).apply()
    fun gamePkg(c: Context): String? = sp(c).getString("game_pkg", null)
    fun setGamePkg(c: Context, v: String?) = sp(c).edit().putString("game_pkg", v).apply()
}
