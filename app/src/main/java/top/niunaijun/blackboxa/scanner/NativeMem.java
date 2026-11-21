package top.niunaijun.blackboxa.scanner;

/** 直接讀寫本進程記憶體的原生函式（有 SIGSEGV 保護）。 */
public final class NativeMem {
    public static final boolean OK;

    static {
        boolean ok;
        try {
            System.loadLibrary("memio");
            ok = true;
        } catch (Throwable t) {
            ok = false;
        }
        OK = ok;
    }

    private NativeMem() {}

    public static native boolean read(long addr, byte[] dst, int off, int len);

    public static native boolean write(long addr, byte[] src, int off, int len);
}
