# -*- coding: utf-8 -*-
"""CGCS2000 (Gauss-Krüger 投影) → WGS84 经纬度 换算。

优先使用 pyproj（精确、可靠）；若 pyproj 不可用则回退到内置的高斯-克吕格逆解
（已与 pyproj 结果逐点校验一致）。

原理：用户给出 3° 带内任意参考经纬度 (lon, lat)，据此确定中央经线
cm = round(lon/3)*3（度），再把 DXF/DWG 的米制坐标 (E, N) 投影逆变换为经纬度。

CGCS2000 采用 GRS80 椭球，与 WGS84 偏差 < 1 m，产物可直接用于
GeoJSON / KML / OSM / GPX 等要求经纬度的格式。
"""
import math

# ---- 是否有 pyproj（打包后仍可用，前提是 --collect-data pyproj）----
try:
    from pyproj import Transformer, CRS
    _HAS_PYPROJ = True
except Exception:
    _HAS_PYPROJ = False

# ---- CGCS2000 / GRS80 椭球参数（内置回退用）----
_A = 6378137.0
_E2 = 0.0066943799901413165
_EP2 = _E2 / (1.0 - _E2)
_FE = 500000.0
_FN = 0.0

_kk = {}


def central_meridian(ref_lon):
    """包含参考经度的 3° 带中央经线（度）。"""
    return round(float(ref_lon) / 3.0) * 3.0


def _transformer(cm):
    """构造米制→经纬度的变换器（pyproj）。"""
    key = ('proj', cm)
    if key not in _kk:
        src = CRS.from_proj4(
            f"+proj=tmerc +lat_0=0 +lon_0={cm} +k=1 +x_0=500000 +y_0=0 "
            f"+ellps=GRS80 +units=m +no_defs")
        _kk[key] = Transformer.from_crs(src, 'EPSG:4326', always_xy=True)
    return _kk[key]


def _meridional_arc(phi, A, E2):
    return A * ((1.0 - E2 / 4.0 - 3.0 * E2 * E2 / 64.0 - 5.0 * E2 ** 3 / 256.0) * phi
                - (3.0 * E2 / 8.0 + 3.0 * E2 * E2 / 32.0 + 45.0 * E2 ** 3 / 1024.0) * math.sin(2.0 * phi)
                + (15.0 * E2 * E2 / 256.0 + 45.0 * E2 ** 3 / 1024.0) * math.sin(4.0 * phi)
                - 35.0 * E2 ** 3 / 3072.0 * math.sin(6.0 * phi))


def _footpoint(y, A, E2):
    phi = y / (A * (1.0 - E2 / 4.0 - 3.0 * E2 * E2 / 64.0 - 5.0 * E2 ** 3 / 256.0))
    for _ in range(12):
        R1 = A * (1.0 - E2) / (1.0 - E2 * math.sin(phi) ** 2) ** 1.5
        d = y - _meridional_arc(phi, A, E2)
        phi += d / R1
        if abs(d) < 1e-12:
            break
    return phi


def _inverse_manual(e, n, cm):
    """内置高斯-克吕格逆解（已与 pyproj 逐点校验一致）：(E,N)米制 + cm(度) → (lon,lat)度。"""
    x = e - _FE
    y = n - _FN
    phi1 = _footpoint(y, _A, _E2)
    sinp = math.sin(phi1)
    cosp = math.cos(phi1)
    tanp = math.tan(phi1)
    N1 = _A / math.sqrt(1.0 - _E2 * sinp * sinp)
    R1 = _A * (1.0 - _E2) / (1.0 - _E2 * sinp * sinp) ** 1.5
    T1 = tanp * tanp
    C1 = _EP2 * cosp * cosp
    D = x / N1
    D2 = D * D
    D3 = D2 * D
    D4 = D2 * D2
    D5 = D4 * D
    D6 = D2 * D4

    lat = phi1 - (N1 * tanp / R1) * (D2 / 2.0
          - (5.0 + 3.0 * T1 + 10.0 * C1 - 4.0 * C1 * C1 - 9.0 * _EP2) * D4 / 24.0
          + (61.0 + 90.0 * T1 + 298.0 * C1 + 45.0 * T1 * T1 - 252.0 * _EP2 - 3.0 * C1 * C1) * D6 / 720.0)

    # 经度：同为 D = x/N1，系列整体除以 cos(φ1) 后换算为度
    dl = (D - (1.0 + 2.0 * T1 + C1) * D3 / 6.0
          + (5.0 - 2.0 * C1 + 28.0 * T1 - 3.0 * C1 * C1 + 8.0 * _EP2 + 24.0 * T1 * T1) * D5 / 120.0) / cosp
    lon = cm + math.degrees(dl)
    return (lon, math.degrees(lat))


def inverse(e, n, cm):
    if _HAS_PYPROJ:
        lon, lat = _transformer(cm).transform(float(e), float(n))
        return (float(lon), float(lat))
    return _inverse_manual(float(e), float(n), cm)


def transform_data(data, ref_lon, ref_lat):
    """把 DXF/DWG 读出的米制坐标批量转换为经纬度（就地修改 data）。"""
    from .model import Feature
    from .exporter import _flatten_coordinates
    cm = central_meridian(ref_lon)

    def conv(p):
        return inverse(p[0], p[1], cm)

    new_features = []
    for f in data.features:
        if f.geom_type == 'Point':
            lon, lat = conv(f.coordinates)
            new_features.append(Feature('Point', [lon, lat], f.properties, f.layer, f.entity_type))
        elif f.geom_type == 'LineString':
            pts = [list(conv(p)) for p in f.coordinates]
            new_features.append(Feature('LineString', pts, f.properties, f.layer, f.entity_type))
        elif f.geom_type == 'Polygon':
            rings = [[list(conv(p)) for p in ring] for ring in f.coordinates]
            new_features.append(Feature('Polygon', rings, f.properties, f.layer, f.entity_type))
        else:
            new_features.append(f)
    data.features = new_features

    minx = miny = float('inf')
    maxx = maxy = float('-inf')
    for f in data.features:
        for x, y in _flatten_coordinates(f.coordinates):
            if x < minx: minx = x
            if y < miny: miny = y
            if x > maxx: maxx = x
            if y > maxy: maxy = y
    if minx <= maxx and miny <= maxy:
        data.bounds = (minx, miny, maxx, maxy)
    return data
