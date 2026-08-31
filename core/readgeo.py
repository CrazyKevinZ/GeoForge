# -*- coding: utf-8 -*-
"""各类地理/数据格式读取器。

将 GeoJSON/JSON/CSV/TXT/KML/GPX/OSM/XML/SVG 解析为统一的 ConvertedData 模型，
实现格式之间的任意互转。
"""
import json
import os
import csv
import xml.etree.ElementTree as ET

from .model import ConvertedData, Feature


def _new(source_name, ext):
    return ConvertedData(source_name=source_name, source_ext=ext)


def _upd_bounds(data, pt):
    minx, miny, maxx, maxy = data.bounds
    minx = min(minx, pt[0]); maxx = max(maxx, pt[0])
    miny = min(miny, pt[1]); maxy = max(maxy, pt[1])
    data.bounds = (minx, miny, maxx, maxy)


def _add(data, feature):
    data.features.append(feature)
    gt = feature.geom_type
    if gt == 'Point':
        data.count_point += 1
    elif gt in ('LineString', 'MultiLineString'):
        data.count_line += 1
    elif gt in ('Polygon', 'MultiPolygon'):
        data.count_polygon += 1
    return feature


def _strip_jsonc(text: str) -> str:
    """去掉 // 行注释，返回可被 json.loads 解析的纯 JSON。"""
    return '\n'.join(ln for ln in text.splitlines() if not ln.strip().startswith('//'))


def _load_json_auto(path):
    """读取 JSON（自动剥离 // 注释头）。若判定为 GeoForge 自身的界面配置
    存档（含 geometry 且无 features），抛清晰提示而非硬转。"""
    with open(path, encoding='utf-8-sig') as fh:
        raw = fh.read()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        obj = json.loads(_strip_jsonc(raw))
    # GeoForge 自身的界面状态配置（geometry 键、无 features/坐标）→ 明确拒绝
    if isinstance(obj, dict) and 'geometry' in obj and 'features' not in obj:
        raise ValueError(
            "这是 GeoForge 自身的界面配置文件（GeoForge_ui.json），"
            "只记录窗口位置，不是坐标数据，不能作为源文件转换。\n"
            "请拖入真正的 DXF/GeoJSON/KML/GPX/CSV/TXT 等坐标文件。")
    return obj


# ---------------------------------------------------------------- GeoJSON
def read_geojson(path, name):
    data = _new(name, '.geojson')
    obj = _load_json_auto(path)
    feats = obj.get('features', []) if isinstance(obj, dict) else obj
    for f in feats:
        props = f.get('properties') or {}
        geom = f.get('geometry') or {}
        gtype = geom.get('type', 'Point')
        coords = geom.get('coordinates', [])
        feat = Feature(gtype, coords, dict(props), str(props.get('layer', '0')), '')
        _add(data, feat)
        _upd_bounds_geom(data, coords)
    if not data.features:
        data.bounds = (-180, -90, 180, 90)
    return data


def _upd_bounds_geom(data, coords):
    for pt in _walk_coords(coords):
        _upd_bounds(data, pt)


def _walk_coords(coords):
    if isinstance(coords, (list, tuple)):
        if len(coords) >= 2 and not isinstance(coords[0], (list, tuple)):
            yield float(coords[0]), float(coords[1])
        else:
            for c in coords:
                yield from _walk_coords(c)


# ---------------------------------------------------------------- JSON 通用
def _find_features(obj):
    """在通用 JSON 里递归寻找 features 列表。"""
    out = []
    if isinstance(obj, dict):
        if 'features' in obj and isinstance(obj['features'], list):
            out.extend(obj['features'])
        for v in obj.values():
            out.extend(_find_features(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_find_features(v))
    return out


def read_json(path, name):
    data = _new(name, '.json')
    obj = _load_json_auto(path)
    found = False
    for item in _find_features(obj):
        # 兼容 GeoJSON Feature 或通用 {type, layer, coordinates, properties}
        if isinstance(item, dict):
            props = item.get('properties') or {}
            if 'geometry' in item and isinstance(item['geometry'], dict):
                geom = item['geometry']
                gtype = geom.get('type', 'Point')
                coords = geom.get('coordinates', [])
            else:
                gtype = item.get('type', item.get('geometry_type')) or 'Point'
                if gtype in ('Point', 'LineString', 'Polygon', 'MultiPoint', 'MultiLineString', 'MultiPolygon'):
                    gtype = gtype
                coords = item.get('coordinates', [])
                props = item.get('properties') or {}
                if not props and 'properties' in item:
                    props = item.get('properties') or {}
            feat = Feature(gtype, coords, dict(props), str(props.get('layer', '0')), str(item.get('entity', '')))
            _add(data, feat)
            _upd_bounds_geom(data, coords)
            found = True
    if not found:
        # 尝试整个 JSON 作为单一结构
        pass
    if not data.features:
        data.bounds = (-180, -90, 180, 90)
    return data


# ---------------------------------------------------------------- CSV / TXT
def _guess_csv_feature(header, row, data):
    idx = {h.strip().lower(): i for i, h in enumerate(header)}
    def getx(key):
        for k in ('lon', 'lng', 'long', 'longitude', 'x', 'easting'):
            if k in idx:
                return row[idx[k]]
        return None
    def gety(key):
        for k in ('lat', 'latitude', 'y', 'northing'):
            if k in idx:
                return row[idx[k]]
        return None
    x = getx('x'); y = gety('y')
    props = {}
    for i, h in enumerate(header):
        if i < len(row):
            props[h] = row[i]
    if x is not None and y is not None:
        try:
            cx, cy = float(x), float(y)
            f = Feature('Point', [cx, cy], props, str(props.get('layer', '0')), '')
            _add(data, f); _upd_bounds(data, (cx, cy))
            return
        except (ValueError, TypeError):
            pass
    # 该行没有坐标列 → 不作为要素；若整份文件都没有坐标行，total==0
    # 会在转换/估算时被拒绝（不虚构 (0,0) 点）。


def read_csv(path, name):
    data = _new(name, '.csv')
    with open(path, encoding='utf-8-sig', newline='') as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    if not rows:
        return data
    header = rows[0]
    for row in rows[1:]:
        if not row or all(c == '' for c in row):
            continue
        _guess_csv_feature(header, row, data)
    if not data.features:
        data.bounds = (-180, -90, 180, 90)
    return data


def read_txt(path, name):
    data = _new(name, '.txt')
    with open(path, encoding='utf-8-sig') as fh:
        lines = fh.read().splitlines()
    # 尝试解析 "x, y" 或 "x y" 坐标行
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith(('=', '[', '#')):
            continue
        parts = s.replace(',', ' ').split()
        if len(parts) >= 2:
            try:
                x, y = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            f = Feature('Point', [x, y], {'text': s}, '0', '')
            _add(data, f); _upd_bounds(data, (x, y))
        # 说明：没有坐标的行不生成要素；若整份文件都没有坐标行，则视为
        # 无有效坐标数据（total==0），转换/估算时会被拒绝，而不是虚构 (0,0) 点。
    if not data.features:
        data.bounds = (-180, -90, 180, 90)
    return data


# ---------------------------------------------------------------- KML
def read_kml(path, name):
    data = _new(name, '.kml')
    tree = ET.parse(path)
    root = tree.getroot()
    ns_uri = _find_namespace(root)
    for pm in root.iter(_q(ns_uri, 'Placemark')):
        name_txt = pm.findtext(_q(ns_uri, 'name')) or ''
        props = {'name': name_txt}
        # Point
        pt = pm.find(_q(ns_uri, 'Point'))
        if pt is not None:
            c = pt.findtext(_q(ns_uri, 'coordinates'))
            coord = _first_coord(c)
            f = Feature('Point', list(coord) if coord else [0, 0], props, '0', '')
            _add(data, f)
            if coord: _upd_bounds(data, (coord[0], coord[1]))
            continue
        ls = pm.find(_q(ns_uri, 'LineString'))
        if ls is not None:
            ring = _parse_coordset(ls.findtext(_q(ns_uri, 'coordinates')))
            if len(ring) >= 2:
                f = Feature('LineString', ring, props, '0', '')
                _add(data, f)
                for p in ring: _upd_bounds(data, p)
            continue
        poly = pm.find(_q(ns_uri, 'Polygon'))
        if poly is not None:
            outer = poly.find(f'.//{_q(ns_uri, "outerBoundaryIs")}')
            ring = []
            if outer is not None:
                lr = outer.find(_q(ns_uri, 'LinearRing'))
                if lr is not None:
                    ring = _parse_coordset(lr.findtext(_q(ns_uri, 'coordinates')))
            if len(ring) >= 3:
                f = Feature('Polygon', [ring], props, '0', '')
                _add(data, f)
                for p in ring: _upd_bounds(data, p)
    if not data.features:
        data.bounds = (-180, -90, 180, 90)
    return data


def _first_coord(text):
    if not text:
        return None
    t = text.strip()
    if ',' in t:
        first = t.split()[0]
        parts = first.split(',')
        try:
            return [float(parts[0]), float(parts[1])]
        except (ValueError, IndexError):
            return None
    return None


def _parse_coordset(text):
    out = []
    if not text:
        return out
    for item in text.strip().split():
        parts = item.split(',')
        if len(parts) >= 2:
            try:
                out.append([float(parts[0]), float(parts[1])])
            except ValueError:
                pass
    return out


def _find_namespace(root):
    if root.tag.startswith('{'):
        return root.tag.split('}')[0][1:]
    return ''


def _q(ns, tag):
    if ns:
        return '{%s}%s' % (ns, tag)
    return tag


# ---------------------------------------------------------------- GPX
def read_gpx(path, name):
    data = _new(name, '.gpx')
    tree = ET.parse(path)
    root = tree.getroot()
    ns = _find_namespace(root)
    # waypoints
    for wpt in root.iter(_q(ns, 'wpt')):
        try:
            lat = float(wpt.get('lat')); lon = float(wpt.get('lon'))
        except (TypeError, ValueError):
            continue
        t = wpt.findtext(_q(ns, 'name')) or ''
        f = Feature('Point', [lon, lat], {'text': t}, '0', '')
        _add(data, f); _upd_bounds(data, (lon, lat))
    # tracks (trkseg → trkpt)
    for trkseg in root.iter(_q(ns, 'trkseg')):
        pts = []
        for tp in trkseg.iter(_q(ns, 'trkpt')):
            try:
                lat = float(tp.get('lat')); lon = float(tp.get('lon'))
            except (TypeError, ValueError):
                continue
            pts.append([lon, lat])
        if len(pts) >= 2:
            f = Feature('LineString', pts, {}, '0', 'trk')
            _add(data, f)
            for p in pts: _upd_bounds(data, p)
    # routes
    for rte in root.iter(_q(ns, 'rte')):
        pts = []
        for tp in rte.iter(_q(ns, 'rtept')):
            try:
                lat = float(tp.get('lat')); lon = float(tp.get('lon'))
            except (TypeError, ValueError):
                continue
            pts.append([lon, lat])
        if len(pts) >= 2:
            f = Feature('LineString', pts, {}, '0', 'rte')
            _add(data, f)
            for p in pts: _upd_bounds(data, p)
    if not data.features:
        data.bounds = (-180, -90, 180, 90)
    return data


# ---------------------------------------------------------------- OSM
def read_osm(path, name):
    data = _new(name, '.osm')
    tree = ET.parse(path)
    root = tree.getroot()
    ns = _find_namespace(root)
    nodes = {}
    for n in root.iter(_q(ns, 'node')):
        try:
            lat = float(n.get('lat')); lon = float(n.get('lon'))
        except (TypeError, ValueError):
            continue
        tags = {t.get('k'): t.get('v') for t in n.findall(_q(ns, 'tag'))}
        nodes[int(n.get('id'))] = (lon, lat, tags)
        f = Feature('Point', [lon, lat], tags, str(tags.get('layer', '0')), '')
        _add(data, f); _upd_bounds(data, (lon, lat))
    for w in root.iter(_q(ns, 'way')):
        refs = [int(nd.get('ref')) for nd in w.findall(_q(ns, 'nd'))]
        tags = {t.get('k'): t.get('v') for t in w.findall(_q(ns, 'tag'))}
        pts = [list(nodes[r][:2]) for r in refs if r in nodes]
        if len(pts) >= 2:
            f = Feature('LineString', pts, tags, str(tags.get('layer', '0')), 'way')
            _add(data, f)
            for p in pts: _upd_bounds(data, p)
    if not data.features:
        data.bounds = (-180, -90, 180, 90)
    return data


# ---------------------------------------------------------------- XML 通用
def read_xml(path, name):
    data = _new(name, '.xml')
    tree = ET.parse(path)
    root = tree.getroot()
    ns = _find_namespace(root)
    for feat in root.iter('Feature'):
        props = {}
        for prop in feat.iter('Property'):
            props[prop.get('name', '')] = prop.text or ''
        geom = feat.find('.//Geometry')
        gtype = 'Point'
        coords = []
        if geom is not None:
            gtype = geom.get('type', 'Point')
            pts = []
            for po in geom.iter('Point'):
                try:
                    pts.append([float(po.get('x')), float(po.get('y'))])
                except (TypeError, ValueError):
                    continue
            if gtype == 'Point' and pts:
                coords = pts[0]
            elif gtype in ('LineString', 'Polygon'):
                coords = pts if gtype == 'LineString' else [pts]
        if coords:
            f = Feature(gtype, coords, props, str(props.get('layer', '0')), feat.get('entity', ''))
            _add(data, f)
            _upd_bounds_geom(data, coords)
    if not data.features:
        data.bounds = (-180, -90, 180, 90)
    return data


# ---------------------------------------------------------------- 分发
FORMAT_READERS = {
    'geojson': read_geojson,
    'json': read_json,
    'csv': read_csv,
    'txt': read_txt,
    'kml': read_kml,
    'gpx': read_gpx,
    'osm': read_osm,
    'xml': read_xml,
}


def read_file(path):
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path)
    reader = FORMAT_READERS.get(ext.lstrip('.'))
    if reader:
        return reader(path, name)
    raise ValueError(f"不支持的输入格式：{ext}。支持的格式：GeoJSON/JSON/CSV/TXT/KML/GPX/DXF/OSM/XML。")
