# -*- coding: utf-8 -*-
"""GeoForge 数据转换器 - 程序入口"""
import os
import sys


def _fix_syspath():
    """支持源码（脚本）方式与打包（frozen）方式两种运行。"""
    if getattr(sys, 'frozen', False):
        return
    # 脚本方式：把本目录加入 sys.path，使 core 可导入
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


def main():
    _fix_syspath()
    from gui import main as gui_main
    gui_main()


if __name__ == '__main__':
    main()
