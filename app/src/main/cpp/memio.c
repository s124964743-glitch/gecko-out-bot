#include <jni.h>
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* 以 SIGSEGV 保護的記憶體複製：位址無效時回傳失敗而不是讓遊戲閃退。 */
static __thread sigjmp_buf tl_jb;
static __thread volatile int tl_guard = 0;

static void on_fault(int sig, siginfo_t *si, void *ctx) {
    (void) si;
    (void) ctx;
    if (tl_guard) siglongjmp(tl_jb, 1);
    signal(sig, SIG_DFL);
}

static int guarded_copy(void *dst, const void *src, size_t n) {
    struct sigaction sa, old_segv, old_bus;
    memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = on_fault;
    sa.sa_flags = SA_SIGINFO | SA_NODEFER;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGSEGV, &sa, &old_segv);
    sigaction(SIGBUS, &sa, &old_bus);
    int ok = 1;
    if (sigsetjmp(tl_jb, 1) == 0) {
        tl_guard = 1;
        memcpy(dst, src, n);
        tl_guard = 0;
    } else {
        tl_guard = 0;
        ok = 0;
    }
    sigaction(SIGSEGV, &old_segv, NULL);
    sigaction(SIGBUS, &old_bus, NULL);
    return ok;
}

JNIEXPORT jboolean JNICALL
Java_top_niunaijun_blackboxa_scanner_NativeMem_read(JNIEnv *env, jclass c, jlong addr,
                                                    jbyteArray dst, jint off, jint len) {
    (void) c;
    if (len <= 0) return JNI_TRUE;
    char *tmp = (char *) malloc((size_t) len);
    if (!tmp) return JNI_FALSE;
    int ok = guarded_copy(tmp, (const void *) (uintptr_t) addr, (size_t) len);
    if (ok) (*env)->SetByteArrayRegion(env, dst, off, len, (const jbyte *) tmp);
    free(tmp);
    return ok ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jboolean JNICALL
Java_top_niunaijun_blackboxa_scanner_NativeMem_write(JNIEnv *env, jclass c, jlong addr,
                                                     jbyteArray src, jint off, jint len) {
    (void) c;
    if (len <= 0) return JNI_TRUE;
    char *tmp = (char *) malloc((size_t) len);
    if (!tmp) return JNI_FALSE;
    (*env)->GetByteArrayRegion(env, src, off, len, (jbyte *) tmp);
    int ok = guarded_copy((void *) (uintptr_t) addr, tmp, (size_t) len);
    free(tmp);
    return ok ? JNI_TRUE : JNI_FALSE;
}
