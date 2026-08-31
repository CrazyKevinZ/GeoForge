# -*- coding: utf-8 -*-
"""数据模型：从 DXF/DWG 中提取的统一中间数据。

所有格式的导出都基于此统一的几何/属性模型，
从而避免每种格式重复解析逻辑。
"""
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any

GeoPoint = Tuple[float, float]


@dataclass
class Feature:
    """一个地理要素（点 / 线 / 面）。"""
    geom_type: str            # 'Point' | 'LineString' | 'Polygon' | 'MultiPoint' | 'MultiLineString' | 'MultiPolygon'
    coordinates: Any          # 与 GeoJSON 兼容的坐标嵌套结构
    properties: Dict[str, Any] = field(default_factory=dict)
    layer: str = "0"
    entity_type: str = ""


@dataclass
class ConvertedData:
    """一次性解析后的内存模型，供所有导出器复用。"""
    features: List[Feature] = field(default_factory=list)
    count_point: int = 0
    count_line: int = 0
    count_polygon: int = 0
    units: str = "unit"       # 单位（dxf 通常无单位）
    bounds: Tuple[float, float, float, float] = (-180.0, -90.0, 180.0, 90.0)
    source_name: str = ""
    source_ext: str = ""

    @property
    def total(self) -> int:
        return len(self.features)
