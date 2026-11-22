package top.niunaijun.blackboxa.scanner

import android.content.Context
import android.graphics.Color
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.LayerDrawable
import android.graphics.drawable.StateListDrawable
import android.view.Gravity

/** iOS 26「液態玻璃」風格的繪製元件：半透明底色 + 斜向反光 + 亮邊 + 頂部高光。 */
object Glass {
    private fun dpf(ctx: Context, v: Float) = v * ctx.resources.displayMetrics.density

    fun panel(ctx: Context, radiusDp: Float = 26f): Drawable {
        val r = dpf(ctx, radiusDp)
        val tint = GradientDrawable().apply { cornerRadius = r; setColor(0x73101218) }
        val sheen = GradientDrawable(
            GradientDrawable.Orientation.TL_BR, intArrayOf(0x3AFFFFFF, 0x0DFFFFFF, 0x26FFFFFF)
        ).apply { cornerRadius = r }
        val edge = GradientDrawable().apply {
            cornerRadius = r
            setColor(Color.TRANSPARENT)
            setStroke(dpf(ctx, 1f).toInt().coerceAtLeast(1), 0x73FFFFFF)
        }
        val top = GradientDrawable(
            GradientDrawable.Orientation.TOP_BOTTOM, intArrayOf(0x45FFFFFF, 0x00FFFFFF)
        ).apply { cornerRadii = floatArrayOf(r, r, r, r, 0f, 0f, 0f, 0f) }
        val ld = LayerDrawable(arrayOf(tint, sheen, edge, top))
        val one = dpf(ctx, 1f).toInt().coerceAtLeast(1)
        ld.setLayerInset(3, one, one, one, 0)
        ld.setLayerHeight(3, dpf(ctx, 64f).toInt())
        ld.setLayerGravity(3, Gravity.TOP)
        return ld
    }

    fun header(ctx: Context, radiusDp: Float = 26f): Drawable {
        val r = dpf(ctx, radiusDp)
        return GradientDrawable(
            GradientDrawable.Orientation.TOP_BOTTOM, intArrayOf(0x44FFFFFF, 0x14FFFFFF)
        ).apply { cornerRadii = floatArrayOf(r, r, r, r, 0f, 0f, 0f, 0f) }
    }

    private fun rect(ctx: Context, fill: Int, stroke: Int, radiusDp: Float) = GradientDrawable().apply {
        cornerRadius = dpf(ctx, radiusDp)
        setColor(fill)
        setStroke(dpf(ctx, 1f).toInt().coerceAtLeast(1), stroke)
    }

    fun button(ctx: Context): Drawable = StateListDrawable().apply {
        addState(intArrayOf(android.R.attr.state_pressed), rect(ctx, 0x66FFFFFF, 0xA6FFFFFF.toInt(), 14f))
        addState(intArrayOf(), rect(ctx, 0x26FFFFFF, 0x55FFFFFF, 14f))
    }

    fun field(ctx: Context, active: Boolean): Drawable =
        if (active) rect(ctx, 0x4DFFFFFF, 0xCCFFFFFF.toInt(), 12f) else rect(ctx, 0x1AFFFFFF, 0x38FFFFFF, 12f)
}
