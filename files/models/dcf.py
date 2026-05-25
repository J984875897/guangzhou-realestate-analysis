# =============================================================================
# models/dcf.py
# DCF（现金流折现）估值模型 + IRR（内部收益率）计算
# =============================================================================

import numpy as np


def calc_dcf(
    total_price,
    down_pct,
    loan_rate,
    rent_per_sqm,
    sqm,
    occ_rate,
    opex_ratio,
    rent_growth,
    discount_rate,
    years=20,
    terminal_cap=0.04,
) -> dict:
    """
    计算房产投资的 DCF 估值。

    返回 dict 含：npv, irr, cash_flows, monthly_mortgage, terminal_value
    """

    # 月供（等额还款）
    loan       = total_price * (1 - down_pct)
    n_months   = 30 * 12
    mr         = loan_rate / 12
    numerator  = loan * mr * (1 + mr) ** n_months
    denominator = (1 + mr) ** n_months - 1
    monthly_mortgage = numerator / denominator
    annual_mortgage  = monthly_mortgage * 12

    # 逐年现金流，同时记录 NOI（用于终值计算）
    cash_flows = []
    last_noi   = 0.0
    for y in range(1, years + 1):
        gross_rent = (
            sqm * rent_per_sqm * 12
            * occ_rate
            * (1 + rent_growth) ** (y - 1)
        )
        noi = gross_rent * (1 - opex_ratio)
        last_noi = noi
        cash_flows.append(noi - annual_mortgage)

    # 终值：用资本化率（terminal_cap）将持有期末的 NOI 折算为资产残值
    # TV = NOI_next / terminal_cap，代表第 years 年末的预期出售价格估算
    # 注意：discount_rate > terminal_cap 时公式有效；此处直接用 cap rate 更稳健
    terminal_value = last_noi * (1 + rent_growth) / terminal_cap

    # NPV：年度现金流折现 + 终值折现
    npv = sum(
        cf / (1 + discount_rate) ** y
        for y, cf in enumerate(cash_flows, 1)
    )
    npv += terminal_value / (1 + discount_rate) ** years

    # IRR：第0期投入首付，最后一期加回资产残值（模拟出售回款）
    irr_flows = [-total_price * down_pct] + cash_flows
    irr_flows[-1] += terminal_value

    if hasattr(np, 'irr'):
        irr = np.irr(irr_flows)
    else:
        irr = _calc_irr(irr_flows)

    return {
        "npv":              npv,
        "irr":              irr,
        "cash_flows":       cash_flows,
        "monthly_mortgage": monthly_mortgage,
        "terminal_value":   terminal_value,
    }


def _calc_irr(cash_flows: list, guess: float = 0.05, tol: float = 1e-6, max_iter: int = 500) -> float:
    """
    牛顿迭代法求 IRR，带发散保护。

    防止 r 变为负数后 (1+r)^t 交替变号导致 OverflowError；
    单步幅度限制在 ±0.5，r 限制在 [-0.9, 5.0] 合理区间内。
    """
    r = guess

    for _ in range(max_iter):
        try:
            f  = sum(cf / (1 + r) ** t for t, cf in enumerate(cash_flows))
            df = sum(-t * cf / (1 + r) ** (t + 1)
                     for t, cf in enumerate(cash_flows) if t > 0)
        except (OverflowError, ZeroDivisionError, ValueError):
            r = r * 0.5 + 0.02   # 步长过大时收缩并偏移
            continue

        if abs(df) < 1e-12:
            break

        step  = f / df
        step  = max(-0.5, min(step, 0.5))   # 单步幅度限制
        r_new = max(-0.9, min(r - step, 5.0))   # 结果范围约束

        if abs(r_new - r) < tol:
            return r_new

        r = r_new

    return r
