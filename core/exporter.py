# -*- coding: utf-8 -*-
"""各格式导出器。

将内存模型 (ConvertedData) 写成目标格式文本。
返回 bytes，便于统一调用方计算进度与写盘。
"""
import json
import os
import xml.etree.ElementTree as ET
import math

from .model import ConvertedData, Feature


def _coords_to_geojson(coordinates):
    """转为 json 可序列化结构。"""
    if isinstance(coordinates, (list, tuple)):
        return [_coords_to_geojson(c) for c in coordinates]
    return coordinates


def _arange(v, sig=6):
    """整数化坐标可读。"""
    if float(v).is_integer():
        return int(v)
    return round(float(v), sig)


def _props_for(f, line_no=None):
    """生成要素属性；为带文字的点标记 label，便于地图预览渲染文字；线/面加编号名称。"""
    p = dict(f.properties or {})
    if f.geom_type == 'Point' and (p.get('text') or p.get('text') == ''):
        p.setdefault('label', True)
    if f.geom_type in ('LineString', 'Polygon'):
        layer = f.layer or ''
        p.setdefault('name', f"{layer} #{line_no}" if line_no is not None else (layer or 'line'))
    return p


# ---------------------------------------------------------------- GeoJSON
def export_geojson(data: ConvertedData) -> bytes:
    feats = []
    line_no = 0
    for f in data.features:
        if f.geom_type in ('LineString', 'Polygon'):
            line_no += 1
        feats.append({
            "type": "Feature",
            "properties": _props_for(f, line_no),
            "geometry": {
                "type": f.geom_type,
                "coordinates": _coords_to_geojson(f.coordinates),
            }
        })
    obj = {
        "type": "FeatureCollection",
        "name": data.source_name,
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4326"}},
        "features": feats,
    }
    return json.dumps(obj, ensure_ascii=False, indent=2).encode('utf-8')


# ---------------------------------------------------------------- JSON(通用，输出标准 GeoJSON 以便地图预览加载)
def export_json(data: ConvertedData) -> bytes:
    obj = {
        "type": "FeatureCollection",
        "name": data.source_name,
        "note": "GeoForge 通用 JSON 导出（标准 GeoJSON，兼容 kistom 预览）",
    }
    feats = []
    line_no = 0
    for f in data.features:
        if f.geom_type in ('LineString', 'Polygon'):
            line_no += 1
        props = _props_for(f, line_no)
        props['layer'] = f.layer
        props['entity'] = f.entity_type
        feats.append({
            "type": "Feature",
            "properties": props,
            "geometry": {
                "type": f.geom_type,
                "coordinates": _coords_to_geojson(f.coordinates),
            },
        })
    obj["features"] = feats
    return json.dumps(obj, ensure_ascii=False, indent=2).encode('utf-8')


# ---------------------------------------------------------------- CSV（lon/lat 列，兼容 kistom 预览；空行分隔不同要素）
def export_csv(data: ConvertedData) -> bytes:
    import csv
    import io
    header = ["longitude", "latitude", "type", "layer", "entity", "properties"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    line_no = 0
    for f in data.features:
        if f.geom_type in ('LineString', 'Polygon'):
            line_no += 1
        flat = _flatten_coordinates(f.coordinates)
        if not flat:
            flat = [f.coordinates]
        prop = dict(f.properties or {})
        if f.geom_type in ('LineString', 'Polygon'):
            layer = f.layer or ''
            prop['name'] = f"{layer} #{line_no}"
        if f.geom_type == 'Point' and (prop.get('text') or prop.get('text') == ''):
            prop['label'] = True
        prop_str = json.dumps(prop, ensure_ascii=False)
        for i, (x, y) in enumerate(flat):
            w.writerow([_arange(x), _arange(y), f.geom_type, f.layer, f.entity_type, prop_str])
        w.writerow([])   # 空行：kistom 据此中断为独立要素
    return buf.getvalue().encode('utf-8-sig')


def _flatten_coordinates(coords, out=None):
    if out is None:
        out = []
    if isinstance(coords, (list, tuple)):
        if len(coords) >= 2 and all(not isinstance(c, (list, tuple)) for c in coords):
            out.append((coords[0], coords[1]))
        else:
            for c in coords:
                _flatten_coordinates(c, out)
    return out


# ---------------------------------------------------------------- TXT（lon/lat 表，兼容 kistom 预览；# 行作注释，空行分隔要素）
def export_txt(data: ConvertedData) -> bytes:
    lines = []
    lines.append(f"# GeoForge 导出（文本表）：{data.source_name}")
    lines.append(f"# 要素统计: 点 {data.count_point} / 线 {data.count_line} / 面 {data.count_polygon} / 合计 {data.total}")
    lines.append("# 坐标系: WGS84 经纬度 [longitude, latitude]；空行分隔不同要素")
    lines.append("longitude; latitude; type; layer; entity; properties")
    line_no = 0
    for f in data.features:
        if f.geom_type in ('LineString', 'Polygon'):
            line_no += 1
        flat = _flatten_coordinates(f.coordinates)
        if not flat:
            flat = [f.coordinates]
        _layer = (f.layer or '')
        _entity = (f.entity_type or '')
        _prop = dict(f.properties or {})
        if f.geom_type in ('LineString', 'Polygon'):
            _prop['name'] = f"{_layer} #{line_no}"
        if f.geom_type == 'Point' and (_prop.get('text') or _prop.get('text') == ''):
            _prop['label'] = True
        _prop = json.dumps(_prop, ensure_ascii=False)
        for x, y in flat:
            lines.append(f"{_arange(x):.6f}; {_arange(y):.6f}; {f.geom_type}; {_layer}; {_entity}; {_prop}")
        lines.append("")   # 空行：kistom 据此中断为独立要素
    text = "\r\n".join(lines) + "\r\n"
    return text.encode('utf-8')


# ---------------------------------------------------------------- OVTXT（奥维互动地图 标签/点 TXT）
def export_ovtxt(data: ConvertedData) -> bytes:
    """奥维互动地图 TXT：每行「名称 经度 纬度」（单空格分隔，经度在前、纬度在后）。
    奥维 TXT 只能导入点/标签，无法由 TXT 生成连通线；如需连通线请用 OVKML。"""
    lines = []
    line_no = 0
    for f in data.features:
        if f.geom_type in ('LineString', 'Polygon'):
            line_no += 1
        layer = f.layer or ''
        txt = (f.properties or {}).get('text')
        if f.geom_type == 'Point':
            name = str(txt) if txt else (layer or str(f.entity_type or '点'))
            lines.append(f"{name} {_arange(f.coordinates[0])} {_arange(f.coordinates[1])}")
        else:
            flat = _flatten_coordinates(f.coordinates) or [f.coordinates]
            name = f"{layer} #{line_no}"
            for x, y in flat:
                lines.append(f"{name} {_arange(x)} {_arange(y)}")
    text = "\r\n".join(lines) + "\r\n"
    return text.encode('utf-8')


# ---------------------------------------------------------------- OVKML（奥维互动地图 KML）
def export_ovkml(data: ConvertedData) -> bytes:
    """奥维互动地图 KML：WGS84 经度/纬度/0，每个对象带名称（线/面含编号）。"""
    return _export_kml_body(data, apply_style=True)


# ---------------------------------------------------------------- KML
def export_kml(data: ConvertedData) -> bytes:
    return _export_kml_body(data, apply_style=True)


# ---------------------------------------------------------------- GPX
def export_gpx(data: ConvertedData) -> bytes:
    ns = {
        '': 'http://www.topografix.com/GPX/1/1',
        'gpxtpx': 'http://www.garmin.com/xmlschemas/TrackPointExtension/v1',
    }
    gpx = ET.Element('gpx', version='1.1', creator='GeoForge',
                     xmlns='http://www.topografix.com/GPX/1/1',
                     xmlns_gpxtpx='http://www.garmin.com/xmlschemas/TrackPointExtension/v1')

    # 点 → waypoint
    waypoints = [f for f in data.features if f.geom_type == 'Point']
    wpts = [f for f in data.features if f.geom_type == 'Point']
    for f in waypoints:
        w = ET.SubElement(gpx, 'wpt', lat=str(f.coordinates[1]), lon=str(f.coordinates[0]))
        name = ET.SubElement(w, 'name')
        name.text = f.properties.get('text', f.entity_type)
        ET.SubElement(w, 'layer').text = f.layer
        ET.SubElement(w, 'entity').text = f.entity_type

    # 线/面 → track / route
    lines = [f for f in data.features if f.geom_type in ('LineString', 'Polygon')]
    for k, f in enumerate(lines, 1):
        trk = ET.SubElement(gpx, 'trk')
        nm = ET.SubElement(trk, 'name')
        nm.text = f"{f.entity_type} {f.layer} #{k}"
        trkseg = ET.SubElement(trk, 'trkseg')
        flat = _flatten_coordinates(f.coordinates)
        for x, y in flat:
            tp = ET.SubElement(trkseg, 'trkpt', lat=str(y), lon=str(x))
            ET.SubElement(tp, 'name').text = f.layer

    ET.indent(gpx, space='  ')
    return ET.tostring(gpx, encoding='utf-8', xml_declaration=True)


# ---------------------------------------------------------------- DXF(重导出)
def export_dxf(data: ConvertedData, original_path: str) -> bytes:
    """输出 DXF：优先原样复制源文件；无源文件则重建一个精简 DXF。"""
    if original_path and os.path.exists(original_path) and os.path.splitext(original_path)[1].lower() == '.dxf':
        with open(original_path, 'rb') as fh:
            return fh.read()

    import ezdxf
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    for f in data.features:
        layer = f.layer if f.layer else '0'
        if layer not in doc.layers:
            doc.layers.add(layer)
        if f.geom_type == 'Point':
            try:
                x, y = f.coordinates[0], f.coordinates[1]
            except (TypeError, IndexError):
                continue
            msp.add_point((float(x), float(y)), dxfattribs={'layer': layer})
        elif f.geom_type == 'LineString':
            pts = _clean_ring(f.coordinates)
            if len(pts) == 2:
                msp.add_line(pts[0], pts[1], dxfattribs={'layer': layer})
            elif len(pts) > 2:
                msp.add_lwpolyline(pts, dxfattribs={'layer': layer})
        elif f.geom_type == 'Polygon':
            ring = f.coordinates[0] if f.coordinates else []
            pts = _clean_ring(ring)
            if len(pts) < 3:
                continue
            circ = _detect_circle(pts)
            if circ:
                msp.add_circle((circ[0], circ[1]), circ[2], dxfattribs={'layer': layer})
            else:
                msp.add_lwpolyline(pts, close=True, dxfattribs={'layer': layer})
    # 用 StringIO 序列化
    import io
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode('utf-8')


def _clean_ring(coords):
    """返回 [(x, y), ...]，去掉末尾与起点重合的闭合点并转为二元组。"""
    out = []
    for p in coords:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        out.append((float(p[0]), float(p[1])))
    if len(out) >= 2:
        ax, ay = out[0]
        bx, by = out[-1]
        if abs(ax - bx) < 1e-9 and abs(ay - by) < 1e-9:
            out = out[:-1]
    return out


def _detect_circle(pts, tol=2e-3):
    """若各点到中心的距离几乎相等则视为圆，返回 (cx, cy, r)。"""
    n = len(pts)
    if n < 8:
        return None
    import math as _m
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    rs = [_m.hypot(p[0] - cx, p[1] - cy) for p in pts]
    r0 = rs[0]
    if r0 <= 0:
        return None
    spread = (max(rs) - min(rs)) / r0
    if spread > tol:
        return None
    return (cx, cy, r0)


# ---------------------------------------------------------------- OSM
def export_osm(data: ConvertedData) -> bytes:
    osm = ET.Element('osm', version='0.6', generator='GeoForge')
    bounds = data.bounds or (-180.0, -90.0, 180.0, 90.0)
    ET.SubElement(osm, 'bounds', minlat=str(bounds[1]), minlon=str(bounds[0]),
                  maxlat=str(bounds[3]), maxlon=str(bounds[2]))

    node_id = 1
    way_id = 1
    way_name_idx = 0
    # JOSM 等编辑器要求每个对象带 version 等元数据属性，否则报错
    _jattrs = {'version': '1', 'timestamp': '2025-01-01T00:00:00Z',
               'changeset': '1', 'user': 'GeoForge', 'uid': '1'}
    nodes = {}      # (x,y) -> id
    node_elems = {}  # (x,y) -> Element

    def ensure_node(x, y):
        nonlocal node_id
        key = (round(float(x), 6), round(float(y), 6))
        if key in nodes:
            return nodes[key]
        nid = node_id
        node_id += 1
        nodes[key] = nid
        elem = ET.SubElement(osm, 'node',
                             id=str(nid), lat=str(key[1]), lon=str(key[0]), **_jattrs)
        node_elems[key] = elem
        return nid

    for f in data.features:
        if f.geom_type == 'Point':
            coords = f.coordinates
            if not isinstance(coords, (list, tuple)) or len(coords) < 2:
                continue
            key = (round(float(coords[0]), 6), round(float(coords[1]), 6))
            try:
                nid = ensure_node(coords[0], coords[1])
                nd = node_elems[key]
            except (TypeError, ValueError, KeyError):
                continue
            if nd is None:
                continue
            props = dict(f.properties or {})
            props['layer'] = f.layer
            for k, v in props.items():
                ET.SubElement(nd, 'tag', k=str(k)[:48], v=str(v)[:248])
        else:
            flat = _flatten_coordinates(f.coordinates)
            if len(flat) < 2:
                continue
            refs = []
            last = None
            for x, y in flat:
                nid = ensure_node(x, y)
                if nid != last:          # 去重相邻同点，避免退化线段
                    refs.append(nid)
                    last = nid
            if len(refs) < 2:
                continue
            wid = way_id
            way_id += 1
            way_name_idx += 1
            w = ET.SubElement(osm, 'way', id=str(wid), **_jattrs)
            for r in refs:
                ET.SubElement(w, 'nd', ref=str(r))
            # kistom 的 OSM 解析器读取的是 way 下的 <name> 子元素，用以区分各条线
            ET.SubElement(w, 'name').text = f"{f.layer} #{way_name_idx}"
            props = dict(f.properties or {})
            props['layer'] = f.layer
            props['entity'] = f.entity_type
            props['name'] = f"{f.layer} #{way_name_idx}"
            for k, v in props.items():
                ET.SubElement(w, 'tag', k=str(k)[:48], v=str(v)[:248])

    ET.indent(osm, space='  ')
    return ET.tostring(osm, encoding='utf-8', xml_declaration=True)


# ---------------------------------------------------------------- XML
def export_xml(data: ConvertedData) -> bytes:
    """通用 XML 导出为 KML 兼容结构（根元素 kml），便于 kistom 地图预览自动识别加载。"""
    return _export_kml_body(data)


def _export_kml_body(data: ConvertedData, apply_style=True) -> bytes:
    ET.register_namespace('', 'http://www.opengis.net/kml/2.2')
    kml = ET.Element('kml', xmlns='http://www.opengis.net/kml/2.2')
    doc = ET.SubElement(kml, 'Document')
    ET.SubElement(doc, 'name').text = data.source_name

    idx = 0
    for f in data.features:
        pm = ET.SubElement(doc, 'Placemark')
        nm = ET.SubElement(pm, 'name')
        txt = ((f.properties or {}).get('text')) or ''
        if txt:
            nm.text = str(txt)
        else:
            idx += 1
            nm.text = f"{f.entity_type} {f.layer} #{idx}"
        if f.properties:
            ET.SubElement(pm, 'description').text = json.dumps(dict(f.properties), ensure_ascii=False)
        ET.SubElement(pm, 'styleUrl').text = f"#layer_{f.layer}"

        if f.geom_type == 'Point':
            et = ET.SubElement(pm, 'Point')
            c = ET.SubElement(et, 'coordinates')
            c.text = f"{f.coordinates[0]},{f.coordinates[1]},0"
        elif f.geom_type in ('LineString',):
            et = ET.SubElement(pm, 'LineString')
            cc = ET.SubElement(et, 'coordinates')
            cc.text = " ".join(f"{x},{y},0" for x, y in f.coordinates)
        elif f.geom_type == 'Polygon':
            et = ET.SubElement(pm, 'Polygon')
            ob = ET.SubElement(et, 'outerBoundaryIs')
            lr = ET.SubElement(ob, 'LinearRing')
            cc = ET.SubElement(lr, 'coordinates')
            ring = f.coordinates[0] if f.coordinates else []
            cc.text = " ".join(f"{x},{y},0" for x, y in ring)

    if apply_style:
        used_layers = {}
        for f in data.features:
            used_layers.setdefault(f.layer, len(used_layers))
        colors = ['ff0000ff', 'ff00ff00', 'ffff0000', 'ffff00ff', 'ff00ffff', 'ffffff00', 'ff000000', 'ffff8000']
        for layer, i in used_layers.items():
            col = colors[i % len(colors)]
            st = ET.SubElement(doc, 'Style', id=f"layer_{layer}")
            ls = ET.SubElement(st, 'LineStyle')
            ET.SubElement(ls, 'color').text = col
            ET.SubElement(ls, 'width').text = '2'
            ps = ET.SubElement(st, 'PolyStyle')
            ET.SubElement(ps, 'color').text = col

    ET.indent(kml, space='  ')
    return ET.tostring(kml, encoding='utf-8', xml_declaration=True)


# ---------------------------------------------------------------- 分发
# TXT 在界面上显示为 (OV)TXT，一次生成 KS.txt(kistom) 与 OV.txt(奥维) 两个文件；
# OVTXT 不单列按钮，仅作内部导出器被 converter 调用。
FORMATS = [
    "GeoJSON", "JSON", "CSV", "TXT", "KML", "OVKML", "GPX", "DXF", "OSM", "XML",
]

# 按钮/徽章显示名（内部格式键 -> 界面文案）
FMT_LABEL = {"TXT": "(OV)TXT"}

EXT_MAP = {
    "GeoJSON": ".geojson",
    "JSON": ".json",
    "CSV": ".csv",
    "TXT": ".txt",
    "OVTXT": ".txt",
    "KML": ".kml",
    "OVKML": ".ovkml",
    "GPX": ".gpx",
    "DXF": ".dxf",
    "OSM": ".osm",
    "XML": ".xml",
}

MAP_LOADABLE = {"GeoJSON", "JSON", "CSV", "TXT", "KML", "OVKML", "GPX", "OSM", "XML"}


def export_format(data: ConvertedData, fmt: str, original_path: str = "") -> bytes:
    f = fmt.strip()
    low = f.lower()
    if low == 'geojson':
        return export_geojson(data)
    if low == 'json':
        return export_json(data)
    if low == 'csv':
        return export_csv(data)
    if low == 'txt':
        return export_txt(data)
    if low == 'ovtxt':
        return export_ovtxt(data)
    if low == 'kml':
        return export_kml(data)
    if low == 'ovkml':
        return export_ovkml(data)
    if low == 'gpx':
        return export_gpx(data)
    if low == 'dxf':
        return export_dxf(data, original_path)
    if low == 'osm':
        return export_osm(data)
    if low == 'xml':
        return export_xml(data)
    raise ValueError(f"不支持的导出格式：{fmt}")


def format_ext(fmt: str) -> str:
    return EXT_MAP.get(fmt, '.' + fmt.lower())
