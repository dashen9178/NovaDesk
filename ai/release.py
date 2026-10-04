# -*- coding: utf-8 -*-
"""
一条命令出包：网页包 -> 塞进安卓 assets -> 编译 APK -> 拷到分享目录。

以前这几步是手敲的，漏掉"把 NovaDesk.html 拷进 assets"就会出现
"网页改了但 APK 里还是老版本"这种幽灵问题。所以固化下来。

用法：python release.py
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # D:\DSH工作\NovaDesk
ANDROID = os.path.join(ROOT, "android")
WEB_OUT = os.path.join(ROOT, "NovaDesk.html")
ASSET = os.path.join(ANDROID, "app", "src", "main", "assets", "NovaDesk.html")
GRADLE_CFG = os.path.join(ANDROID, "app", "build.gradle.kts")
APK_BUILT = os.path.join(ANDROID, "app", "build", "outputs", "apk", "debug", "app-debug.apk")
SHARE = r"D:\novadesk-share"
JAVA_HOME = r"D:\android-build\jdk"
GRADLE_HOME = r"D:\android-build\gradle-8.11.1"
ANDROID_HOME = r"D:\android-build\sdk"


def run(cmd, cwd, env=None):
    print("$ " + " ".join(cmd))
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        sys.stdout.write(p.stdout or "")
        sys.stderr.write(p.stderr or "")
        raise SystemExit("命令失败：%s" % " ".join(cmd))
    return p.stdout or ""


def read_version():
    """从 build.gradle.kts 里读 versionName —— 单一事实来源"""
    with open(GRADLE_CFG, "r", encoding="utf-8") as f:
        txt = f.read()
    m = re.search(r'versionName\s*=\s*"([^"]+)"', txt)
    if not m:
        raise SystemExit("build.gradle.kts 里找不到 versionName")
    return m.group(1)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    version = read_version()
    print("== 版本 %s ==" % version)

    # 1) 打网页包
    out = run([sys.executable, "build.py"], HERE)
    print(out.strip())

    # 2) 塞进 assets（这一步最容易漏）
    shutil.copyfile(WEB_OUT, ASSET)
    print("已拷进 assets：%s -> %s (%d 字节)"
          % (os.path.basename(WEB_OUT), ASSET, os.path.getsize(ASSET)))

    # 3) 校验 assets 里的内容和刚打包的一模一样
    if sha256(WEB_OUT) != sha256(ASSET):
        raise SystemExit("assets 里的页面和刚打包的不一致，别继续了")
    print("assets 校验通过（哈希一致）")

    # 4) 编译 APK
    env = dict(os.environ)
    env["JAVA_HOME"] = JAVA_HOME
    env["ANDROID_HOME"] = ANDROID_HOME
    env["ANDROID_SDK_ROOT"] = ANDROID_HOME
    env["PATH"] = os.path.join(JAVA_HOME, "bin") + os.pathsep + env.get("PATH", "")
    gradle = os.path.join(GRADLE_HOME, "bin", "gradle.bat")
    out = run([gradle, "assembleDebug", "--console=plain", "-q"], ANDROID, env)
    tail = [l for l in out.splitlines() if l.strip()][-8:]
    for l in tail:
        print("   " + l)

    if not os.path.exists(APK_BUILT):
        raise SystemExit("没找到 APK：%s" % APK_BUILT)

    # 5) 拷到分享目录
    os.makedirs(SHARE, exist_ok=True)
    apk_name = "NovaDesk-%s.apk" % version
    apk_out = os.path.join(SHARE, apk_name)
    shutil.copyfile(APK_BUILT, apk_out)
    shutil.copyfile(WEB_OUT, os.path.join(SHARE, "NovaDesk.html"))

    # 6) 确认 APK 里的 assets 就是刚打的那个包（别信"应该没问题"）
    import zipfile
    with zipfile.ZipFile(apk_out) as z:
        inner = z.read("assets/NovaDesk.html")
    inner_hash = hashlib.sha256(inner).hexdigest()
    if inner_hash != sha256(WEB_OUT):
        raise SystemExit("APK 里的页面和源文件不一致！")
    print("APK 内部页面校验通过")

    size = os.path.getsize(apk_out)
    print("")
    print("APK   : %s  (%.2f MB)" % (apk_out, size / 1048576.0))
    print("SHA256: %s" % sha256(apk_out))
    print("HTML  : %s" % os.path.join(SHARE, "NovaDesk.html"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
