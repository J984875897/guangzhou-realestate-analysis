# =============================================================================
# processing/geo_features.py
# 地理特征工程：坐标转换、距离计算、逐小区 IRR 估算
# =============================================================================

import math
from typing import Optional

import numpy as np
import pandas as pd

from models.dcf import calc_dcf


# =============================================================================
# 坐标转换：BD-09 → WGS84
# 贝壳 API 返回百度 BD-09 坐标，OSM 使用 WGS84，需先转换再计算距离
# =============================================================================

def _transform_lat(lng: float, lat: float) -> float:
    ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat
    ret += 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * math.pi)
            + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi)
            + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi)
            + 320.0 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(lng: float, lat: float) -> float:
    ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng
    ret += 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * math.pi)
            + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * math.pi)
            + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * math.pi)
            + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    """GCJ-02（火星坐标）→ WGS84，精度约 1–5 米。"""
    a  = 6378245.0
    ee = 0.00669342162296594323
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat   = lat / 180.0 * math.pi
    magic     = math.sin(rad_lat)
    magic     = 1 - ee * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((a * (1 - ee)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (a / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lng - d_lng, lat - d_lat


def bd09_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    """BD-09（百度坐标）→ WGS84，转换链：BD-09 → GCJ-02 → WGS84。"""
    # BD-09 → GCJ-02
    x = lng - 0.0065
    y = lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * math.pi * 3000.0 / 180.0)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * math.pi * 3000.0 / 180.0)
    gcj_lng = z * math.cos(theta)
    gcj_lat = z * math.sin(theta)
    # GCJ-02 → WGS84
    return gcj02_to_wgs84(gcj_lng, gcj_lat)


def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    """WGS84 → GCJ-02，与 gcj02_to_wgs84 互为近似逆变换，精度约 2–5 米。"""
    a  = 6378245.0
    ee = 0.00669342162296594323
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat    = lat / 180.0 * math.pi
    magic      = math.sin(rad_lat)
    magic      = 1 - ee * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((a * (1 - ee)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (a / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lng + d_lng, lat + d_lat


def gcj02_to_bd09(lng: float, lat: float) -> tuple[float, float]:
    """GCJ-02 → BD-09。"""
    z     = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * math.pi * 3000.0 / 180.0)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * math.pi * 3000.0 / 180.0)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006


def wgs84_to_bd09(lng: float, lat: float) -> tuple[float, float]:
    """WGS84 → BD-09，转换链：WGS84 → GCJ-02 → BD-09。"""
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    return gcj02_to_bd09(gcj_lng, gcj_lat)


# =============================================================================
# Haversine 距离（km）
# =============================================================================

def haversine(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """计算两点之间的球面距离（km）。"""
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# =============================================================================
# 地理特征工程
# =============================================================================

def add_geo_features(
    house_df: pd.DataFrame,
    metro_df: pd.DataFrame,
    school_df: pd.DataFrame,
    radius_km: float = 1.0,
) -> pd.DataFrame:
    """
    为每个小区计算：
      - dist_nearest_metro  : 最近地铁站距离（km）
      - metro_count_1km     : radius_km 内地铁站数量
      - dist_nearest_school : 最近学校距离（km，小学+中学合并）
      - school_count_1km    : radius_km 内学校数量
      - wgs_lng / wgs_lat   : BD-09 转换后的 WGS84 坐标（供图表使用）

    house_df 需含 lng, lat 列（BD-09 坐标）；
    metro_df / school_df 需含 lng, lat 列（WGS84 坐标）。
    """
    df = house_df.copy()

    # 检查坐标列是否存在
    if "lng" not in df.columns or "lat" not in df.columns:
        # 没有坐标，填充 NaN
        for col in ["wgs_lng", "wgs_lat", "dist_nearest_metro",
                    "metro_count_1km", "dist_nearest_school", "school_count_1km"]:
            df[col] = np.nan
        return df

    # 转换小区坐标：BD-09 → WGS84
    wgs_coords = df.apply(
        lambda row: pd.Series(bd09_to_wgs84(row["lng"], row["lat"]),
                              index=["wgs_lng", "wgs_lat"])
        if pd.notna(row["lng"]) and pd.notna(row["lat"]) else pd.Series([np.nan, np.nan], index=["wgs_lng", "wgs_lat"]),
        axis=1,
    )
    df["wgs_lng"] = wgs_coords["wgs_lng"]
    df["wgs_lat"] = wgs_coords["wgs_lat"]

    # 地铁特征
    metro_coords = list(zip(metro_df["lng"], metro_df["lat"])) if not metro_df.empty else []
    school_coords = list(zip(school_df["lng"], school_df["lat"])) if not school_df.empty else []

    def _nearest_and_count(wgs_lng, wgs_lat, poi_coords, radius):
        if pd.isna(wgs_lng) or pd.isna(wgs_lat) or not poi_coords:
            return np.nan, 0
        dists = [haversine(wgs_lng, wgs_lat, plng, plat) for plng, plat in poi_coords]
        nearest = min(dists)
        count   = sum(1 for d in dists if d <= radius)
        return nearest, count

    metro_results = df.apply(
        lambda r: _nearest_and_count(r["wgs_lng"], r["wgs_lat"], metro_coords, radius_km),
        axis=1,
    )
    df["dist_nearest_metro"] = metro_results.apply(lambda x: x[0])
    df["metro_count_1km"]    = metro_results.apply(lambda x: x[1])

    school_results = df.apply(
        lambda r: _nearest_and_count(r["wgs_lng"], r["wgs_lat"], school_coords, radius_km),
        axis=1,
    )
    df["dist_nearest_school"] = school_results.apply(lambda x: x[0])
    df["school_count_1km"]    = school_results.apply(lambda x: x[1])

    return df.reset_index(drop=True)


# =============================================================================
# 逐小区 IRR 计算
# =============================================================================

def calc_per_community_irr(
    house_df: pd.DataFrame,
    dcf_params: dict,
    business_modes: dict,
) -> pd.DataFrame:
    """
    对每个小区用其 unit_price 独立计算 DCF/IRR，新增列：
      irr_<mode_name>  （如 irr_整租 / irr_民宿 / irr_隔断分租）

    Parameters
    ----------
    house_df      : 含 unit_price 列的小区 DataFrame
    dcf_params    : CONFIG["dcf_params"]（sqm, down_pct, loan_rate, discount_rate, years）
    business_modes: BUSINESS_MODES dict（含 rent_per_sqm, occ_rate, opex_ratio, rent_growth）
    """
    df = house_df.copy()

    sqm           = dcf_params["sqm"]
    down_pct      = dcf_params["down_pct"]
    loan_rate     = dcf_params["loan_rate"]
    discount_rate = dcf_params["discount_rate"]
    years         = dcf_params["years"]

    for mode_name, mode_params in business_modes.items():
        irr_col = f"irr_{mode_name}"
        irr_vals = []

        for _, row in df.iterrows():
            unit_price = row.get("unit_price")
            if pd.isna(unit_price) or unit_price <= 0:
                irr_vals.append(np.nan)
                continue

            total_price = unit_price * sqm

            try:
                result = calc_dcf(
                    total_price   = total_price,
                    down_pct      = down_pct,
                    loan_rate     = loan_rate,
                    rent_per_sqm  = mode_params["rent_per_sqm"],
                    sqm           = sqm,
                    occ_rate      = mode_params["occ_rate"],
                    opex_ratio    = mode_params["opex_ratio"],
                    rent_growth   = mode_params["rent_growth"],
                    discount_rate = discount_rate,
                    years         = years,
                    terminal_cap  = mode_params.get("terminal_cap", 0.04),
                    initial_capex = (
                        mode_params.get("initial_capex", 0.0)
                        + mode_params.get("initial_capex_per_sqm", 0.0) * sqm
                    ),
                )
                irr_vals.append(result["irr"])
            except Exception:
                irr_vals.append(np.nan)

        df[irr_col] = irr_vals

    return df.reset_index(drop=True)
