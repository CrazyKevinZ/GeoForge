# -*- coding: utf-8 -*-
"""构建脚本：把 GeoForge 编译为可执行 exe。

用法：
    python build.py

需要已安装：pip install pyinstaller ezdxf
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # 本文件所在目录 = 应用目录
APP = HERE                                                  # 应用目录即源代码目录
PARENT = os.path.dirname(HERE)                              # 上级目录（含 LOGO.png）
BUILD = os.path.join(HERE, "_build")
DIST = os.path.join(HERE, "_dist")

# 目标（公用版本）：本体放到 C:\Users\Administrator\Documents\Default Project\dxf2Gis
TARGET = r"C:\Users\Administrator\Documents\Default Project\dxf2Gis"


def main():
    # 复制图标到 app 目录（源图标在上级目录 LOGO.png，此处放在应用目录）
    for name in ("LOGO.png",):
        src = os.path.join(PARENT, name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(APP, name))
    # 若已有直接位于应用目录的 LOGO.png（被打包二次），也保留
    if not os.path.exists(os.path.join(APP, "LOGO.png")):
        for cand in (os.path.join(PARENT, "LOGO.png"),):
            if os.path.exists(cand):
                shutil.copy(cand, os.path.join(APP, "LOGO.png"))

    # 生成 .ico（PyInstaller --icon 需要 ico）
    ico = os.path.join(APP, "LOGO.ico")
    logo_png = os.path.join(APP, "LOGO.png")
    if os.path.exists(logo_png) and not os.path.exists(ico):
        try:
            from PIL import Image
            im = Image.open(logo_png)
            if im.mode != 'RGBA':
                im = im.convert('RGBA')
            im.save(ico, sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
        except Exception as e:
            print("无法生成 ico：", e)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",
        "--name", "GeoForge",
        "--icon", os.path.join(APP, "LOGO.ico"),
        "--collect-data", "tkinterdnd2",
        "--collect-data", "pyproj",
        "--distpath", DIST,
        "--workpath", BUILD,
        "--specpath", HERE,
        os.path.join(APP, "main.py"),
    ]
    print("执行:", " ".join(cmd))
    subprocess.check_call(cmd)

    exe = os.path.join(DIST, "GeoForge.exe")
    if os.path.exists(exe):
        os.makedirs(TARGET, exist_ok=True)
        dst = os.path.join(TARGET, "GeoForge.exe")
        shutil.copy(exe, dst)
        # 窗口/任务栏图标已内嵌到 exe，运行时无需另放 LOGO.png
        print(f"\n[OK] 编译完成：{dst}")
        return 0
    else:
        print("\n[FAIL] 未找到输出 exe")
        return 1


if __name__ == '__main__':
    sys.exit(main())
