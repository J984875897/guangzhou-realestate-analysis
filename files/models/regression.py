# =============================================================================
# models/regression.py
# OLS 多元回归：分析地铁/学校距离对 IRR 的影响
# =============================================================================

import pandas as pd
import numpy as np

try:
    import statsmodels.api as sm
    _HAS_STATSMODELS = True
except ImportError:
    _HAS_STATSMODELS = False


FEATURE_COLS = [
    "dist_nearest_metro",
    "metro_count_1km",
    "dist_nearest_school",
    "school_count_1km",
    "unit_price",
]

FEATURE_LABELS = {
    "dist_nearest_metro":   "最近地铁距离(km)",
    "metro_count_1km":      "1km内地铁站数",
    "dist_nearest_school":  "最近学校距离(km)",
    "school_count_1km":     "1km内学校数",
    "unit_price":           "挂牌单价(元/㎡)",
    "const":                "截距",
}

SIGNIFICANCE = {0.01: "***", 0.05: "**", 0.10: "*", 1.0: ""}


def _sig_stars(p: float) -> str:
    for threshold, stars in SIGNIFICANCE.items():
        if p < threshold:
            return stars
    return ""


def run_irr_regression(
    df: pd.DataFrame,
    target_col: str,
    mode_name: str,
) -> dict:
    """
    对单一商业模式的 IRR 运行 OLS 回归。

    Parameters
    ----------
    df         : 含 FEATURE_COLS 和 target_col 的 DataFrame
    target_col : 目标列名，如 "irr_整租"
    mode_name  : 用于打印和存储，如 "整租"

    Returns
    -------
    dict 含：
      model       : statsmodels OLS result 对象（或 None）
      summary_str : 格式化文本输出
      params      : Series（系数）
      pvalues     : Series（p 值）
      conf_int    : DataFrame（95% 置信区间）
      rsquared    : float
      n_obs       : int
      success     : bool
    """
    # 准备数据：取所需列并去除 NaN
    available_features = [c for c in FEATURE_COLS if c in df.columns]
    cols = available_features + [target_col]
    sub = df[cols].dropna()

    result_base = {
        "model":       None,
        "summary_str": "",
        "params":      pd.Series(dtype=float),
        "pvalues":     pd.Series(dtype=float),
        "conf_int":    pd.DataFrame(),
        "rsquared":    np.nan,
        "n_obs":       len(sub),
        "success":     False,
        "mode_name":   mode_name,
        "target_col":  target_col,
    }

    if len(sub) < len(available_features) + 2:
        result_base["summary_str"] = f"[{mode_name}] 有效样本量不足（{len(sub)} 条），跳过回归"
        return result_base

    if not _HAS_STATSMODELS:
        result_base["summary_str"] = (
            f"[{mode_name}] statsmodels 未安装，无法运行回归。\n"
            "请执行：pip install statsmodels"
        )
        return result_base

    X = sm.add_constant(sub[available_features])
    y = sub[target_col] * 100   # 转换为百分比，使系数更易读

    try:
        model = sm.OLS(y, X).fit()
    except Exception as e:
        result_base["summary_str"] = f"[{mode_name}] OLS 拟合失败: {e}"
        return result_base

    # 格式化输出
    lines = [
        f"\n{'='*58}",
        f"  模式: {mode_name}   目标变量: IRR（%）",
        f"  R² = {model.rsquared:.4f}   调整 R² = {model.rsquared_adj:.4f}   样本量 = {int(model.nobs)}",
        f"{'='*58}",
        f"  {'特征':<22} {'系数':>8} {'标准误':>8} {'p值':>8} {'显著性':>6}",
        f"  {'-'*54}",
    ]

    conf = model.conf_int()
    for name in model.params.index:
        label = FEATURE_LABELS.get(name, name)
        coef  = model.params[name]
        se    = model.bse[name]
        pval  = model.pvalues[name]
        stars = _sig_stars(pval)
        lines.append(f"  {label:<22} {coef:>8.4f} {se:>8.4f} {pval:>8.4f} {stars:>6}")

    lines += [
        f"  {'-'*54}",
        f"  显著性：*** p<0.01  ** p<0.05  * p<0.10",
        f"{'='*58}",
    ]
    summary_str = "\n".join(lines)

    return {
        "model":       model,
        "summary_str": summary_str,
        "params":      model.params,
        "pvalues":     model.pvalues,
        "conf_int":    conf,
        "rsquared":    model.rsquared,
        "n_obs":       int(model.nobs),
        "success":     True,
        "mode_name":   mode_name,
        "target_col":  target_col,
    }


def run_all_regressions(
    df: pd.DataFrame,
    modes: list[str],
) -> dict:
    """
    对所有商业模式分别运行 OLS 回归。

    Parameters
    ----------
    df    : 含 irr_<mode> 列和 FEATURE_COLS 的 DataFrame
    modes : 模式名称列表，如 ["整租", "民宿", "隔断分租"]

    Returns
    -------
    dict: {mode_name: regression_result_dict}
    """
    results = {}
    for mode in modes:
        target_col = f"irr_{mode}"
        if target_col not in df.columns:
            print(f"[回归] 列 {target_col} 不存在，跳过")
            continue
        result = run_irr_regression(df, target_col, mode)
        print(result["summary_str"])
        results[mode] = result

    return results
