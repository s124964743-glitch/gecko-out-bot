package app.memedit

import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.abs

/** 數值類型。所有值在引擎內都以 Long「位元」表示（4 位元組者帶正負號擴展）。 */
enum class VType(val label: String, val size: Int) {
    I32("整數4B", 4), I64("整數8B", 8), F32("浮點4B", 4), F64("雙精度8B", 8);

    val isFloat: Boolean get() = this == F32 || this == F64

    fun parse(text: String): Long? = try {
        val s = text.trim()
        when (this) {
            I32 -> s.toLong().toInt().toLong()
            I64 -> s.toLong()
            F32 -> java.lang.Float.floatToRawIntBits(s.toFloat()).toLong()
            F64 -> java.lang.Double.doubleToRawLongBits(s.toDouble())
        }
    } catch (e: NumberFormatException) {
        null
    }

    fun num(bits: Long): Double = when (this) {
        I32 -> bits.toInt().toDouble()
        I64 -> bits.toDouble()
        F32 -> java.lang.Float.intBitsToFloat(bits.toInt()).toDouble()
        F64 -> java.lang.Double.longBitsToDouble(bits)
    }

    fun show(bits: Long): String = when (this) {
        I32 -> bits.toInt().toString()
        I64 -> bits.toString()
        else -> {
            val d = num(bits)
            if (d.isNaN() || d.isInfinite()) d.toString()
            else String.format(Locale.US, "%.4f", d).trimEnd('0').trimEnd('.')
        }
    }

    fun next(): VType = values()[(ordinal + 1) % values().size]
}

fun interface Matcher {
    fun test(bits: Long): Boolean
}

enum class Op { MATCH, INC, DEC, CHANGED, SAME }

fun VType.exact(text: String): Matcher? {
    val v = parse(text) ?: return null
    if (!isFloat) return Matcher { it == v }
    val d = num(v)
    return Matcher { abs(num(it) - d) <= 0.001 }
}

fun VType.range(a: String, b: String): Matcher? {
    if (!isFloat) {
        val x = a.trim().toLongOrNull() ?: return null
        val y = b.trim().toLongOrNull() ?: x
        val lo = minOf(x, y)
        val hi = maxOf(x, y)
        return Matcher { it in lo..hi }
    }
    val x = a.trim().toDoubleOrNull() ?: return null
    val y = b.trim().toDoubleOrNull() ?: x
    val lo = minOf(x, y)
    val hi = maxOf(x, y)
    return Matcher { num(it) in lo..hi }
}

/**
 * 記憶體搜尋/修改引擎（免 root）。
 * 透過 /proc/self/mem 讀寫「本進程」記憶體 —— 這是免 root 時唯一合法可行的目標範圍。
 */
object MemEngine {
    class Region(val start: Long, val end: Long)

    private const val CHUNK = 1 shl 20
    const val CAP = 3_000_000

    @Volatile
    var type: VType = VType.I32

    var addrs = LongArray(1024)
        private set
    var vals = LongArray(1024)
        private set

    @Volatile
    var count = 0
        private set

    @Volatile
    var truncated = false
        private set

    @Volatile
    var readOnly = false
        private set

    val frozen = ConcurrentHashMap<Long, Pair<VType, Long>>()

    private val lock = Any()
    private var file: RandomAccessFile? = null
    private var freezer: Thread? = null
    private val LE = ByteOrder.LITTLE_ENDIAN

    private fun mem(): RandomAccessFile {
        file?.let { return it }
        val f = try {
            RandomAccessFile("/proc/self/mem", "rw")
        } catch (e: Exception) {
            readOnly = true
            RandomAccessFile("/proc/self/mem", "r")
        }
        file = f
        return f
    }

    private fun readAt(f: RandomAccessFile, addr: Long, buf: ByteArray, off: Int, len: Int): Boolean {
        try {
            f.seek(addr)
            var got = 0
            while (got < len) {
                val r = f.read(buf, off + got, len - got)
                if (r <= 0) return false
                got += r
            }
            return true
        } catch (e: IOException) {
            return false
        }
    }

    fun clear() {
        count = 0
        truncated = false
        addrs = LongArray(1024)
        vals = LongArray(1024)
    }

    private fun add(a: Long, v: Long): Boolean {
        if (count >= CAP) {
            truncated = true
            return false
        }
        if (count == addrs.size) {
            addrs = addrs.copyOf(count * 2)
            vals = vals.copyOf(count * 2)
        }
        addrs[count] = a
        vals[count] = v
        count++
        return true
    }

    fun regions(): List<Region> {
        val out = ArrayList<Region>()
        File("/proc/self/maps").forEachLine { line ->
            val p = line.split(Regex("\\s+"), 6)
            if (p.size < 2 || p[1].length < 2 || p[1][0] != 'r' || p[1][1] != 'w') return@forEachLine
            val path = if (p.size >= 6) p[5].trim() else ""
            if (path.startsWith("[v") || path.startsWith("/dev/") || path.startsWith("/system") ||
                path.startsWith("/vendor") || path.startsWith("/apex") || path.startsWith("/product") ||
                path.startsWith("/data/dalvik-cache") || path.endsWith(".art")
            ) return@forEachLine
            val r = p[0].split('-')
            if (r.size != 2) return@forEachLine
            val s = r[0].toLongOrNull(16) ?: return@forEachLine
            val e = r[1].toLongOrNull(16) ?: return@forEachLine
            if (e <= s || e - s > (1L shl 30)) return@forEachLine
            out.add(Region(s, e))
        }
        return out
    }

    private fun scanBuf(bb: ByteBuffer, from: Int, to: Int, base: Long, step: Int, m: Matcher): Boolean {
        var o = from
        while (o + step <= to) {
            val v = if (step == 4) bb.getInt(o).toLong() else bb.getLong(o)
            if (m.test(v)) {
                if (!add(base + o, v)) return false
            }
            o += step
        }
        return true
    }

    /** 初次搜尋：掃描所有可讀寫區段。回傳結果筆數。 */
    fun firstScan(t: VType, m: Matcher, onProgress: (Int) -> Unit): Int {
        type = t
        clear()
        val regs = regions()
        val total = regs.sumOf { it.end - it.start }.coerceAtLeast(1L)
        var done = 0L
        val buf = ByteArray(CHUNK)
        val bb = ByteBuffer.wrap(buf).order(LE)
        val step = t.size
        loop@ for (r in regs) {
            var a = r.start
            while (a < r.end) {
                val n = minOf(CHUNK.toLong(), r.end - a).toInt()
                val ok = synchronized(lock) { readAt(mem(), a, buf, 0, n) }
                if (ok) {
                    if (!scanBuf(bb, 0, n, a, step, m)) break@loop
                } else {
                    var o = 0
                    while (o < n) {
                        val l = minOf(4096, n - o)
                        val ok2 = synchronized(lock) { readAt(mem(), a + o, buf, o, l) }
                        if (ok2 && !scanBuf(bb, o, o + l, a, step, m)) break@loop
                        o += l
                    }
                }
                a += n
                done += n
                onProgress((done * 100 / total).toInt())
            }
        }
        return count
    }

    /** 在現有結果中再次篩選。 */
    fun refine(op: Op, m: Matcher?): Int {
        val t = type
        val size = t.size
        val page = ByteArray(8192)
        val bb = ByteBuffer.wrap(page).order(LE)
        var pageBase = -1L
        var pageLen = 0
        var w = 0
        for (i in 0 until count) {
            val a = addrs[i]
            if (pageBase < 0 || a < pageBase || a + size > pageBase + pageLen) {
                val base = a and 4095L.inv()
                pageBase = -1L
                pageLen = 0
                if (synchronized(lock) { readAt(mem(), base, page, 0, 8192) }) {
                    pageBase = base
                    pageLen = 8192
                } else if (synchronized(lock) { readAt(mem(), base, page, 0, 4096) }) {
                    pageBase = base
                    pageLen = 4096
                } else {
                    continue
                }
            }
            val o = (a - pageBase).toInt()
            val cur = if (size == 4) bb.getInt(o).toLong() else bb.getLong(o)
            val old = vals[i]
            val keep = when (op) {
                Op.MATCH -> m != null && m.test(cur)
                Op.INC -> t.num(cur) > t.num(old)
                Op.DEC -> t.num(cur) < t.num(old)
                Op.CHANGED -> cur != old
                Op.SAME -> cur == old
            }
            if (keep) {
                addrs[w] = a
                vals[w] = cur
                w++
            }
        }
        count = w
        return w
    }

    fun peek(addr: Long, t: VType): Long? {
        val b = ByteArray(t.size)
        val ok = synchronized(lock) { readAt(mem(), addr, b, 0, t.size) }
        if (!ok) return null
        val bb = ByteBuffer.wrap(b).order(LE)
        return if (t.size == 4) bb.getInt(0).toLong() else bb.getLong(0)
    }

    fun write(addr: Long, t: VType, bits: Long): Boolean = synchronized(lock) {
        try {
            val bb = ByteBuffer.allocate(t.size).order(LE)
            if (t.size == 4) bb.putInt(bits.toInt()) else bb.putLong(bits)
            val f = mem()
            f.seek(addr)
            f.write(bb.array())
            true
        } catch (e: IOException) {
            false
        }
    }

    @Synchronized
    fun setFrozen(addr: Long, t: VType, bits: Long, on: Boolean) {
        if (!on) {
            frozen.remove(addr)
            return
        }
        frozen[addr] = Pair(t, bits)
        if (freezer?.isAlive == true) return
        freezer = Thread {
            while (true) {
                for ((a, p) in frozen) write(a, p.first, p.second)
                try {
                    Thread.sleep(40)
                } catch (e: InterruptedException) {
                    return@Thread
                }
            }
        }.apply {
            isDaemon = true
            start()
        }
    }

    /** 引擎自檢：在測試目標中搜尋獨特數值、改寫，再確認真的被改到。 */
    fun selfTest(): String {
        val tb = TestTarget.buf
        val magic = 1357924680
        val after = 1357924681
        tb.putInt(0, magic)
        val regs = regions().size
        val m = VType.I32.exact(magic.toString())!!
        val n = firstScan(VType.I32, m) { }
        var writes = 0
        for (i in 0 until count) {
            if (write(addrs[i], VType.I32, after.toLong())) writes++
        }
        val ok = tb.getInt(0) == after
        tb.putInt(0, 100)
        clear()
        return "掃描 $regs 個區段、找到 $n 筆、成功寫入 $writes 筆" +
            (if (readOnly) "（唯讀模式）" else "") + "\n" +
            if (ok) "✅ 測試目標已被改寫，引擎正常" else "❌ 測試目標沒有被改到"
    }
}

/** 本 App 內的測試目標：值放在原生記憶體（不會被 GC 搬動），可用來驗證搜尋/修改。 */
object TestTarget {
    val buf: ByteBuffer = ByteBuffer.allocateDirect(64).order(ByteOrder.LITTLE_ENDIAN).apply {
        putInt(0, 100)
        putFloat(8, 250.5f)
        putLong(16, 123456789012L)
        putDouble(24, 3.14159)
    }
}
