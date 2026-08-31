# -*- coding: utf-8 -*-
"""DXF / DWG 读取器。

- DXF 使用 ezdxf 完整解析（点/线/多段线/圆/圆弧/样条/文本/多段线/面域等）。
- DWG 优先尝试 ezdxf 的 ODA File Converter 桥接（需安装 ODA File Converter）；
  若不可用则报出明确错误提示。
"""
import os
import math
from typing import Optional

from .model import ConvertedData, Feature


def _pt(v) -> tuple:
    try:
        return (float(v[0]), float(v[1]))
    except Exception:
        return (0.0, 0.0)


def parse_dxf(doc, source_name: str, ext: str) -> ConvertedData:
    data = ConvertedData(source_name=source_name, source_ext=ext)
    msp = doc.modelspace()

    minx, miny, maxx, maxy = 1e18, 1e18, -1e18, -1e18

    def upd(pt):
        nonlocal minx, miny, maxx, maxy
        if pt[0] < minx: minx = pt[0]
        if pt[0] > maxx: maxx = pt[0]
        if pt[1] < miny: miny = pt[1]
        if pt[1] > maxy: maxy = pt[1]

    for e in msp:
        etype = e.dxftype()
        layer = getattr(e.dxf, 'layer', "0")
        color = getattr(getattr(e, 'dxf', None), 'color', None)

        # ---- 点 ----
        if etype == 'POINT':
            pt = _pt(e.dxf.location)
            upd(pt)
            data.features.append(Feature('Point', list(pt), {'entity': etype, 'layer': layer, 'color': color}, layer, etype))
            data.count_point += 1

        # ---- 文本 / 多行文本 / 属性 ----
        elif etype in ('TEXT', 'MTEXT', 'ATTRIB'):
            pos = _pt(e.dxf.insert if etype != 'MTEXT' else e.dxf.insert)
            text = str(getattr(e.dxf, 'text', "") or "")
            upd(pos)
            data.features.append(Feature('Point', list(pos), {'entity': etype, 'layer': layer, 'text': text, 'color': color}, layer, etype))
            data.count_point += 1

        # ---- 块引用 INSERT（展开块内实体，避免块丢失）----
        elif etype == 'INSERT':
            try:
                ip = _pt(e.dxf.insert)
                rot = float(getattr(getattr(e, 'dxf', None), 'rotation', 0.0) or 0.0)
                sx = float(getattr(getattr(e, 'dxf', None), 'xscale', 1.0) or 1.0)
                sy = float(getattr(getattr(e, 'dxf', None), 'yscale', 1.0) or 1.0)
                block = e.block()
                for be in block:
                    _emit_entity(be, data, upd, ip, rot, sx, sy, depth=0)
            except Exception:
                pass

        # ---- 线 ----
        elif etype == 'LINE':
            a = _pt(e.dxf.start); b = _pt(e.dxf.end)
            upd(a); upd(b)
            data.features.append(Feature('LineString', [list(a), list(b)], {'entity': etype, 'layer': layer, 'color': color}, layer, etype))
            data.count_line += 1

        elif etype in ('LWPOLYLINE', 'POLYLINE'):
            pts = []
            if etype == 'LWPOLYLINE':
                for p in e.get_points('xy'):
                    pts.append((p[0], p[1]))
            else:
                for v in e.vertices:
                    pts.append((v.dxf.location.x, v.dxf.location.y))
            if len(pts) < 2:
                continue
            for p in pts: upd(p)
            closed = 0
            try:
                if etype == 'POLYLINE':
                    closed = int(e.is_closed)
                else:
                    closed = int(e.closed)
            except Exception:
                closed = 0
            if closed and pts[0] != pts[-1]:
                pts.append(pts[0])
            if closed:
                data.features.append(Feature('Polygon', [ [list(p) for p in pts] ], {'entity': etype, 'layer': layer, 'color': color}, layer, etype))
                data.count_polygon += 1
            else:
                data.features.append(Feature('LineString', [list(p) for p in pts], {'entity': etype, 'layer': layer, 'color': color}, layer, etype))
                data.count_line += 1

        # ---- 圆 / 圆弧 ----
        elif etype == 'CIRCLE':
            c = _pt(e.dxf.center)
            r = float(getattr(e.dxf, 'radius', 0.0) or 0.0)
            upd((c[0] - r, c[1] - r)); upd((c[0] + r, c[1] + r))
            # 用多边形近似圆
            pts = _arc_points(c, r, 0.0, 360.0, 72)
            data.features.append(Feature('Polygon', [pts], {'entity': etype, 'layer': layer, 'color': color, 'radius': r}, layer, etype))
            data.count_polygon += 1

        elif etype == 'ARC':
            c = _pt(e.dxf.center)
            r = float(getattr(e.dxf, 'radius', 0.0) or 0.0)
            a1 = float(getattr(e.dxf, 'start_angle', 0.0) or 0.0)
            a2 = float(getattr(e.dxf, 'end_angle', 360.0) or 360.0)
            upd((c[0] - r, c[1] - r)); upd((c[0] + r, c[1] + r))
            pts = _arc_points(c, r, a1, a2, 64)
            data.features.append(Feature('LineString', pts, {'entity': etype, 'layer': layer, 'color': color}, layer, etype))
            data.count_line += 1

        # ---- 样条 ----
        elif etype == 'SPLINE':
            pts = []
            if hasattr(e, 'control_points'):
                for p in e.control_points:
                    pts.append((p.x, p.y))
            else:
                for p in e.control_points:
                    pts.append((p[0], p[1]))
            if len(pts) < 2:
                continue
            for p in pts: upd(p)
            try:
                closed = int(e.closed)
            except Exception:
                closed = 0
            if closed and pts[0] != pts[-1]:
                pts.append(pts[0])
            if closed:
                data.features.append(Feature('Polygon', [ [list(p) for p in pts] ], {'entity': etype, 'layer': layer, 'color': color}, layer, etype))
                data.count_polygon += 1
            else:
                data.features.append(Feature('LineString', [list(p) for p in pts], {'entity': etype, 'layer': layer, 'color': color}, layer, etype))
                data.count_line += 1

        # ---- 面域 / 实体填充（用外环近似） ----
        elif etype == 'HATCH':
            try:
                for path in e.paths:
                    if hasattr(path, 'vertices'):
                        pts = []
                        for v in path.vertices:
                            pts.append((v[0], v[1]))
                        if len(pts) < 3:
                            continue
                        for p in pts: upd(p)
                        data.features.append(Feature('Polygon', [pts], {'entity': etype, 'layer': layer, 'color': color}, layer, etype))
                        data.count_polygon += 1
            except Exception:
                pass

    if data.features:
        data.bounds = (minx, miny, maxx, maxy)
    return data


def _emit_entity(be, data, upd, ip, rot, sx, sy, depth=0):
    """把块内实体按 INSERT 变换（平移/旋转/缩放）写入要素列表。"""
    if depth > 16:
        return
    etype = be.dxftype()
    layer = getattr(be.dxf, 'layer', "0")
    color = getattr(getattr(be, 'dxf', None), 'color', None)

    def tr(p):
        x, y = p[0] * sx, p[1] * sy
        if rot:
            r = math.radians(rot)
            c, s = math.cos(r), math.sin(r)
            x, y = x * c - y * s, x * s + y * c
        return (ip[0] + x, ip[1] + y)

    try:
        if etype == 'POINT':
            pt = tr(_pt(be.dxf.location))
            upd(pt)
            data.features.append(Feature('Point', list(pt), {'entity': etype, 'layer': layer, 'color': color}, layer, etype))
            data.count_point += 1

        elif etype in ('TEXT', 'MTEXT'):
            pos = tr(_pt(be.dxf.insert))
            text = str(getattr(be.dxf, 'text', "") or "")
            upd(pos)
            data.features.append(Feature('Point', list(pos), {'entity': etype, 'layer': layer, 'text': text, 'color': color}, layer, etype))
            data.count_point += 1

        elif etype == 'LINE':
            a = tr(_pt(be.dxf.start)); b = tr(_pt(be.dxf.end))
            upd(a); upd(b)
            data.features.append(Feature('LineString', [list(a), list(b)], {'entity': etype, 'layer': layer, 'color': color}, layer, etype))
            data.count_line += 1

        elif etype in ('LWPOLYLINE', 'POLYLINE'):
            pts = []
            if etype == 'LWPOLYLINE':
                pts = [tr((p[0], p[1])) for p in be.get_points('xy')]
            else:
                pts = [tr((v.dxf.location.x, v.dxf.location.y)) for v in be.vertices]
            if len(pts) < 2:
                return
            for p in pts: upd(p)
            closed = 0
            try:
                closed = int(be.closed if etype == 'LWPOLYLINE' else be.is_closed)
            except Exception:
                closed = 0
            if closed and pts[0] != pts[-1]:
                pts.append(pts[0])
            if closed:
                data.features.append(Feature('Polygon', [[list(p) for p in pts]], {'entity': etype, 'layer': layer, 'color': color}, layer, etype))
                data.count_polygon += 1
            else:
                data.features.append(Feature('LineString', [list(p) for p in pts], {'entity': etype, 'layer': layer, 'color': color}, layer, etype))
                data.count_line += 1

        elif etype == 'CIRCLE':
            c = tr(_pt(be.dxf.center))
            r = abs(float(getattr(be.dxf, 'radius', 0.0) or 0.0)) * sx
            upd((c[0] - r, c[1] - r)); upd((c[0] + r, c[1] + r))
            pts = _arc_points(c, r, 0.0, 360.0, 72)
            data.features.append(Feature('Polygon', [pts], {'entity': etype, 'layer': layer, 'color': color, 'radius': r}, layer, etype))
            data.count_polygon += 1

        elif etype == 'ARC':
            c = tr(_pt(be.dxf.center))
            r = abs(float(getattr(be.dxf, 'radius', 0.0) or 0.0)) * sx
            a1 = float(getattr(be.dxf, 'start_angle', 0.0) or 0.0)
            a2 = float(getattr(be.dxf, 'end_angle', 360.0) or 360.0)
            upd((c[0] - r, c[1] - r)); upd((c[0] + r, c[1] + r))
            pts = _arc_points(c, r, a1, a2, 64)
            data.features.append(Feature('LineString', pts, {'entity': etype, 'layer': layer, 'color': color}, layer, etype))
            data.count_line += 1

        elif etype == 'SPLINE':
            pts = [tr((float(p.x), float(p.y))) for p in be.control_points]
            if len(pts) >= 2:
                for p in pts: upd(p)
                data.features.append(Feature('LineString', [list(p) for p in pts], {'entity': etype, 'layer': layer, 'color': color}, layer, etype))
                data.count_line += 1

        elif etype == 'INSERT':
            block = be.block()
            ni = _pt(be.dxf.insert)
            nrot = rot + float(getattr(getattr(be, 'dxf', None), 'rotation', 0.0) or 0.0)
            for sub in block:
                _emit_entity(sub, data, upd, (ip[0] + ni[0] * sx, ip[1] + ni[1] * sy), nrot, sx, sy, depth + 1)
    except Exception:
        pass


def _arc_points(center, radius, start_deg, end_deg, segments=72):
    if end_deg < start_deg:
        end_deg += 360.0
    pts = []
    for i in range(segments + 1):
        ang = math.radians(start_deg + (end_deg - start_deg) * i / segments)
        pts.append([center[0] + radius * math.cos(ang), center[1] + radius * math.sin(ang)])
    return pts


def load_dwg(path: str, source_name: str):
    """使用 ezdxf ODA 桥接读取 DWG。若无 ODA 则抛 ValueError。"""
    try:
        from ezdxf.addons import odafc
        if not odafc.is_installed():
            raise ValueError("未检测到 ODA File Converter，无法读取 DWG。\n"
                             "DWG 需借助外部转换器（ODA File Converter）解析，它无法随本软件打包集成，"
                             "请自行免费下载安装后重试。\n"
                             "下载地址：https://www.opendesign.com/guestfiles/oda_file_converter\n"
                             "安装后把 'ODAFileConverter' 所在目录加入系统 PATH（重启本软件生效）。")
        doc = odafc.readfile(path)
        return parse_dxf(doc, source_name, '.dwg')
    except ImportError:
        raise ValueError("缺少 ezdxf.odafc 支持。")
    except ValueError as ve:
        raise ve
    except Exception as e:
        raise ValueError(f"DWG 读取失败：{e}")


def load_file(path: str) -> ConvertedData:
    ext = os.path.splitext(path)[1].lower()
    base = os.path.basename(path)
    if ext in ('.dxf', '.dwg'):
        if ext == '.dwg':
            return load_dwg(path, base)
        import ezdxf
        try:
            doc = ezdxf.readfile(path)
        except Exception as e:
            raise ValueError(f"DXF 读取失败（可能是二进制/损坏文件）：{e}")
        return parse_dxf(doc, base, ext)
    else:
        from .readgeo import read_file as read_geo
        return read_geo(path)
