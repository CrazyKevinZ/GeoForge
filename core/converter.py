# -*- coding: utf-8 -*-
"""转换编排器。

步骤化转换 + 进度回调：
  1) 解析源文件（约 40%）
  2) 导出目标格式（约 40%）
  3) 写盘（约 20%）
进度回调接收 (percent, message) 便于 UI 更新。
"""
import os
import time

from .reader import load_file
from .exporter import export_format, format_ext, MAP_LOADABLE, FORMATS
from .estimator import estimate_size


def _is_projected(path):
    """是否投影米制源（DXF/DWG）。"""
    ext = os.path.splitext(path)[1].lower()
    return ext in ('.dxf', '.dwg')


class ProgressReporter:
    def __init__(self, cb):
        self.cb = cb or (lambda p, m, e, r: None)
        self._start = None

    def start(self):
        self._start = time.time()

    def report(self, percent, message):
        elapsed = (time.time() - self._start) if self._start else 0.0
        if percent > 0:
            remaining = elapsed * (100.0 - percent) / percent
        else:
            remaining = 0.0
        self.cb(percent, message, elapsed, remaining)


class ConvertJob:
    """一个源文件的完整转换任务。"""

    def __init__(self, source_path: str, out_dir: str, fmt: str, progress_cb=None,
                 ref_lon: float = None, ref_lat: float = None):
        self.source_path = source_path
        self.out_dir = out_dir
        self.fmt = fmt
        self.ref_lon = ref_lon
        self.ref_lat = ref_lat
        self.progress = ProgressReporter(progress_cb)
        self.result_path = ""
        self.output_paths = []   # 本次写入的所有输出文件完整路径（含 (OV)TXT 的双文件）
        self.data = None
        self.should_cancel = False

    def _reproject(self):
        """DXF/DWG（投影米制）且提供了参考经纬度时，换算为 WGS84 经纬度。"""
        if _is_projected(self.source_path):
            if self.ref_lon is None or self.ref_lat is None:
                return
            from . import proj
            proj.transform_data(self.data, self.ref_lon, self.ref_lat)

    @property
    def base_name(self) -> str:
        return os.path.splitext(os.path.basename(self.source_path))[0]

    def output_path(self) -> str:
        ext = format_ext(self.fmt)
        # 避免覆盖：若已存在则加序号
        path = os.path.join(self.out_dir, self.base_name + ext)
        n = 1
        while os.path.exists(path):
            path = os.path.join(self.out_dir, f"{self.base_name}_{n}{ext}")
            n += 1
        return path

    def _dual_paths(self, suf_ks: str, suf_ov: str, ext: str):
        """为 TXT 组合输出挑选互不冲突的两个文件名：base{suf_ks}{ext} 与 base{suf_ov}{ext}。"""
        base = self.base_name
        n = 1
        while True:
            ks = os.path.join(self.out_dir, f"{base}{suf_ks}{ext}")
            ov = os.path.join(self.out_dir, f"{base}{suf_ov}{ext}")
            if not os.path.exists(ks) and not os.path.exists(ov):
                return ks, ov
            base = f"{self.base_name}_{n}"
            n += 1

    def estimated_size(self) -> int:
        if self.data is None:
            return 0
        return estimate_size(self.data, self.fmt)

    def run(self):
        """执行转换，返回输出路径。"""
        self.progress.start()
        self.progress.report(2, "读取源文件…")
        self.data = load_file(self.source_path)
        if self.should_cancel:
            return ""
        if self.data.total == 0:
            raise ValueError(
                "所选文件没有可转换的有效坐标数据（0 个要素），已取消转换。\n"
                "请确认该文件是包含坐标的 DXF/GeoJSON/KML/GPX/CSV/TXT 等文件，或先修复数据后再试。")
        self._reproject()
        if self.should_cancel:
            return ""
        self.progress.report(45, f"解析完成，共 {self.data.total} 个要素，正在生成 {self.fmt}…")

        if self.fmt == "TXT":
            # (OV)TXT：一次生成 kistom(KS) 与 奥维(OV) 两个 TXT 文件
            self.progress.report(85, "写入 KS.txt 与 OV.txt …")
            ks = export_format(self.data, "TXT", self.source_path)
            ov = export_format(self.data, "OVTXT", self.source_path)
            pks, pov = self._dual_paths("KS", "OV", ".txt")
            self.result_path = pks
            self.output_paths = [pks, pov]
            with open(pks, 'wb') as fh:
                fh.write(ks)
            with open(pov, 'wb') as fh:
                fh.write(ov)
            if self.should_cancel:
                for p in (pks, pov):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                return ""
            self.progress.report(100, "完成")
            return self.result_path

        out_bytes = export_format(self.data, self.fmt, self.source_path)
        if self.should_cancel:
            return ""
        self.progress.report(85, "写入文件…")

        self.result_path = self.output_path()
        self.output_paths = [self.result_path]
        with open(self.result_path, 'wb') as fh:
            fh.write(out_bytes)
        if self.should_cancel:
            try:
                os.remove(self.result_path)
            except OSError:
                pass
            return ""
        self.progress.report(100, "完成")
        return self.result_path

    def load_only(self):
        """仅解析（用于估算大小），不写盘。"""
        self.data = load_file(self.source_path)
        if self.data.total == 0:
            raise ValueError(
                "所选文件没有可转换的有效坐标数据（0 个要素）。\n"
                "请确认该文件是包含坐标的 DXF/GeoJSON/KML/GPX/CSV/TXT 等文件。")
        self._reproject()
        return self.data


def can_load_in_map(fmt: str) -> bool:
    return fmt in MAP_LOADABLE
