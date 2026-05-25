# =============================================================================
# processing/cleaner.py
# 数据清洗 + 租售比计算
# =============================================================================

import pandas as pd
import numpy as np


def clean_house_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    清洗二手房原始数据：
    1. 删除关键字段为空的行
    2. IQR 方法剔除单价异常值
    3. 给面积加分组标签
    """
    required_cols = [c for c in ["unit_price", "area", "total_price"] if c in df.columns]
    df = df.dropna(subset=required_cols)

    Q1, Q3 = df["unit_price"].quantile([0.25, 0.75])
    IQR = Q3 - Q1

    df = df[
        (df["unit_price"] > Q1 - 1.5 * IQR) &
        (df["unit_price"] < Q3 + 1.5 * IQR)
    ]

    if "area" in df.columns:
        df["area_bin"] = pd.cut(
            df["area"],
            bins=[0, 60, 80, 100, 120, 150, 300],
            labels=["60以下", "60-80", "80-100", "100-120", "120-150", "150+"]
        )

    return df.reset_index(drop=True)


def calc_rent_sale_ratio(house_df: pd.DataFrame, rent_df: pd.DataFrame) -> dict:
    """
    计算租售比和年化租金回报率。

    house_df 需含 unit_price 列（元/㎡）
    rent_df  需含 monthly_rent_per_sqm 列（元/㎡/月）

    租售比 = 买入均价 / 月租均价，即多少个月回本
    年化毛收益率 = 月租 × 12 / 房价
    """
    avg_price = house_df["unit_price"].median()
    avg_rent  = rent_df["monthly_rent_per_sqm"].median()

    ratio        = avg_price / avg_rent
    annual_yield = avg_rent * 12 / avg_price * 100

    return {
        "avg_unit_price":   avg_price,
        "avg_monthly_rent": avg_rent,
        "ratio":            f"1:{ratio:.0f}",
        "annual_yield":     annual_yield,
    }
