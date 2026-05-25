# =============================================================================
# scrapers/poi_scraper.py
# 通过 Overpass API（OpenStreetMap）拉取广州海珠区地铁站和学校 POI 数据
#
# 数据源：Overpass API（免费、无需 API Key）
# 坐标系：WGS84（与贝壳 BD-09 不同，距离计算前需在 geo_features.py 中转换）
# =============================================================================

import json
import time
from typing import Optional

import pandas as pd

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


# 海珠区 WGS84 边界框 (south, west, north, east)
HAIZHU_BBOX = (22.95, 113.22, 23.13, 113.42)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT = 60   # 秒

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

_REQUEST_HEADERS = {
    "User-Agent": "HouseDataProject/1.0",
    "Accept":     "application/json",
}


# =============================================================================
# 广州海珠区 Mock POI 数据（Overpass 失败时使用）
# 来源：广州地铁官网 + 百度地图 WGS84 近似坐标
# =============================================================================

_MOCK_METRO = [
    # 地铁2号线
    {"name": "江泰路站",  "lat": 23.0827, "lng": 113.2847},
    {"name": "南洲站",    "lat": 23.0598, "lng": 113.3166},
    {"name": "万胜围站",  "lat": 23.0740, "lng": 113.3766},
    # 地铁3号线
    {"name": "大塘站",    "lat": 23.0888, "lng": 113.2903},
    {"name": "客村站",    "lat": 23.0998, "lng": 113.3196},
    {"name": "赤岗站",    "lat": 23.0888, "lng": 113.3344},
    {"name": "磨碟沙站",  "lat": 23.0965, "lng": 113.3441},
    # 地铁8号线
    {"name": "鹭江站",    "lat": 23.0931, "lng": 113.2701},
    {"name": "凤凰新村站","lat": 23.0863, "lng": 113.2820},
    {"name": "江南西站",  "lat": 23.0921, "lng": 113.2921},
    {"name": "江夏站",    "lat": 23.0913, "lng": 113.3023},
    {"name": "昌岗站",    "lat": 23.0955, "lng": 113.3114},
    # APM 线
    {"name": "琶洲站",    "lat": 23.1005, "lng": 113.3695},
    {"name": "会展南站",  "lat": 23.1074, "lng": 113.3581},
    # 地铁18/22号线
    {"name": "琶洲西区站","lat": 23.1008, "lng": 113.3508},
]

_MOCK_SCHOOLS = [
    # 海珠区知名中小学（WGS84 近似坐标）
    {"name": "广州市第五中学",          "lat": 23.0978, "lng": 113.2813},
    {"name": "广州市第六中学",          "lat": 23.1001, "lng": 113.2977},
    {"name": "广州市第十六中学",        "lat": 23.0941, "lng": 113.3191},
    {"name": "广州市海珠区实验中学",    "lat": 23.0877, "lng": 113.3067},
    {"name": "广州市南武中学",          "lat": 23.0963, "lng": 113.2854},
    {"name": "广州市海珠实验小学",      "lat": 23.0955, "lng": 113.3023},
    {"name": "广州市海珠区同福小学",    "lat": 23.0936, "lng": 113.2780},
    {"name": "广州市海珠区龙凤小学",    "lat": 23.0860, "lng": 113.3155},
    {"name": "广州市海珠区石溪小学",    "lat": 23.0760, "lng": 113.3062},
    {"name": "广州外国语学校",          "lat": 23.1044, "lng": 113.3569},
    {"name": "广州市第十七中学",        "lat": 23.0812, "lng": 113.3441},
    {"name": "广州市海珠区新港小学",    "lat": 23.1010, "lng": 113.3400},
]


# =============================================================================
# POIScraper
# =============================================================================

class POIScraper:
    """
    通过 Overpass API 拉取地铁站和学校 POI，失败时自动回退到 mock 数据。

    使用方法：
        scraper = POIScraper()
        metro_df, school_df = scraper.fetch_all()
    """

    def __init__(self, timeout: int = OVERPASS_TIMEOUT):
        self.timeout = timeout

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def fetch_all(
        self, bbox: tuple = HAIZHU_BBOX
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """同时拉取地铁站和学校，任一失败均回退到 mock 数据。"""
        metro_df  = self.fetch_metro_stations(bbox)
        school_df = self.fetch_schools(bbox)
        return metro_df, school_df

    def fetch_metro_stations(self, bbox: tuple = HAIZHU_BBOX) -> pd.DataFrame:
        """拉取地铁站 POI，失败时返回 mock 数据。"""
        south, west, north, east = bbox
        query = f"""
[out:json][timeout:{self.timeout}];
(
  node["railway"="station"]["station"="subway"]({south},{west},{north},{east});
  node["railway"="station"]["subway"="yes"]({south},{west},{north},{east});
  node["station"="subway"]({south},{west},{north},{east});
);
out body;
"""
        df = self._query_overpass(query, poi_type="metro")
        if df is None or df.empty:
            print("[POI] 地铁站 Overpass 请求失败，使用 mock 数据")
            df = self._mock_metro_df()
        else:
            print(f"[POI] 地铁站：获取 {len(df)} 条（Overpass）")
        return df

    def fetch_schools(self, bbox: tuple = HAIZHU_BBOX) -> pd.DataFrame:
        """拉取学校 POI（小学+中学合并），失败时返回 mock 数据。"""
        south, west, north, east = bbox
        query = f"""
[out:json][timeout:{self.timeout}];
(
  node["amenity"="school"]({south},{west},{north},{east});
  way["amenity"="school"]({south},{west},{north},{east});
  relation["amenity"="school"]({south},{west},{north},{east});
);
out center;
"""
        df = self._query_overpass(query, poi_type="school")
        if df is None or df.empty:
            print("[POI] 学校 Overpass 请求失败，使用 mock 数据")
            df = self._mock_school_df()
        else:
            print(f"[POI] 学校：获取 {len(df)} 条（Overpass）")
        return df

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _query_overpass(
        self, query: str, poi_type: str, retries: int = 2
    ) -> Optional[pd.DataFrame]:
        """向 Overpass API 发送请求，依次尝试镜像节点，解析结果为 DataFrame。"""
        if not _HAS_REQUESTS:
            print("[POI] requests 未安装，跳过 Overpass 请求")
            return None

        for attempt in range(1, retries + 1):
            for endpoint in OVERPASS_ENDPOINTS:
                try:
                    resp = requests.post(
                        endpoint,
                        data={"data": query},
                        headers=_REQUEST_HEADERS,
                        timeout=self.timeout,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return self._parse_elements(data.get("elements", []), poi_type)
                except Exception as e:
                    host = endpoint.split("/")[2]
                    print(f"[POI] {host} 第{attempt}次失败: {e}")
            if attempt < retries:
                time.sleep(5 * attempt)

        return None

    def _parse_elements(self, elements: list, poi_type: str) -> pd.DataFrame:
        """解析 Overpass 返回的 elements 列表为 DataFrame。"""
        records = []
        for el in elements:
            el_type = el.get("type")

            # node 直接取 lat/lon；way/relation 取 center
            if el_type == "node":
                lat = el.get("lat")
                lng = el.get("lon")
            else:
                center = el.get("center", {})
                lat = center.get("lat")
                lng = center.get("lon")

            if lat is None or lng is None:
                continue

            tags = el.get("tags", {})
            name = (
                tags.get("name:zh") or
                tags.get("name") or
                tags.get("name:en") or
                f"{poi_type}_{el.get('id', '')}"
            )

            records.append({
                "osm_id":   el.get("id"),
                "name":     name,
                "lat":      float(lat),
                "lng":      float(lng),
                "poi_type": poi_type,
            })

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df = df.drop_duplicates(subset=["lat", "lng"]).reset_index(drop=True)
        return df

    # ------------------------------------------------------------------
    # Mock 数据
    # ------------------------------------------------------------------

    @staticmethod
    def _mock_metro_df() -> pd.DataFrame:
        df = pd.DataFrame(_MOCK_METRO)
        df["poi_type"] = "metro"
        df["osm_id"]   = range(len(df))
        return df

    @staticmethod
    def _mock_school_df() -> pd.DataFrame:
        df = pd.DataFrame(_MOCK_SCHOOLS)
        df["poi_type"] = "school"
        df["osm_id"]   = range(len(df))
        return df


# =============================================================================
# Nominatim 地理编码：小区名称 → BD-09 坐标
# =============================================================================

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def geocode_communities(
    names: list,
    district: str = "海珠区",
    city: str = "广州",
    bbox: tuple = HAIZHU_BBOX,
) -> dict:
    """
    用 Nominatim 地理编码小区名称，返回 {name: {"lng": bd09_lng, "lat": bd09_lat}}。
    未命中的小区不出现在结果中。遵循 Nominatim 1 req/s 速率限制。
    """
    if not _HAS_REQUESTS:
        print("[地理编码] requests 未安装，跳过")
        return {}

    from processing.geo_features import wgs84_to_bd09

    south, west, north, east = bbox
    result = {}
    seen: set = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            resp = requests.get(
                NOMINATIM_URL,
                params={
                    "q": f"{name}, {district}, {city}, 广东省, 中国",
                    "format": "json",
                    "limit": 1,
                    "accept-language": "zh",
                    "countrycodes": "cn",
                    "viewbox": f"{west},{north},{east},{south}",
                    "bounded": 1,
                },
                headers={
                    "User-Agent": "HouseDataProject/1.0",
                    "Accept": "application/json",
                },
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json()
            if items:
                wgs_lng = float(items[0]["lon"])
                wgs_lat = float(items[0]["lat"])
                if not (south <= wgs_lat <= north and west <= wgs_lng <= east):
                    print(f"  [地理编码] {name} 结果不在海珠区范围内，已忽略")
                    continue
                bd_lng, bd_lat = wgs84_to_bd09(wgs_lng, wgs_lat)
                result[name] = {"lng": bd_lng, "lat": bd_lat}
                print(f"  [地理编码] {name} ✓ ({bd_lat:.4f}, {bd_lng:.4f})")
            else:
                print(f"  [地理编码] {name} 未找到")
        except Exception as e:
            print(f"  [地理编码] {name} 失败: {e}")
        time.sleep(1.1)  # Nominatim policy: max 1 req/s

    return result


# =============================================================================
# 直接运行时的快速测试
# =============================================================================

if __name__ == "__main__":
    scraper = POIScraper()
    metro_df, school_df = scraper.fetch_all()

    print(f"\n地铁站（前5条）：")
    print(metro_df.head().to_string(index=False))

    print(f"\n学校（前5条）：")
    print(school_df.head().to_string(index=False))
