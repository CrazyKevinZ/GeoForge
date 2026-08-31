# -*- coding: utf-8 -*-
"""输出文件大小估算器。

在真正转换之前，根据特征数量、坐标点数量与目标格式特征，
估算输出文件的大致字节数。
"""
from .model import ConvertedData


def _count_points(data: ConvertedData) -> int:
    from .exporter import _flatten_coordinates
    n = 0
    for f in data.features:
        if f.geom_type == 'Point':
            n += 1
        else:
            n += len(_flatten_coordinates(f.coordinates))
    return max(n, 1)


def _base_per_feature(fmt: str) -> float:
    """每种格式每特征的基础字节成本。"""
    base = {
        "GeoJSON": 120, "JSON": 90, "CSV": 45, "TXT": 55, "OVTXT": 30,
        "KML": 200, "OVKML": 200, "GPX": 150, "DXF": 130, "OSM": 220, "XML": 180,
    }
    return base.get(fmt, 100)


def _per_point(fmt: str) -> float:
    pp = {
        "GeoJSON": 40, "JSON": 35, "CSV": 30, "TXT": 40, "OVTXT": 25,
        "KML": 60, "OVKML": 60, "GPX": 60, "DXF": 50, "OSM": 75, "XML": 65,
    }
    return pp.get(fmt, 40)


def estimate_size(data: ConvertedData, fmt: str) -> int:
    """返回估算字节数。"""
    if data.total == 0:
        return 0
    pts = _count_points(data)
    est = _base_per_feature(fmt) * data.total + _per_point(fmt) * pts
    return int(est)


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"
