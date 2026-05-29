# =============================================================================
# models/sensitivity.py
# 敏感性分析：对 IRR / NPV 关键输入参数做 one-at-a-time 扫描
# =============================================================================

import math
from .dcf import calc_dcf

SENSITIVITY_RANGES = {
    "occ_rate":      [0.50, 0.60, 0.70, 0.80],
    "rent_delta":    [-0.20, -0.10, 0.00, 0.10, 0.20],
    "rent_growth_delta": [-0.02, -0.01, 0.00, 0.01, 0.02],
    "terminal_cap_delta": [-0.01, 0.00, 0.01, 0.02],
    "discount_rate": [0.045, 0.055, 0.065, 0.075],
    "price_delta":   [0.00, -0.05, -0.10, -0.20],
}


def _safe_dcf(**kwargs) -> dict:
    try:
        r = calc_dcf(**kwargs)
        # _calc_irr clamps to [-0.9, 5.0]; boundary values mean no convergence
        irr = r["irr"]
        if irr is not None and not math.isnan(irr):
            if abs(irr - 5.0) < 1e-6 or abs(irr + 0.9) < 1e-6:
                r = dict(r, irr=float("nan"))
        return r
    except Exception:
        return {"irr": float("nan"), "npv": float("nan")}


def run_sensitivity(
    base_total_price: float,
    dcf_params: dict,
    business_modes: dict,
    terminal_cap_base: float = 0.04,
) -> dict:
    """
    One-at-a-time 敏感性分析。

    Parameters
    ----------
    base_total_price : 基础总价（元），由 area_median * sqm 计算
    dcf_params       : CONFIG["dcf_params"]，含 down_pct/loan_rate/sqm/discount_rate/years
    business_modes   : BUSINESS_MODES dict
    terminal_cap_base: 基准退出资本化率

    Returns
    -------
    dict  结构见模块注释
    """
    modes = list(business_modes.keys())
    down_pct      = dcf_params["down_pct"]
    loan_rate     = dcf_params["loan_rate"]
    sqm           = dcf_params["sqm"]
    disc_base     = dcf_params["discount_rate"]
    years         = dcf_params["years"]

    def _base_call(mode_name, **overrides):
        mp = business_modes[mode_name]
        base_terminal_cap = mp.get("terminal_cap", terminal_cap_base)
        initial_capex = (
            mp.get("initial_capex", 0.0)
            + mp.get("initial_capex_per_sqm", 0.0) * sqm
        )
        kw = dict(
            total_price   = base_total_price,
            down_pct      = down_pct,
            loan_rate     = loan_rate,
            rent_per_sqm  = mp["rent_per_sqm"],
            sqm           = sqm,
            occ_rate      = mp["occ_rate"],
            opex_ratio    = mp["opex_ratio"],
            rent_growth   = mp["rent_growth"],
            discount_rate = disc_base,
            years         = years,
            terminal_cap  = base_terminal_cap,
            initial_capex = initial_capex,
        )
        kw.update(overrides)
        return _safe_dcf(**kw)

    results = {}

    # ------------------------------------------------------------------
    # 1. 出租率敏感性
    # ------------------------------------------------------------------
    occ_results = {m: [] for m in modes}
    for occ in SENSITIVITY_RANGES["occ_rate"]:
        for m in modes:
            r = _base_call(m, occ_rate=occ)
            occ_results[m].append(r["irr"])

    results["occ_rate"] = {
        "param_name":   "出租率",
        "metric":       "irr",
        "levels":       SENSITIVITY_RANGES["occ_rate"],
        "level_labels": ["50%", "60%", "70%", "80%"],
        "base_index":   None,
        "results":      occ_results,
    }

    # ------------------------------------------------------------------
    # 2. 租金单价敏感性（相对基准的乘数）
    # ------------------------------------------------------------------
    rent_results = {m: [] for m in modes}
    for delta in SENSITIVITY_RANGES["rent_delta"]:
        for m in modes:
            base_rent = business_modes[m]["rent_per_sqm"]
            r = _base_call(m, rent_per_sqm=base_rent * (1 + delta))
            rent_results[m].append(r["irr"])

    results["rent_per_sqm"] = {
        "param_name":   "租金单价",
        "metric":       "irr",
        "levels":       SENSITIVITY_RANGES["rent_delta"],
        "level_labels": ["-20%", "-10%", "基准", "+10%", "+20%"],
        "base_index":   2,
        "results":      rent_results,
    }

    # ------------------------------------------------------------------
    # 3. 租金增长敏感性（相对各模式基准增长率的百分点变化）
    # ------------------------------------------------------------------
    growth_results = {m: [] for m in modes}
    for delta in SENSITIVITY_RANGES["rent_growth_delta"]:
        for m in modes:
            base_growth = business_modes[m]["rent_growth"]
            r = _base_call(m, rent_growth=max(0.0, base_growth + delta))
            growth_results[m].append(r["irr"])

    results["rent_growth"] = {
        "param_name":   "租金年增长",
        "metric":       "irr",
        "levels":       SENSITIVITY_RANGES["rent_growth_delta"],
        "level_labels": ["基准-2pp", "基准-1pp", "基准", "基准+1pp", "基准+2pp"],
        "base_index":   2,
        "results":      growth_results,
    }

    # ------------------------------------------------------------------
    # 4. 退出 Cap Rate 敏感性（相对各模式基准退出 cap 的百分点变化）
    # ------------------------------------------------------------------
    cap_results = {m: [] for m in modes}
    for delta in SENSITIVITY_RANGES["terminal_cap_delta"]:
        for m in modes:
            base_cap = business_modes[m].get("terminal_cap", terminal_cap_base)
            cap = max(0.01, base_cap + delta)
            r = _base_call(m, terminal_cap=cap)
            cap_results[m].append(r["irr"])

    results["terminal_cap"] = {
        "param_name":   "退出Cap Rate",
        "metric":       "irr",
        "levels":       SENSITIVITY_RANGES["terminal_cap_delta"],
        "level_labels": ["基准-1pp", "基准", "基准+1pp", "基准+2pp"],
        "base_index":   1,
        "results":      cap_results,
    }

    # ------------------------------------------------------------------
    # 5. 折现率敏感性（只影响 NPV，不影响 IRR）
    # ------------------------------------------------------------------
    disc_results = {m: [] for m in modes}
    for dr in SENSITIVITY_RANGES["discount_rate"]:
        for m in modes:
            r = _base_call(m, discount_rate=dr)
            npv_wan = r["npv"] / 10000 if not math.isnan(r["npv"]) else float("nan")
            disc_results[m].append(npv_wan)

    results["discount_rate"] = {
        "param_name":   "折现率",
        "metric":       "npv",
        "levels":       SENSITIVITY_RANGES["discount_rate"],
        "level_labels": ["4.5%", "5.5%", "6.5%", "7.5%"],
        "base_index":   1,
        "results":      disc_results,
    }

    # ------------------------------------------------------------------
    # 6. 房价变动敏感性（影响首付 + 月供，从而影响 IRR）
    # ------------------------------------------------------------------
    price_results = {m: [] for m in modes}
    for delta in SENSITIVITY_RANGES["price_delta"]:
        adj_price = base_total_price * (1 + delta)
        for m in modes:
            mp = business_modes[m]
            initial_capex = (
                mp.get("initial_capex", 0.0)
                + mp.get("initial_capex_per_sqm", 0.0) * sqm
            )
            r = _safe_dcf(
                total_price   = adj_price,
                down_pct      = down_pct,
                loan_rate     = loan_rate,
                rent_per_sqm  = mp["rent_per_sqm"],
                sqm           = sqm,
                occ_rate      = mp["occ_rate"],
                opex_ratio    = mp["opex_ratio"],
                rent_growth   = mp["rent_growth"],
                discount_rate = disc_base,
                years         = years,
                terminal_cap  = mp.get("terminal_cap", terminal_cap_base),
                initial_capex = initial_capex,
            )
            price_results[m].append(r["irr"])

    results["price_delta"] = {
        "param_name":   "房价变动",
        "metric":       "irr",
        "levels":       SENSITIVITY_RANGES["price_delta"],
        "level_labels": ["基准", "-5%", "-10%", "-20%"],
        "base_index":   0,
        "results":      price_results,
    }

    # ------------------------------------------------------------------
    # 7. 龙卷图参考数据（民宿模式，排除 discount_rate）
    # ------------------------------------------------------------------
    ref_mode = "民宿"
    base_irr_ref = _base_call(ref_mode)["irr"]

    tornado_rows = []
    for key, data in results.items():
        if data["metric"] != "irr":
            continue
        mode_irrs = [v for v in data["results"][ref_mode] if not math.isnan(v)]
        if not mode_irrs:
            continue
        low_irr  = min(mode_irrs)
        high_irr = max(mode_irrs)
        low_idx  = data["results"][ref_mode].index(low_irr)
        high_idx = data["results"][ref_mode].index(high_irr)
        tornado_rows.append({
            "param":       data["param_name"],
            "param_key":   key,
            "low_irr":     low_irr,
            "high_irr":    high_irr,
            "low_label":   data["level_labels"][low_idx],
            "high_label":  data["level_labels"][high_idx],
        })

    tornado_rows.sort(key=lambda r: r["high_irr"] - r["low_irr"], reverse=True)

    results["_tornado_ref"] = {
        "mode":     ref_mode,
        "base_irr": base_irr_ref,
        "rows":     tornado_rows,
    }

    return results
