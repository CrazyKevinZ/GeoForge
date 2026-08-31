# -*- coding: utf-8 -*-
"""GeoForge 数据转换器 - tkinter 图形界面

依据 UI.MD 视觉规范实现：顶部导航栏、核心转换场景卡片、
格式徽章、底部全局状态栏（进度条 + 百分比 + 已用/剩余时间）。
支持 11 种格式任意互转 + 拖拽文件 + 独立卡片布局。
"""
import os
import sys
import gc
import time
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from core.converter import ConvertJob
from core.exporter import FORMATS, FMT_LABEL
from core.estimator import human_size

APP_NAME = "GeoForge 数据转换器"
VIP_TEXT = "V 1.0.1.26091"

EXT_TO_FMT = {
    ".dxf": "GeoJSON", ".dwg": "GeoJSON", ".geojson": "KML", ".kml": "GeoJSON",
    ".json": "KML", ".csv": "GeoJSON", ".txt": "GeoJSON", ".gpx": "KML",
    ".xml": "GeoJSON", ".osm": "GeoJSON",
}
SUPPORTED_SRC_EXTS = [".dxf", ".dwg", ".geojson", ".json", ".csv", ".txt",
                      ".kml", ".gpx", ".osm", ".xml"]

COLOR = {
    "GeoJSON": "#059669", "JSON": "#7C3AED", "CSV": "#64748B", "TXT": "#64748B",
    "OVTXT": "#0D9488", "KML": "#0EA5E9", "OVKML": "#0284C7", "GPX": "#EA580C",
    "DXF": "#1E40AF", "OSM": "#DB2777", "XML": "#7C3AED",
}


def base_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.dirname(__file__))


def _ui_cfg_file() -> str:
    return os.path.join(base_dir(), "GeoForge_ui.json")


def _strip_jsonc(text: str) -> str:
    """去掉文件头部的 // 注释行，返回可被 json.loads 解析的纯 JSON。"""
    lines = []
    for ln in text.splitlines():
        if ln.strip().startswith('//'):
            continue
        lines.append(ln)
    return '\n'.join(lines)


def _load_geometry(default: str) -> str:
    """读取上次保存的窗口几何；无效或失败则用默认。"""
    try:
        import json
        with open(_ui_cfg_file(), 'r', encoding='utf-8') as fh:
            cfg = json.loads(_strip_jsonc(fh.read()))
        geo = cfg.get('geometry', '')
        if geo and 'x' in geo:
            return geo
    except Exception:
        pass
    return default


def _save_geometry(geometry: str):
    try:
        import json
        cfg = {}
        try:
            with open(_ui_cfg_file(), 'r', encoding='utf-8') as fh:
                cfg = json.loads(_strip_jsonc(fh.read()))
        except Exception:
            cfg = {}
        cfg['geometry'] = geometry
        header = (
            "// ============================================================\n"
            "// GeoForge 界面状态配置文件\n"
            "// 作用：记录主窗口的位置与大小（geometry），下次启动时恢复\n"
            "//       上次的窗口状态。\n"
            "// 删除：可以安全删除。删除后程序会使用默认窗口大小，并在\n"
            "//       下次关闭窗口时自动重新生成本文件。\n"
            "// 本文件由程序自动生成与更新，无需手动编辑。\n"
            "// ============================================================\n"
        )
        with open(_ui_cfg_file(), 'w', encoding='utf-8') as fh:
            fh.write(header)
            json.dump(cfg, fh, ensure_ascii=False)
    except Exception:
        pass


def resource(name: str) -> str:
    return os.path.join(base_dir(), name)


class GeoForgeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_NAME)
        root.geometry(_load_geometry("1080x560"))
        root.minsize(820, 520)

        try:
            # 窗口/任务栏图标：以内嵌 base64 方式打包，无需外部 LOGO.png
            from core.icon import png_data_b64
            img = tk.PhotoImage(data=png_data_b64())
            root.iconphoto(True, img)
            self._logo_img = img
        except Exception:
            pass

        # 状态
        self.source_path = ""
        self.out_dir = ""
        self.target_fmt = tk.StringVar(value="GeoJSON")
        self.status_text = tk.StringVar(value="就绪")
        self.est_var = tk.StringVar(value="估算大小：--")
        self.queue_text = tk.StringVar(value="等待中: 0 | 转换中: 0 | 完成: 0")
        # 进度条（绘制在进度条上的组合文字：百分比 ｜ 时间）
        self.prog_text_var = tk.StringVar(value="0%  ｜  已用 0.0s | 剩余 --")
        self._bar_val = 0.0          # 当前显示进度（0~100）
        self._bar_target = 0.0       # 转换报道的目标进度（用于平滑补间）
        self._bar_remaining = -1.0   # 剩余时间估计，-1=未知
        # 持续秒表：任何任务（估算/转换）运行期间每 100ms 更新一次时间
        self._watch_start = 0.0
        self._watch_elapsed = 0.0    # 停表后记录的最终用时
        self._watch_active = False
        self._last_elapsed = 0.0
        self._last_remaining = 0.0
        self._done_count = 0
        self._running = False
        self._cancel_requested = False
        self._conv_gen = 0
        self.job = None
        self._log = []
        self.ref_lonlat = tk.StringVar()   # DXF→其余格式的参考经纬度（格式：经度,纬度）
        self._ref_needed = False

        self._build_style()
        self._build_header()
        self._build_main()
        # 无独立底部状态栏：进度条置于“输出大小估算”卡片内，UI 外框保持留空

        # 默认目标格式 GeoJSON（选择源文件后自动识别格式）
        self._refresh_fmt()

        self._log.append("欢迎使用 GeoForge 数据转换器")

    # ------------------------------------------------------------- 样式
    def _build_style(self):
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure('TEntry', padding=(8, 6))

    # ------------------------------------------------------------- 组件
    def _build_header(self):
        hdr = tk.Frame(self.root, bg='#FFFFFF', height=48)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)
        sep = tk.Frame(self.root, bg='#F0F0F0', height=1)
        sep.pack(fill='x')

        logo_box = tk.Frame(hdr, bg='#00B3A4', width=32, height=32)
        logo_box.place(x=16, y=8)
        tk.Label(logo_box, text="G", bg='#00B3A4', fg='white',
                 font=('Arial', 15, 'bold')).place(relx=0.5, rely=0.5, anchor='center')

        tk.Label(hdr, text=APP_NAME, bg='#FFFFFF', fg='#0F172A',
                 font=('Microsoft YaHei', 13, 'bold')).place(x=58, y=12)

        # VIP 徽章
        vip = tk.Label(hdr, text=VIP_TEXT, bg='#7C3AED', fg='white',
                       font=('Microsoft YaHei', 9, 'bold'))
        vip.place(relx=1.0, x=-30, y=14, anchor='ne')

    def _card(self, parent, title, accent_hex, subtitle=None, bg='#FFFFFF', right_factory=None):
        """统一的卡片容器。返回 (card_frame, inner_frame)。
        title 显示在头部左侧；right_factory 若提供，则会在头部行内被调用，
        并用其返回的组件当作标题右侧的同排内容（父级即标题行，保证同排）。"""
        card = tk.Frame(parent, bg=bg, highlightthickness=1, highlightbackground='#E2E8F0')
        side = tk.Frame(card, bg=accent_hex, width=5)
        side.pack(side='left', fill='y')
        inner = tk.Frame(card, bg=bg)
        inner.pack(side='left', fill='both', expand=True, padx=10, pady=6)
        if right_factory is not None:
            # 标题与提示同排：建头部行，标题靠左，提示紧随其后（padx 为约4个字符间距）
            head = tk.Frame(inner, bg=bg)
            head.pack(fill='x', anchor='w')
            tk.Label(head, text=title, bg=bg, fg='#0F172A',
                     font=('Microsoft YaHei', 11, 'bold')).pack(side='left')
            rw = right_factory(head)
            rw.config(bg=bg)
            rw.pack(in_=head, side='left', padx=(44, 0))
        else:
            tk.Label(inner, text=title, bg=bg, fg='#0F172A',
                     font=('Microsoft YaHei', 11, 'bold')).pack(anchor='w')
        if subtitle:
            tk.Label(inner, text=subtitle, bg=bg, fg='#94A3B8',
                     font=('Microsoft YaHei', 9)).pack(anchor='w', pady=(1, 0))
        return card, inner

    def _badge(self, parent, text, bg):
        tk.Label(parent, text=text, bg=bg, fg='white',
                 font=('Microsoft YaHei', 8, 'bold'), padx=7, pady=2).pack(side='left', padx=2)

    @staticmethod
    def _fmt_label(fmt: str) -> str:
        return FMT_LABEL.get(fmt, fmt)

    def _build_main(self):
        # 直接布局，无滚动条；区块各取自然高度、宽度随窗口拉伸（整体缩放不截断）
        body = tk.Frame(self.root, bg='#FAFAFA')
        body.pack(fill='both', expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)  # 配置：吸收多余高度
        body.rowconfigure(1, weight=0)  # 估算：自然高

        # ---- 配置区（自动识别文件格式，无需切换场景）----
        g2 = tk.Frame(body, bg='#FAFAFA')
        g2.grid(row=0, column=0, sticky='nsew')
        g2.grid_columnconfigure(0, weight=1)
        g2.rowconfigure(0, weight=1)   # 卡片区吸收高度
        g2.rowconfigure(1, weight=1)   # 输出目录/格式区吸收高度

        src_card, src_inner = self._card(g2, "选择源文件", "#2563EB",
                                         "支持 10 种格式互转：DXF / GeoJSON / JSON / CSV / TXT / KML / OVKML / GPX / OSM / XML")
        src_card.grid(row=0, column=0, sticky='nsew', padx=20, pady=(10, 0))

        # 下半区：左=选择文件（拖拽/点击），右=参考经纬度输入
        src_mid = tk.Frame(src_inner, bg='#FFFFFF')
        src_mid.pack(fill='x', pady=(8, 0))
        src_mid.grid_columnconfigure(0, weight=3, minsize=360)
        src_mid.grid_columnconfigure(1, weight=2, minsize=250)
        # 左列上下分开：0=徽章行，1=选择文件，2=提示（右列顶对齐）
        left_col = tk.Frame(src_mid, bg='#FFFFFF')
        left_col.grid(row=0, column=0, sticky='nsew')

        # 格式徽章行（保持在一行显示）
        badge_row = tk.Frame(left_col, bg='#FFFFFF')
        badge_row.pack(fill='x', pady=(6, 0))
        for b_idx, fmt in enumerate(FORMATS):
            self._badge(badge_row, self._fmt_label(fmt), COLOR.get(fmt, '#64748B'))
        self.src_badge_row = badge_row

        # 拖拽/点击区（选中后切换为文件展示）
        zone = tk.Frame(left_col, bg='#FFFFFF')
        zone.pack(fill='x', pady=(8, 0))
        self.src_zone = zone

        drop_btn = tk.Button(zone, bg='#F8FAFC', fg='#2563EB', relief='solid', bd=2,
                             highlightbackground='#CBD5E1', activebackground='#EFF6FF',
                             activeforeground='#1D4ED8', cursor='hand2', anchor='center',
                             font=('Microsoft YaHei', 11, 'bold'),
                             text="＋  点击选择文件，或将文件拖拽到此处",
                             command=self.choose_source)
        drop_btn.configure(wraplength=420, justify='center')
        drop_btn.pack(fill='x', ipady=6)
        self.drop_btn = drop_btn

        # 文件展示区（选中文件后显示）
        file_box = tk.Frame(zone, bg='#F8FAFC', highlightthickness=2,
                            highlightbackground='#2563EB', cursor='hand2')
        self.file_box = file_box
        self.file_badge = tk.Label(file_box, text="DXF", bg='#1E40AF', fg='white',
                                   font=('Microsoft YaHei', 10, 'bold'), padx=10, pady=4)
        self.file_badge.pack(side='left', padx=(12, 12), pady=8)
        mid = tk.Frame(file_box, bg='#F8FAFC')
        mid.pack(side='left', fill='x', expand=True, pady=6)
        self.file_name_lbl = tk.Label(mid, text="", bg='#F8FAFC', fg='#0F172A',
                                      font=('Microsoft YaHei', 11, 'bold'), anchor='w')
        self.file_name_lbl.pack(fill='x')
        self.file_path_lbl = tk.Label(mid, text="", bg='#F8FAFC', fg='#64748B',
                                      font=('Microsoft YaHei', 9), anchor='w')
        self.file_path_lbl.pack(fill='x')
        # 右侧操作：取消选择 + 重新选择
        ops = tk.Frame(file_box, bg='#F8FAFC')
        ops.pack(side='right', padx=(4, 12))
        self.cancel_btn = tk.Button(ops, text="取消选择", bg='#F8FAFC', fg='#DC2626',
                                    font=('Microsoft YaHei', 9, 'bold'), relief='flat',
                                    activebackground='#F8FAFC', activeforeground='#B91C1C',
                                    cursor='hand2', command=self.clear_source)
        self.cancel_btn.pack(side='left', padx=(0, 8))
        tk.Button(ops, text="↻ 重新选择", bg='#F8FAFC', fg='#2563EB',
                  font=('Microsoft YaHei', 9, 'bold'), relief='flat',
                  activebackground='#F8FAFC', activeforeground='#1D4ED8',
                  cursor='hand2', command=self.choose_source).pack(side='left')
        file_box.bind('<Button-1>', lambda e: self.choose_source())
        # 只给非按钮区域绑定“点击即重新选择”，避免与“取消选择/重新选择”冲突
        for w in file_box.winfo_children():
            if w in (ops,):
                continue
            w.bind('<Button-1>', lambda e: self.choose_source())
        self.file_display = file_box

        tk.Label(left_col, text="点击文件卡片可重新选择源文件",
                 bg='#FFFFFF', fg='#94A3B8', font=('Microsoft YaHei', 9)).pack(anchor='w', pady=(4, 0))
        self.drop_zone = zone  # 兼容拖拽注册（整块可拖）

        # 右列：参考经纬度输入
        ref_frame = tk.Frame(src_mid, bg='#FFFFFF', highlightbackground='#FDE68A',
                             highlightthickness=1)
        ref_frame.grid(row=0, column=1, sticky='nsew', padx=(12, 0))
        self.ref_frame = ref_frame
        tk.Label(ref_frame, text="参照经纬度（DXF转其余格式必填）",
                 bg='#FFFFFF', fg='#B45309', font=('Microsoft YaHei', 9, 'bold')).pack(anchor='w', padx=10, pady=(8, 2))
        sub = tk.Label(ref_frame, text="所选图纸项目所在地附近任意一点经纬度，填写格式：经度,纬度",
                       bg='#FFFFFF', fg='#94A3B8', font=('Microsoft YaHei', 8))
        sub.pack(anchor='w', padx=10)
        self.ref_entry = tk.Entry(ref_frame, textvariable=self.ref_lonlat,
                                  font=('Microsoft YaHei', 10))
        self.ref_entry.pack(fill='x', padx=10, pady=(6, 4))
        # 点击输入框即全选，便于一键清除更换新值（支持再次点击也全选）
        self.ref_entry.bind('<FocusIn>', self._ref_select_all)
        self.ref_entry.bind('<Button-1>', self._ref_select_all)
        # 预置 3° 带坐标：点击对应 ZONE 按钮自动填入该带内的预置经纬度
        zones = ["Z29", "Z30", "Z35", "Z36", "Z37", "Z38", "Z39", "Z40"]
        zone_colors = ["#1E40AF", "#0EA5E9", "#059669", "#7C3AED", "#DB2777",
                       "#EA580C", "#0D9488", "#64748B"]
        zone_frame = tk.Frame(ref_frame, bg='#FFFFFF')
        zone_frame.pack(fill='x', padx=10, pady=(0, 8))
        tk.Label(zone_frame, text="点击下列编号(Z29~Z40)自动填写3°带预置经纬度：", bg='#FFFFFF', fg='#64748B',
                 font=('Microsoft YaHei', 8)).pack(anchor='w', pady=(0, 4))
        zb_row = tk.Frame(zone_frame, bg='#FFFFFF')
        zb_row.pack(fill='x')
        for i, zone in enumerate(zones):
            _zc = zone_colors[i % len(zone_colors)]
            btn = tk.Button(zb_row, text=zone, bg=_zc, fg='white', relief='raised', bd=2,
                            font=('Microsoft YaHei', 9, 'bold'), padx=0, pady=2, cursor='hand2',
                            activebackground=_zc, activeforeground='white',
                            command=lambda z=zone: self._fill_zone_preset(z))
            btn.pack(side='left', fill='x', expand=True, padx=1)
        ref_frame.grid_remove()

        # 输出目录（独立卡片） + 目标格式 两块并排
        opt_row = tk.Frame(g2, bg='#FAFAFA')
        opt_row.grid(row=1, column=0, sticky='nsew', padx=20, pady=(8, 0))
        g2.rowconfigure(2, weight=1)
        opt_row.grid_columnconfigure(0, weight=4)
        opt_row.grid_columnconfigure(1, weight=5)

        # ---- 输出目录（独立卡片）----
        out_card, out_inner = self._card(opt_row, "输出目录", "#059669",
                                         "默认保存到桌面，可点击“浏览…”或手动填写确定修改")
        out_card.grid(row=0, column=0, sticky='nsew', padx=(0, 6))
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(desktop):
            desktop = os.path.expanduser("~")
        self.out_dir = desktop
        self.out_entry = ttk.Entry(out_inner, font=('Microsoft YaHei', 9))
        self.out_entry.insert(0, desktop)
        self.out_entry.pack(fill='x', pady=(6, 0))
        btn_row = tk.Frame(out_inner, bg='#FFFFFF')
        btn_row.pack(fill='x', pady=(6, 0))
        # 打开输出目录
        open_btn = tk.Button(btn_row, text="打开输出目录", bg='#0D9488', fg='white', relief='flat',
                             font=('Microsoft YaHei', 9, 'bold'), padx=14, pady=3, cursor='hand2',
                             activebackground='#0F766E', activeforeground='white',
                             command=self.open_outdir)
        open_btn.pack(side='right', padx=(6, 0))
        # 确定：应用手动输入的目录，不存在则自动新建
        ok_btn = tk.Button(btn_row, text="确定", bg='#059669', fg='white', relief='flat',
                           font=('Microsoft YaHei', 9, 'bold'), padx=18, pady=3, cursor='hand2',
                           activebackground='#047857', activeforeground='white',
                           command=self.apply_outdir)
        ok_btn.pack(side='right', padx=(6, 0))
        out_btn = tk.Button(btn_row, text="浏览…", bg='#059669', fg='white', relief='flat',
                            font=('Microsoft YaHei', 9, 'bold'), padx=16, pady=3, cursor='hand2',
                            activebackground='#047857', activeforeground='white',
                            command=self.choose_outdir)
        out_btn.pack(side='right')

        # ---- 目标格式（加大徽章选择器）----
        self.conv_hint_frame = None          # 由下方 right_factory 在标题行内创建
        fmt_card, fmt_inner = self._card(opt_row, "选择目标格式", "#D97706",
                                         "点击格式徽章选择输出格式，点击下方「开始估算」和「开始转换」查看输出大小和转换操作",
                                         right_factory=lambda head: self._build_conv_hint(head))
        fmt_card.grid(row=0, column=1, sticky='nsew', padx=(6, 0))
        self.fmt_buttons = {}
        self.conv_hint_badges = {}
        row_frame = None
        for idx, fmt in enumerate(FORMATS):
            if idx % 5 == 0:
                row_frame = tk.Frame(fmt_inner, bg='#FFFFFF')
                row_frame.pack(fill='x', pady=(2, 0))
            b = tk.Button(row_frame, text=self._fmt_label(fmt), width=9, bg='#FFFFFF', fg='#334155',
                          relief='solid', bd=1, highlightbackground='#CBD5E1',
                          font=('Microsoft YaHei', 9, 'bold'), pady=2, cursor='hand2',
                          command=lambda f=fmt: self.select_fmt(f))
            b.pack(side='left', padx=4, pady=2, fill='x', expand=True)
            self.fmt_buttons[fmt] = b
        self._refresh_fmt()

        # ---- 估算大小 + 开始转换（同卡并排）----
        g3 = tk.Frame(body, bg='#FAFAFA')
        g3.grid(row=1, column=0, sticky='nsew', pady=(6, 2))
        g3.grid_columnconfigure(0, weight=1)
        est_card, est_inner = self._card(g3, "输出大小估算", "#7C3AED")
        est_card.grid(row=0, column=0, sticky='nsew', padx=20)
        est_start = tk.Frame(est_inner, bg='#FFFFFF')
        est_start.pack(fill='x', pady=(4, 0))
        self.est_lbl = tk.Label(est_start, textvariable=self.est_var, bg='#EBF5FF', fg='#1D4ED8',
                                font=('Microsoft YaHei', 11, 'bold'), padx=12, pady=8)
        self.est_lbl.pack(side='left')
        # 手动估算按钮（不再自动估算）
        tk.Button(est_start, text="开始估算", bg='#EAF2FE', fg='#2563EB', relief='flat',
                 font=('Microsoft YaHei', 10, 'bold'), padx=14, pady=6, cursor='hand2',
                 activebackground='#DBEAFE', activeforeground='#1D4ED8',
                 command=self._update_est).pack(side='left', padx=12)
        # 醒目的开始转换按钮（紧挨估算大小）
        self.start_btn = tk.Button(est_start, text="开始转换", bg='#2563EB', fg='white', relief='flat',
                                   font=('Microsoft YaHei', 13, 'bold'), padx=34, pady=8, cursor='hand2',
                                   activebackground='#1D4ED8', activeforeground='white',
                                   command=self.start_conversion)
        self.start_btn.pack(side='right', pady=2)

        # 进度条：透明底 + 淡蓝填充；文字透明底双层绘制
        #（底色深色，填充覆盖部分自动变为白色，实现“填充经过文字变成进度条样式”）
        prog_strip = tk.Frame(est_inner, bg='#FFFFFF', height=28)
        prog_strip.pack(fill='x', pady=(8, 0))
        prog_strip.pack_propagate(False)
        self.prog_strip = prog_strip
        self.prog_canvas = tk.Canvas(prog_strip, height=28, bg='#FFFFFF',
                                     highlightthickness=0, bd=0)
        self.prog_canvas.pack(fill='x')
        self._prog_font = ('Microsoft YaHei', 10, 'bold')
        self._fill_item = self.prog_canvas.create_rectangle(0, 0, 0, 28,
                                                            fill='#BFDBFE', outline='')
        self._base_text_item = self.prog_canvas.create_text(0, 14,
                                                            text=self.prog_text_var.get(),
                                                            fill='#334155', font=self._prog_font,
                                                            anchor='center')  # 底色文字（未填充区）
        # 覆盖层画布：宽度=已填充宽度，白色文字在此上显示；宽度外自动裁切
        self._ovl_canvas = tk.Canvas(prog_strip, height=28, bg='#BFDBFE',
                                     highlightthickness=0, bd=0, width=0)
        self._ovl_canvas.place(x=0, y=0)
        self._ovl_text_item = self._ovl_canvas.create_text(0, 14,
                                                           text=self.prog_text_var.get(),
                                                           fill='#FFFFFF',
                                                           font=self._prog_font,
                                                           anchor='center')

        # ---- 参考经纬度（DXF/DWG → 其余格式时必填）----
        # 放置在“选择源文件”卡片右侧（见 _build_main 顶部布局）

        # 记录 body 引用用于拖拽
        self.body = body

    def _refresh_fmt(self):
        if not self.fmt_buttons:
            return
        sel = self.target_fmt.get()
        for fmt, b in self.fmt_buttons.items():
            if fmt == sel:
                b.config(bg=COLOR.get(fmt, '#2563EB'), fg='white', relief='flat',
                         highlightbackground=COLOR.get(fmt, '#2563EB'))
            else:
                b.config(bg='#FFFFFF', fg='#334155', relief='solid',
                         highlightbackground='#CBD5E1')
        self._update_conv_hint()

    def _src_format(self):
        """从源文件扩展名推导源格式显示名（无源文件返回 None）。"""
        if not self.source_path:
            return None
        ext = os.path.splitext(self.source_path)[1].lower()
        name = {
            ".dxf": "DXF", ".dwg": "DWG", ".geojson": "GeoJSON", ".json": "JSON",
            ".csv": "CSV", ".txt": "TXT", ".kml": "KML", ".gpx": "GPX",
            ".osm": "OSM", ".xml": "XML", ".ovkml": "OVKML",
        }
        return name.get(ext, ext.lstrip('.').upper() or None)

    def _build_conv_hint(self, head):
        """在标题行 head 内创建转换提示框，随后由 _update_conv_hint 填充内容。"""
        f = tk.Frame(head, bg='#FFFFFF')
        self.conv_hint_frame = f
        return f

    def _update_conv_hint(self):
        """渲染动态转换提示：源格式 转 目标格式（徽章同款样式）。"""
        if not hasattr(self, 'conv_hint_frame') or self.conv_hint_frame is None:
            return
        # 清空旧的提示内容
        for w in self.conv_hint_frame.winfo_children():
            w.destroy()
        src = self._src_format()
        tgt = self.target_fmt.get()
        if not src:
            lbl = tk.Label(self.conv_hint_frame, text="尚未选择源文件",
                           bg='#FFFFFF', fg='#94A3B8', font=('Microsoft YaHei', 9))
            lbl.pack(side='left', padx=(6, 0))
            return
        # 源徽章
        tk.Label(self.conv_hint_frame, text=src, bg=COLOR.get(src, '#2563EB'),
                 fg='white', relief='flat', padx=8, pady=2,
                 font=('Microsoft YaHei', 9, 'bold')).pack(side='left', padx=(6, 2))
        # 箭头
        tk.Label(self.conv_hint_frame, text=" 转 ", bg='#FFFFFF', fg='#F59E0B',
                 font=('Microsoft YaHei', 10, 'bold')).pack(side='left')
        # 目标徽章
        tk.Label(self.conv_hint_frame, text=tgt, bg=COLOR.get(tgt, '#2563EB'),
                 fg='white', relief='flat', padx=8, pady=2,
                 font=('Microsoft YaHei', 9, 'bold')).pack(side='left', padx=(2, 0))

    def _is_projected_source(self):
        """源文件是否为投影米制（DXF/DWG），需要参考经纬度换算。"""
        if not self.source_path:
            return False
        ext = os.path.splitext(self.source_path)[1].lower()
        return ext in ('.dxf', '.dwg')

    def _update_ref_visibility(self):
        """当源为 DXF/DWG 且目标非 DXF 时，显示参考经纬度输入并标记必填。"""
        need = self._is_projected_source() and self.target_fmt.get() != "DXF"
        self._ref_needed = need
        if not hasattr(self, 'ref_frame'):
            return
        if need:
            self.ref_frame.grid()
        else:
            self.ref_frame.grid_remove()

    def _ref_select_all(self, event):
        """点击输入框时全选文本，便于一键更换新值。"""
        self.root.after(1, lambda: (self.ref_entry.select_range(0, tk.END),
                                    self.ref_entry.icursor(tk.END)))

    def _fill_zone_preset(self, zone):
        """点击 3° 带按钮，填入该带内预置经纬度。"""
        lat = 40.0
        presets = {
            "Z29": 87.0, "Z30": 90.0, "Z31": 93.0, "Z32": 96.0, "Z33": 99.0,
            "Z34": 102.0, "Z35": 105.0, "Z36": 108.0, "Z37": 111.0, "Z38": 114.0,
            "Z39": 117.0, "Z40": 120.0, "Z41": 123.0, "Z42": 126.0, "Z43": 129.0,
            "Z44": 132.0, "Z45": 135.0,
        }
        cm = presets.get(zone)
        if cm is None:
            return
        self.ref_lonlat.set(f"{cm:.6f}, {lat:.6f}")

    def clear_source(self):
        """取消选择：清除源文件状态，回到“＋点击选择”初始态。"""
        self.source_path = ""
        # 隐藏文件展示，恢复初始拖拽按钮
        self.file_display.pack_forget()
        self.drop_btn.pack(fill='x', ipady=6)
        # 重置进度、估算与状态
        self._watch_stop_task()
        self._bar_val = 0.0
        self._bar_target = 0.0
        self._bar_remaining = -1.0
        self._render_prog()
        self.est_var.set("估算大小：--")
        self._update_ref_visibility()
        self.status_text.set("已取消选择")
        self._update_conv_hint()

    def _read_reference(self):
        """解析参考经纬度（格式：经度,纬度）；非法时返回 None 并提示。"""
        raw = (self.ref_lonlat.get() or "").strip()
        parts = [p.strip() for p in raw.replace("，", ",").split(",")]
        parts = [p for p in parts if p]
        if len(parts) < 2:
            messagebox.showwarning(APP_NAME, "请按“经度,纬度”格式填写参考点，例如：86.082090, 44.280233。")
            return None
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            messagebox.showwarning(APP_NAME, "参考经纬度格式不正确，请输入两个数字，例如：86.082090, 44.280233。")
            return None
        if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
            messagebox.showwarning(APP_NAME, "参考经度应在 -180~180，纬度应在 -90~90。")
            return None
        return (lon, lat)

    # ------------------------------------------------------------- 状态栏
    # ------------------------------------------------------------- 逻辑
    def choose_source(self):
        filetypes = [
            ("所有支持的格式 (*.dxf;*.dwg;*.geojson;*.json;*.csv;*.txt;*.kml;*.gpx;*.osm;*.xml)",
             "*.dxf;*.dwg;*.geojson;*.json;*.csv;*.txt;*.kml;*.gpx;*.osm;*.xml"),
            ("CAD 格式 (*.dxf;*.dwg)", "*.dxf;*.dwg"),
            ("GeoJSON ( *.geojson )", "*.geojson"),
            ("JSON ( *.json )", "*.json"),
            ("KML ( *.kml )", "*.kml"),
            ("GPX ( *.gpx )", "*.gpx"),
            ("CSV 表格 ( *.csv )", "*.csv"),
            ("文本 ( *.txt )", "*.txt"),
            ("OSM ( *.osm )", "*.osm"),
            ("XML ( *.xml )", "*.xml"),
            ("所有文件 (*.*)", "*.*"),
        ]
        path = filedialog.askopenfilename(title="选择源文件", filetypes=filetypes)
        if path:
            self.set_source(path)

    def set_source(self, path):
        if not path or not os.path.isfile(path):
            messagebox.showwarning(APP_NAME,
                "无法识别的文件路径。\n"
                "拖拽/选择的文件不存在，或路径含空格被错误拆分。\n"
                "请改用“选择源文件”按钮从文件对话框选择，或重新拖拽一次。")
            return False
        ext = os.path.splitext(path)[1].lower()
        if ext not in SUPPORTED_SRC_EXTS:
            messagebox.showwarning(APP_NAME,
                f"不支持的文件类型：{ext or '（无扩展名）'}。\n"
                "请选择支持的源文件：DXF / DWG / GeoJSON / JSON / CSV / TXT / KML / GPX / OSM / XML。")
            return False
        self.source_path = path
        fname = os.path.basename(path)
        fmt_name = EXT_TO_FMT.get(ext, 'GeoJSON')

        # 立即切换为文件展示样式（先于估算，让界面马上刷新）
        self.file_name_lbl.config(text=fname)
        self.file_path_lbl.config(text=os.path.dirname(path))
        badge_txt = ext.lstrip('.').upper() if ext else 'FILE'
        self.file_badge.config(text=badge_txt, bg=COLOR.get(fmt_name, '#64748B'))
        self.drop_btn.pack_forget()
        self.file_display.pack(fill='x')

        if ext in EXT_TO_FMT:
            self.target_fmt.set(EXT_TO_FMT[ext])
            self._refresh_fmt()
        self.status_text.set(f"已选择：{fname}")
        self._update_ref_visibility()
        # 不自动估算：重置估算与进度条，由“开始估算”手动触发
        self._watch_stop_task()
        self._bar_val = 0.0
        self._bar_target = 0.0
        self._bar_remaining = -1.0
        self._render_prog()
        self.est_var.set("估算大小：--")
        self._update_conv_hint()

    def choose_outdir(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.out_dir = d
            self.out_entry.delete(0, tk.END)
            self.out_entry.insert(0, d)

    def apply_outdir(self):
        """应用手动输入的输出目录；若目录不存在则自动新建。"""
        raw = (self.out_entry.get() or "").strip()
        if not raw:
            messagebox.showwarning(APP_NAME, "请输入或选择输出目录。")
            return
        # 展开 ~ / 环境变量等相对表示
        raw = os.path.expandvars(os.path.expanduser(raw))
        # 若输入的是相对路径，则以用户主目录为基准解析
        if not os.path.isabs(raw):
            raw = os.path.join(os.path.expanduser("~"), raw)
        try:
            os.makedirs(raw, exist_ok=True)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"无法创建输出目录：\n{raw}\n{e}")
            return
        self.out_dir = raw
        self.out_entry.delete(0, tk.END)
        self.out_entry.insert(0, raw)
        self.status_text.set(f"输出目录：{raw}")

    def select_fmt(self, fmt):
        self.target_fmt.set(fmt)
        self._refresh_fmt()
        self._update_ref_visibility()
        # 不自动估算，仅重置估算结果，由“开始估算”手动触发
        self.est_var.set("估算大小：--")

    def _update_est(self):
        fmt = self.target_fmt.get()
        # 新估算启动，递增代数，使旧回调失效；估算进行中禁止重复触发
        if getattr(self, '_est_active', False) or self._running:
            return
        self._est_gen = getattr(self, '_est_gen', 0) + 1
        gen = self._est_gen

        if not self.source_path:
            self.est_var.set("估算大小：--")
            return

        # 进入“估算进行中”状态：复用卡片内进度条（与转换共用）
        self._est_active = True
        self.est_var.set("估算进行中，请稍后…")
        self.status_text.set("正在估算输出大小…")
        self._watch_start_task()   # 启动持续秒表（时间连续走动）

        src, outdir = self.source_path, self.out_dir or os.path.dirname(self.source_path)
        self._est_start = time.time()

        def worker():
            try:
                job = ConvertJob(src, outdir, fmt)
                data = job.load_only()
                size = job.estimated_size()
                total = data.total if data else 0
                ok, result = True, (size, total)
            except Exception as e:
                ok, result = False, str(e)
            self.root.after(0, lambda: self._finish_est(gen, ok, result))

        threading.Thread(target=worker, daemon=True).start()
        self._tick_est(gen)

    def _tick_est(self, gen):
        if gen != getattr(self, '_est_gen', 0):
            return
        if self._running or not getattr(self, '_est_active', False):
            return  # 转换已开始或估算已结束，不再驱动进度条
        elapsed = time.time() - self._est_start
        self._bar_val = min(92, self._bar_val + 1.0)   # 按 1% 平滑增长
        remain = elapsed * (100 - self._bar_val) / self._bar_val if self._bar_val > 1 else 0.0
        self._bar_remaining = remain
        self._render_prog()
        self.root.after(120, lambda: self._tick_est(gen))

    def _finish_est(self, gen, ok, result):
        if gen != getattr(self, '_est_gen', 0):
            return
        self._est_active = False
        self._watch_stop_task()
        self._bar_val = 100
        self._bar_target = 100
        self._bar_remaining = 0.0
        self._render_prog()
        if ok:
            size, total = result
            self.est_var.set(f"估算输出大小：≈ {human_size(size)}（{total} 个要素）")
            self.status_text.set(f"估算完成：≈ {human_size(size)}（{total} 个要素）")
        else:
            self.est_var.set(f"估算失败：{result}")
            self.status_text.set("估算失败")
        # 估算结束稍后复位进度条（若已开始转换则交给转换回调管理）
        self.root.after(700, lambda g=gen: self._reset_est_bar(g))

    def _reset_est_bar(self, gen):
        if gen != getattr(self, '_est_gen', 0):
            return
        if self._running:
            return
        self._bar_val = 0
        self._bar_target = 0
        self._bar_remaining = -1.0
        self._render_prog()
        # 状态文字由后续估算/转换覆盖

    # ------------------------------------------------------------- 持续秒表
    def _watch_start_task(self):
        """启动秒表：任务开始瞬间连续计时，进度条从 0 平滑起步。"""
        self._watch_start = time.time()
        self._watch_elapsed = 0.0
        self._watch_active = True
        self._bar_val = 0.0
        self._bar_target = 0.0
        self._bar_remaining = 0.0
        self._render_prog()
        self.root.after(100, self._watch_tick)

    def _watch_tick(self):
        if not self._watch_active:
            return
        # 转换期间进度事件稀疏：把进度条按 1% 向目标平滑补间，避免跳跃
        if self._running and not getattr(self, '_est_active', False):
            if self._bar_val < self._bar_target:
                self._bar_val = min(self._bar_target, self._bar_val + 1.0)
        self._render_prog()
        self.root.after(100, self._watch_tick)

    def _watch_stop_task(self):
        """停止秒表并记录最终用时。"""
        if self._watch_active:
            self._watch_elapsed = time.time() - self._watch_start
        self._watch_active = False

    def _render_prog(self):
        """按当前进度条状态重绘：百分比/时间/剩余 组合文字 + 填充几何。"""
        if self._watch_active:
            elapsed = time.time() - self._watch_start
        else:
            elapsed = self._watch_elapsed
        rem = self._bar_remaining
        if rem is not None and rem >= 0:
            text = f"{int(self._bar_val)}%  ｜  已用 {elapsed:.1f}s | 剩余 {rem:.1f}s"
        else:
            text = f"{int(self._bar_val)}%  ｜  已用 {elapsed:.1f}s | 剩余 --"
        self.prog_text_var.set(text)
        self.prog_canvas.itemconfig(self._base_text_item, text=text)
        self._ovl_canvas.itemconfig(self._ovl_text_item, text=text)
        # 更新填充矩形与覆盖层（按 1% 同步增长）
        W = self.prog_canvas.winfo_width()
        H = self.prog_canvas.winfo_height()
        if W < 4:
            W = 4
        if H < 4:
            H = 28
        fw = max(0, int(round(W * self._bar_val / 100.0)))
        self.prog_canvas.coords(self._fill_item, 0, 0, fw, H)
        self.prog_canvas.coords(self._base_text_item, W / 2.0, H / 2.0)
        self._ovl_canvas.configure(width=fw, height=H)
        self._ovl_canvas.coords(self._ovl_text_item, W / 2.0, H / 2.0)

    def start_conversion(self):
        # 双用按钮：转换中点击即取消
        if self._running:
            self.request_cancel()
            return
        if not self.source_path:
            messagebox.showwarning(APP_NAME, "请先选择源文件（DXF/DWG/GeoJSON/KML/CSV 等）。")
            return
        # 若估算仍在进行，立即取消估算并转接为正式转换
        if getattr(self, '_est_active', False):
            self._est_active = False
            self._est_gen = getattr(self, '_est_gen', 0) + 1  # 使待定估算回调失效
            self.est_var.set("取消估算，已开启转换")
        fmt = self.target_fmt.get()
        out_dir = self.out_dir or os.path.dirname(self.source_path)
        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showwarning(APP_NAME, "输出目录无效，请选择有效目录。")
            return

        # DXF/DWG → 其余格式：必须提供参考经纬度才能正确换算坐标
        ref_lon = ref_lat = None
        if self._is_projected_source() and fmt != "DXF":
            ref = self._read_reference()
            if ref is None:
                return
            ref_lon, ref_lat = ref

        self._running = True
        self._cancel_requested = False
        self._conv_gen = getattr(self, '_conv_gen', 0) + 1
        gen = self._conv_gen
        self.start_btn.config(state='normal', text="取消转换", bg='#DC2626',
                              activebackground='#B91C1C')
        self._watch_start_task()   # 秒表从转换开始连续计时
        job = ConvertJob(self.source_path, out_dir, fmt,
                         progress_cb=lambda p, m, e, r: self._on_progress(gen, p, m, e, r),
                         ref_lon=ref_lon, ref_lat=ref_lat)
        self.job = job

        def worker(gen=gen, job=job):
            try:
                path = job.run()
                self.root.after(0, lambda: self._on_done(gen, job, path))
            except Exception as e:
                err = e  # 提前绑定，避免 lambda 延迟引用 except 局部变量 e 时抛 NameError
                self.root.after(0, lambda _e=err: self._on_error(gen, _e))

        threading.Thread(target=worker, daemon=True).start()

    def request_cancel(self):
        """立即停止转换并释放内存。"""
        job = getattr(self, 'job', None)
        if job is not None:
            job.should_cancel = True       # 工作线程在检查点立即退出、不写盘
        self._cancel_requested = True
        self._running = False
        self.job = None                    # 释放 task 引用
        self.start_btn.config(state='normal', text="开始转换", bg='#2563EB',
                              activebackground='#1D4ED8')
        self._watch_stop_task()
        self._bar_val = 0
        self._bar_target = 0
        self._bar_remaining = -1.0
        self._render_prog()
        self.status_text.set("已取消转换")
        self._log.append("转换已取消")
        gc.collect()                       # 立即回收已加载的要素数据内存

    def _on_progress(self, gen, percent, message, elapsed, remaining):
        self.root.after(0, lambda: self._apply_progress(gen, percent, message, elapsed, remaining))

    def _apply_progress(self, gen, percent, message, elapsed, remaining):
        if gen != getattr(self, '_conv_gen', 0) or self._cancel_requested:
            return  # 旧任务或已取消的回调，忽略
        self._last_elapsed = elapsed
        self._last_remaining = remaining
        # 记为目标值，由秒表 tick 按 1% 平滑补间，避免跳跃
        self._bar_target = percent
        self._bar_remaining = remaining
        self.status_text.set(message)
        self._log.append(message)

    def _on_done(self, gen, job, path):
        if gen != getattr(self, '_conv_gen', 0) or self._cancel_requested:
            return  # 已取消或中途换了任务，忽略旧结果
        self._running = False
        self.job = None
        self.start_btn.config(state='normal', text="开始转换", bg='#2563EB',
                              activebackground='#1D4ED8')
        self._watch_stop_task()          # 停秒表，记录最终用时
        self._bar_val = 100
        self._bar_target = 100
        self._bar_remaining = self._last_remaining if self._last_remaining >= 0 else 0.0
        self._render_prog()
        self._done_count += 1
        self.queue_text.set(f"等待中: 0 | 转换中: 0 | 完成: {self._done_count}")
        self._log.append(f"完成：{path}")
        self.last_output = path
        self.status_text.set("转换完成")
        # 转换已完成解析，补上准确估算结果
        try:
            if job.data is not None:
                total = job.data.total
                self.est_var.set(f"估算输出大小：≈ {human_size(job.estimated_size())}（{total} 个要素）")
        except Exception:
            pass
        paths = getattr(job, 'output_paths', None) or ([path] if path else [])
        body = "转换完成！"
        if paths:
            body += "\n" + "\n".join(f"输出文件：{p}" for p in paths)
        messagebox.showinfo(APP_NAME, body)

    def _on_error(self, gen, e):
        if gen != getattr(self, '_conv_gen', 0) or self._cancel_requested:
            return  # 已取消，忽略
        self._running = False
        self.job = None
        self.start_btn.config(state='normal', text="开始转换", bg='#2563EB',
                              activebackground='#1D4ED8')
        self._watch_stop_task()          # 停秒表
        self._bar_val = 0
        self._bar_target = 0
        self._bar_remaining = -1.0
        self._render_prog()
        self.status_text.set("转换失败")
        self._log.append(f"错误：{e}")
        messagebox.showerror(APP_NAME, str(e))

    # ------------------------------------------------------------- 工具
    def open_map(self, path=None):
        path = path or getattr(self, 'last_output', '')
        if not path or not os.path.exists(path):
            messagebox.showinfo(APP_NAME, "尚无输出文件。请先完成一次转换。")
            return
        self._open_file_or_folder(path)

    def _open_file_or_folder(self, path):
        try:
            if os.name == 'nt':
                subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
            else:
                import webbrowser
                webbrowser.open('file://' + os.path.abspath(path))
        except Exception as e:
            self._log.append(f"打开失败：{e}")
            messagebox.showwarning(APP_NAME, f"无法打开：{e}")

    def open_outdir(self):
        d = self.out_dir or (os.path.dirname(self.source_path) if self.source_path else '')
        if not d or not os.path.isdir(d):
            messagebox.showinfo(APP_NAME, "尚未设置输出目录。")
            return
        try:
            if os.name == 'nt':
                subprocess.Popen(['explorer', os.path.normpath(d)])
        except Exception:
            pass

    def show_log(self):
        win = tk.Toplevel(self.root)
        win.title("转换日志")
        win.geometry("560x360")
        txt = tk.Text(win, wrap='word')
        txt.pack(fill='both', expand=True, padx=8, pady=8)
        for line in self._log:
            txt.insert(tk.END, line + "\n")
        txt.config(state='disabled')

    def reset(self):
        self.source_path = ""
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(desktop):
            desktop = os.path.expanduser("~")
        self.out_dir = desktop
        self.out_entry.delete(0, tk.END)
        self.out_entry.insert(0, desktop)
        # 恢复为“点击选择”区域，隐藏文件展示
        if hasattr(self, 'file_display') and hasattr(self, 'drop_btn'):
            self.file_display.pack_forget()
            self.drop_btn.pack(fill='x', ipady=18)
        self.select_fmt("GeoJSON")
        self._watch_stop_task()
        self._bar_val = 0
        self._bar_target = 0
        self._bar_remaining = -1.0
        self._render_prog()
        self.est_var.set("估算大小：--")
        self.status_text.set("就绪")
        self._log.append("已重置")

    # ------------------------------------------------------------- 拖拽
    def register_dnd(self):
        """在根窗口与正文上注册拖拽（需 TkinterDnD 根窗口）。"""
        try:
            from tkinterdnd2 import DND_FILES
            root = self.root
            root.drop_target_register(DND_FILES)
            root.dnd_bind('<<Drop>>', self._on_drop)
            if getattr(self, 'drop_zone', None):
                self.drop_zone.drop_target_register(DND_FILES)
                self.drop_zone.dnd_bind('<<Drop>>', self._on_drop)
            if getattr(self, 'body', None):
                try:
                    self.body.drop_target_register(DND_FILES)
                    self.body.dnd_bind('<<Drop>>', self._on_drop)
                except Exception:
                    pass
        except Exception as e:
            self._log.append(f"拖拽不可用：{e}")

    def _parse_drop_paths(self, data):
        """解析拖拽路径：tkdnd 对含空格的路径用 {} 包裹；未包裹且含空格的
        单个路径，整个字符串即一个文件路径，绝不能按空格拆分。"""
        data = (data or "").strip()
        if not data:
            return []
        if ' ' in data and not data.startswith('{'):
            return [data]
        try:
            return list(self.root.tk.splitlist(data))
        except Exception:
            return [data]

    def _on_drop(self, event):
        paths = self._parse_drop_paths(event.data)
        if paths:
            self.set_source(paths[0])


def main():
    # 若安装了 TkinterDnD，用其 Tk 根窗口以获得原生 Windows 拖拽支持
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    app = GeoForgeApp(root)
    app.register_dnd()

    # 记住窗口大小：关闭时保存；拖动缩放时防抖保存
    def _do_save():
        try:
            _save_geometry(root.geometry())
        finally:
            root._geo_save_pending = None

    def _on_resize(_e):
        if not getattr(root, '_geo_save_pending', None):
            root._geo_save_pending = root.after(500, _do_save)

    root.bind('<Configure>', _on_resize)
    root.protocol('WM_DELETE_WINDOW', lambda: (_do_save(), root.destroy()))
    root.mainloop()


if __name__ == '__main__':
    main()
