# =============================================================================
# visualization/report.py
# 决策端 HTML 报告生成器（文件驱动版）
#
# 不依赖 DataFrame，直接读取：
#   output/summary.json        — KPI 指标数字
#   output/charts/*.html       — Plotly 交互图（通过 <iframe> 嵌入）
#   output/charts/*.png        — Matplotlib 静态图（通过 <img> 嵌入）
#
# 生成 output/dashboard.html（固定名，每次覆盖），可与 charts/ 目录一起打开。
# =============================================================================

import json
from pathlib import Path
from datetime import datetime


# =============================================================================
# HTML 辅助函数
# =============================================================================

def _iframe(charts_dir: Path, stem: str, height: int = 420) -> str:
    """生成 <iframe>；HTML 文件不存在时返回灰色占位 div。"""
    if (charts_dir / f"{stem}.html").exists():
        return (
            f'<iframe src="charts/{stem}.html" '
            f'style="width:100%;height:{height}px;border:none;border-radius:6px;" '
            f'loading="lazy"></iframe>'
        )
    return f'<div class="placeholder">图表暂缺：{stem}</div>'


def _img(charts_dir: Path, stem: str) -> str:
    """生成 <img>；PNG 文件不存在时返回灰色占位 div。"""
    if (charts_dir / f"{stem}.png").exists():
        return f'<img src="charts/{stem}.png" style="width:100%;border-radius:6px;display:block">'
    return f'<div class="placeholder">图表暂缺：{stem}</div>'


def _chart_block(title: str, content_html: str, full_width: bool = False) -> str:
    cls = "chart-card full" if full_width else "chart-card"
    return f'<div class="{cls}"><div class="chart-title">{title}</div>{content_html}</div>'


def _kpi_card(label: str, value: str, unit: str = "",
              sub: str = "", card_class: str = "") -> str:
    cls = f"kpi-card {card_class}".strip()
    return (
        f'<div class="{cls}">'
        f'<div class="kpi-label">{label}</div>'
        f'<div><span class="kpi-value">{value}</span>'
        f'<span class="kpi-unit">{unit}</span></div>'
        f'<div class="kpi-sub">{sub}</div>'
        f'</div>'
    )


def _default_summary() -> dict:
    return {
        "generated_at":         "—",
        "avg_price_wan":        0.0,
        "avg_rent_sqm":         0.0,
        "annual_yield_pct":     0.0,
        "rent_sale_ratio":      0,
        "best_mode":            "—",
        "best_irr_pct":         None,
        "discount_rate_pct":    5.5,
        "house_count":          0,
        "has_geo":              False,
        "has_regression":       False,
        "best_no_loan_mode":    "—",
        "best_no_loan_irr_pct": None,
        "has_no_loan":          False,
        "best_owned_mode":         "—",
        "best_owned_noi_wan":      None,
        "best_owned_monthly_wan":  None,
        "best_owned_cap_rate_pct": None,
        "has_owned":               False,
        "has_sensitivity":         False,
        "reno_data_source":        "unknown",
        "reno_case_count":         0,
        "reno_success_pages":      0,
        "reno_failed_pages":       0,
        "reno_detail_success":     0,
        "reno_detail_failed":      0,
    }


# =============================================================================
# CSS
# =============================================================================

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, "PingFang SC", "Helvetica Neue", Arial, sans-serif;
  background: #f0f2f5;
  color: #333;
}
header {
  background: linear-gradient(135deg, #1a237e 0%, #283593 60%, #3949ab 100%);
  color: #fff;
  padding: 28px 40px;
}
header h1 { font-size: 22px; font-weight: 600; letter-spacing: 1px; }
header p  { font-size: 13px; opacity: 0.75; margin-top: 6px; }

.container { max-width: 1400px; margin: 0 auto; padding: 32px 24px; }

/* KPI Cards */
.kpi-row { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 32px; }
.kpi-card {
  flex: 1 1 180px;
  background: #fff;
  border-radius: 12px;
  padding: 20px 22px;
  box-shadow: 0 2px 8px rgba(0,0,0,.07);
  border-left: 4px solid #3949ab;
}
.kpi-card.highlight { border-left-color: #ff8f00; }
.kpi-card.decision  { border-left-color: #27ae60; }
.kpi-card.warn      { border-left-color: #e74c3c; }
.kpi-label { font-size: 12px; color: #888; margin-bottom: 8px;
             text-transform: uppercase; letter-spacing: .5px; }
.kpi-value { font-size: 26px; font-weight: 700; color: #1a237e; }
.kpi-unit  { font-size: 13px; color: #666; margin-left: 4px; }
.kpi-sub   { font-size: 12px; color: #aaa; margin-top: 4px; }

/* Section */
.section {
  background: #fff;
  border-radius: 12px;
  padding: 28px 28px 20px;
  margin-bottom: 28px;
  box-shadow: 0 2px 8px rgba(0,0,0,.06);
}
.section h2 {
  font-size: 17px; font-weight: 600; color: #1a237e;
  border-left: 4px solid #3949ab; padding-left: 12px;
  margin-bottom: 20px;
}

/* Chart grid */
.chart-row { display: flex; flex-wrap: wrap; gap: 20px; }
.chart-card {
  flex: 1 1 460px; min-width: 0;
  background: #fafbfc;
  border: 1px solid #e8eaf6;
  border-radius: 8px;
  padding: 16px;
}
.chart-card.full { flex: 1 1 100%; }
.chart-title {
  font-size: 13px; color: #5c6bc0; font-weight: 600;
  margin-bottom: 10px; padding-bottom: 8px;
  border-bottom: 1px solid #e8eaf6;
}

/* Placeholder */
.placeholder {
  height: 200px;
  display: flex; align-items: center; justify-content: center;
  color: #bbb; font-size: 14px;
  background: #f5f5f5; border-radius: 6px;
}

/* Decision summary */
.decision-section { background: #e8f5e9; border: 1px solid #a5d6a7; }
.decision-section h2 { border-left-color: #27ae60; color: #1b5e20; }
.decision-section.warn { background: #fff3e0; border-color: #ffcc80; }
.decision-section.warn h2 { border-left-color: #e65100; color: #bf360c; }
.bullets { list-style: none; margin-top: 4px; }
.bullets li {
  display: flex; gap: 12px; padding: 10px 0;
  border-bottom: 1px solid rgba(0,0,0,.06);
  font-size: 14px; line-height: 1.6;
}
.bullets li:last-child { border-bottom: none; }
.bullet-label {
  min-width: 72px; font-weight: 600;
  color: #388e3c; font-size: 13px; padding-top: 1px;
}
.decision-section.warn .bullet-label { color: #e65100; }

footer {
  text-align: center; padding: 20px;
  font-size: 12px; color: #bbb;
}
"""


# =============================================================================
# 主函数
# =============================================================================

def generate_dashboard(output_dir: Path) -> Path:
    """
    读取 output_dir/summary.json 和 output_dir/charts/ 中的图表文件，
    生成 output_dir/dashboard.html（固定文件名，每次覆盖）。

    可单独调用，无需流水线数据对象。
    """
    output_dir = Path(output_dir)
    charts_dir = output_dir / "charts"

    # ------------------------------------------------------------------
    # 1. 读 KPI 摘要
    # ------------------------------------------------------------------
    summary_file = output_dir / "summary.json"
    if summary_file.exists():
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
    else:
        summary = _default_summary()

    avg_price_wan       = summary.get("avg_price_wan", 0) or 0
    avg_rent_sqm        = summary.get("avg_rent_sqm", 0) or 0
    annual_yield_pct    = summary.get("annual_yield_pct", 0) or 0
    rent_sale_ratio     = summary.get("rent_sale_ratio", 0) or 0
    best_mode           = summary.get("best_mode", "—")
    best_irr_pct        = summary.get("best_irr_pct")           # None 或 float
    discount            = summary.get("discount_rate_pct", 5.5)
    house_count         = summary.get("house_count", 0)
    has_geo             = summary.get("has_geo", False)
    has_regression      = summary.get("has_regression", False)
    best_no_loan_mode   = summary.get("best_no_loan_mode", "—")
    best_nl_irr_pct     = summary.get("best_no_loan_irr_pct")   # None 或 float
    has_no_loan         = summary.get("has_no_loan", False)
    best_owned_mode        = summary.get("best_owned_mode", "—")
    best_owned_noi_wan     = summary.get("best_owned_noi_wan")       # None 或 float
    best_owned_monthly_wan = summary.get("best_owned_monthly_wan")   # None 或 float
    best_owned_cap_pct     = summary.get("best_owned_cap_rate_pct")  # None 或 float
    has_owned              = summary.get("has_owned", False)
    has_sensitivity        = summary.get("has_sensitivity", False)
    reno_data_source       = summary.get("reno_data_source", "unknown")
    reno_case_count        = summary.get("reno_case_count", 0) or 0
    reno_success_pages     = summary.get("reno_success_pages", 0) or 0
    reno_failed_pages      = summary.get("reno_failed_pages", 0) or 0
    reno_detail_success    = summary.get("reno_detail_success", 0) or 0
    reno_detail_failed     = summary.get("reno_detail_failed", 0) or 0
    gen_time            = summary.get("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
    default_mv          = int(round(avg_price_wan * 90)) if avg_price_wan else 307

    # ------------------------------------------------------------------
    # 2. KPI 卡片
    # ------------------------------------------------------------------
    irr_display = f"{best_irr_pct:.2f}%" if best_irr_pct is not None else "—"

    if best_irr_pct is None:
        decision_label, decision_class = "数据不足", ""
    elif best_irr_pct > discount:
        decision_label, decision_class = "建议关注", "decision"
    else:
        decision_label, decision_class = "建议观望", "warn"

    nl_irr_display = f"{best_nl_irr_pct:.2f}%" if best_nl_irr_pct is not None else "—"
    owned_monthly_display = f"{best_owned_monthly_wan:.2f}" if best_owned_monthly_wan is not None else "—"
    owned_cap_sub = (f"{best_owned_mode} · 年化{best_owned_cap_pct:.2f}%"
                     if best_owned_cap_pct is not None else "—")

    kpi_html = (
        '<div class="kpi-row">'
        + _kpi_card("二手房均价", f"{avg_price_wan:.2f}", "万/㎡", "海珠区中位数")
        + _kpi_card("月租金单价", f"{avg_rent_sqm:.1f}", "元/㎡/月", "海珠区中位数")
        + _kpi_card("年化毛收益率", f"{annual_yield_pct:.2f}", "%",
                    f"租售比 1:{int(rent_sale_ratio)}")
        + _kpi_card("最优商业模式", best_mode, "", f"IRR {irr_display}", "highlight")
        + _kpi_card("无贷款最优IRR", nl_irr_display, "",
                    f"{best_no_loan_mode} · 全款情景", "highlight")
        + _kpi_card("已持有月净收入", owned_monthly_display, "万元/月", owned_cap_sub)
        + _kpi_card("投资建议", decision_label, "", "", decision_class)
        + '</div>'
    )

    # ------------------------------------------------------------------
    # 3. Section 1：市场概况
    # ------------------------------------------------------------------
    sec1 = (
        '<div class="section"><h2>市场概况</h2>'
        '<div class="chart-row">'
        + _chart_block("二手房挂牌单价分布", _iframe(charts_dir, "房价分布"))
        + _chart_block("月租金单价分布",     _iframe(charts_dir, "租金分布"))
        + '</div>'
        '<div class="chart-row" style="margin-top:16px">'
        + _chart_block("租售比与年化收益率", _iframe(charts_dir, "租售比", height=440),
                       full_width=True)
        + '</div></div>'
    )

    # ------------------------------------------------------------------
    # 4. Section 2：DCF / IRR
    # ------------------------------------------------------------------
    sec2 = (
        '<div class="section"><h2>投资分析（DCF / IRR）</h2>'
        '<div class="chart-row">'
        + _chart_block("年度净现金流（20年）",   _iframe(charts_dir, "DCF现金流"))
        + _chart_block("三种模式 NPV / IRR 对比", _iframe(charts_dir, "NPV_IRR对比"))
        + '</div></div>'
    )

    # ------------------------------------------------------------------
    # 5. Section 3：装修成本
    # ------------------------------------------------------------------
    if reno_data_source == "to8to_real":
        reno_note = (
            f"数据来源：土巴兔当前整屋案例入口（xiaoguotu.to8to.com），"
            f"采集 {reno_case_count} 条；列表页成功 {reno_success_pages} 页，失败 {reno_failed_pages} 页；"
            f"详情页成功 {reno_detail_success} 条，失败 {reno_detail_failed} 条。"
        )
    elif reno_data_source == "mock_fallback":
        reno_note = (
            f"数据来源：模拟兜底。土巴兔当前入口未采集到可用真实案例，"
            f"本节仅用于流程演示和图表占位。"
        )
    else:
        reno_note = "数据来源：未记录。"

    sec3 = (
        '<div class="section"><h2>装修成本参考</h2>'
        f'<p style="font-size:13px;color:#666;margin-bottom:16px">{reno_note}</p>'
        '<div class="chart-row">'
        + _chart_block("装修档次综合单价与工期",
                       _iframe(charts_dir, "装修成本对比", height=400),
                       full_width=True)
        + '</div></div>'
    )

    # ------------------------------------------------------------------
    # 6. Section 4：无房贷情景分析（有图表才渲染）
    # ------------------------------------------------------------------
    if has_no_loan:
        nl_irr_note = f"无贷款最优模式为「{best_no_loan_mode}」，IRR {nl_irr_display}"
        sec_no_loan = (
            '<div class="section"><h2>无房贷情景分析</h2>'
            f'<p style="font-size:13px;color:#666;margin-bottom:16px">'
            f'假设房屋已完全还清贷款，以全款总价为初始投入，消除月供压力后的收益对比。{nl_irr_note}。</p>'
            '<div class="chart-row">'
            + _chart_block("有贷款 vs 无贷款 IRR 对比",
                           _iframe(charts_dir, "无贷款IRR对比", height=420))
            + _chart_block("年净现金流对比（最优模式）",
                           _iframe(charts_dir, "无贷款现金流", height=420))
            + '</div></div>'
        )
    else:
        sec_no_loan = ""

    # ------------------------------------------------------------------
    # 7. Section 5：已持有房产收益分析（有图表才渲染）
    # ------------------------------------------------------------------
    if has_owned:
        owned_desc = ""
        if best_owned_noi_wan is not None:
            owned_desc = (
                f"最优模式「{best_owned_mode}」年净收入 {best_owned_noi_wan:.1f} 万元"
                f"（月均 {best_owned_monthly_wan:.2f} 万元），"
                f"隐含年化回报率 {best_owned_cap_pct:.2f}%。"
            )
        sec_owned = (
            '<div class="section"><h2>已持有房产收益分析</h2>'
            f'<p style="font-size:13px;color:#666;margin-bottom:16px">'
            f'假设贷款已还清，不计原始购房成本，分析未来持有期间的纯现金流收益。{owned_desc}</p>'
            '<div class="chart-row">'
            + _chart_block("各模式年净收入（第1年 vs 第20年）",
                           _iframe(charts_dir, "已持有NOI对比", height=420))
            + _chart_block("各模式累计净收益（20年）",
                           _iframe(charts_dir, "已持有累计收益", height=420))
            + '</div></div>'
        )
    else:
        sec_owned = ""

    # ------------------------------------------------------------------
    # 8. Section 6：地理分析（有数据才渲染）
    # ------------------------------------------------------------------
    if has_geo or has_regression:
        geo_content = (
            '<div class="chart-row">'
            + (
                _chart_block("各小区 IRR 地理分布",
                             _iframe(charts_dir, "IRR地理分布", height=500))
                if has_geo else ""
            )
            + (
                _chart_block("IRR 与地铁/学校距离回归分析",
                             _img(charts_dir, "IRR回归分析"))
                if has_regression else ""
            )
            + '</div>'
        )
        sec5 = f'<div class="section"><h2>地理分析</h2>{geo_content}</div>'
    else:
        sec5 = (
            '<div class="section"><h2>地理分析</h2>'
            '<p style="color:#aaa;padding:20px 0">'
            '小区坐标尚未编码（Nominatim 未返回结果），地理特征图表暂缺。'
            '数据充足后重新运行流水线即可生成。'
            '</p></div>'
        )

    # ------------------------------------------------------------------
    # 9. Section 7：投资决策摘要
    # ------------------------------------------------------------------
    bullets = []

    # 房价水平
    if avg_price_wan > 6:
        price_note = f"均价 {avg_price_wan:.1f} 万/㎡，属于海珠核心区高价位段"
    elif avg_price_wan > 4:
        price_note = f"均价 {avg_price_wan:.1f} 万/㎡，处于海珠区中等价位段"
    elif avg_price_wan > 0:
        price_note = f"均价 {avg_price_wan:.1f} 万/㎡，性价比较高"
    else:
        price_note = "均价数据暂缺"
    bullets.append(("房价水平", price_note))

    # 租金回报
    if annual_yield_pct >= 5:
        yield_note = f"年化毛收益率 {annual_yield_pct:.2f}%，高于市场平均，租金回报优异"
    elif annual_yield_pct >= 3:
        yield_note = f"年化毛收益率 {annual_yield_pct:.2f}%，高于国内平均水平（约 1.5–2%）"
    elif annual_yield_pct > 0:
        yield_note = f"年化毛收益率 {annual_yield_pct:.2f}%，偏低，关注租金增长潜力"
    else:
        yield_note = "收益率数据暂缺"
    bullets.append(("租金回报", yield_note))

    # 推荐模式
    if best_irr_pct is not None and best_mode != "—":
        if best_irr_pct > discount:
            mode_note = (f"推荐「{best_mode}」模式，IRR {best_irr_pct:.2f}%，"
                         f"超过折现率基准（{discount:.1f}%），回报可覆盖资金成本")
        else:
            mode_note = (f"最优模式「{best_mode}」IRR {best_irr_pct:.2f}%，"
                         f"低于折现率基准（{discount:.1f}%），需审慎评估")
    else:
        mode_note = "IRR 数据不足，请确认 DCF 参数配置"
    bullets.append(("推荐模式", mode_note))

    # 位置优势
    if has_geo:
        bullets.append(("位置优势", "地理编码成功，详细区位分析见地理分析图表"))
    else:
        bullets.append(("位置优势", "地理编码覆盖不足，建议核查目标小区周边设施"))

    # 无贷款对比
    if best_irr_pct is not None and best_nl_irr_pct is not None:
        if best_nl_irr_pct > best_irr_pct:
            leverage_note = (
                f"若还清贷款，{best_no_loan_mode}模式 IRR 从 {best_irr_pct:.2f}% "
                f"升至 {best_nl_irr_pct:.2f}%（+{best_nl_irr_pct - best_irr_pct:.2f}pp），"
                "消除月供拖累后年均现金流全部转正"
            )
        else:
            leverage_note = (
                f"有贷款（首付30%）IRR {best_irr_pct:.2f}% 高于无贷款 {best_nl_irr_pct:.2f}%，"
                "系杠杆效应：以更少自有资本撬动更高比例回报；"
                "无贷款情景现金流从第一年即转正，风险更低"
            )
        bullets.append(("无贷款对比", leverage_note))

    # 已持有收益
    if best_owned_noi_wan is not None:
        owned_note = (
            f"不计购房本金，「{best_owned_mode}」每年纯净收入 {best_owned_noi_wan:.1f} 万元"
            f"（月均 {best_owned_monthly_wan:.2f} 万元），"
            f"相当于房产市值隐含年化回报率 {best_owned_cap_pct:.2f}%"
        )
        bullets.append(("持有收益", owned_note))

    # 风险提示
    bullets.append(("风险提示",
                    f"数据样本 {house_count} 条；租售比 1:{int(rent_sale_ratio)}，"
                    "高于 1:200 意味着纯租金回本周期长，建议结合房价增值预期综合判断"))

    bullets_html = "\n".join(
        f'<li><span class="bullet-label">{lbl}</span><span>{note}</span></li>'
        for lbl, note in bullets
    )
    warn_cls = " warn" if decision_class == "warn" else ""
    sec6 = (
        f'<div class="section decision-section{warn_cls}">'
        f'<h2>投资决策摘要</h2>'
        f'<ul class="bullets">{bullets_html}</ul>'
        f'</div>'
    )

    # ------------------------------------------------------------------
    # 10. Section 9：敏感性分析（有图表才渲染）
    # ------------------------------------------------------------------
    if has_sensitivity:
        sec_sensitivity = (
            '<div class="section"><h2>敏感性分析</h2>'
            '<p style="font-size:13px;color:#666;margin-bottom:16px">'
            '各关键参数对 IRR / NPV 的敏感程度。新增租金年增长敏感性，用于观察长期增长假设'
            '从保守到乐观变化时对三种模式 IRR 的影响；折现率行显示 NPV（万元），其余行显示 IRR（%）。'
            '龙卷图以民宿模式为基准，展示参数区间对 IRR 的影响幅度。'
            '</p>'
            '<div class="chart-row">'
            + _chart_block("IRR / NPV 敏感性分析表",
                           _iframe(charts_dir, "敏感性分析表", height=700),
                           full_width=True)
            + '</div>'
            '<div class="chart-row" style="margin-top:16px">'
            + _chart_block("IRR 敏感性龙卷图（民宿基准）",
                           _iframe(charts_dir, "敏感性龙卷图", height=440),
                           full_width=True)
            + '</div></div>'
        )
    else:
        sec_sensitivity = ""

    # ------------------------------------------------------------------
    # 11. 拼装完整 HTML
    # ------------------------------------------------------------------
    sec_calc = _calculator_section(default_mv)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>广州海珠区房产投资分析报告</title>
  <style>{_CSS}</style>
</head>
<body>
  <header>
    <h1>广州市海珠区 · 房产投资分析报告</h1>
    <p>生成时间：{gen_time} &nbsp;|&nbsp; 数据来源：贝壳找房 / 土巴兔 / OSM Overpass API</p>
  </header>

  <div class="container">
    {kpi_html}
    {sec1}
    {sec2}
    {sec3}
    {sec_no_loan}
    {sec_owned}
    {sec5}
    {sec6}
    {sec_sensitivity}
    {sec_calc}
  </div>

  <footer>本报告由自动化分析系统生成，仅供参考，不构成投资建议。</footer>
</body>
</html>"""

    out = output_dir / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    return out


# =============================================================================
# 交互式房产投资计算器
# =============================================================================

_CALCULATOR_TEMPLATE = """<style>
.calc-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:20px}
@media(max-width:768px){.calc-grid{grid-template-columns:1fr}}
.calc-group h3{font-size:13px;font-weight:600;color:#5c6bc0;margin-bottom:12px;
  padding-bottom:6px;border-bottom:1px solid #e8eaf6}
.calc-field{display:flex;align-items:center;gap:8px;margin-bottom:10px}
.calc-field label{min-width:110px;font-size:13px;color:#555;text-align:right}
.calc-field input{width:100px;padding:6px 10px;border:1px solid #ccc;border-radius:6px;
  font-size:13px;text-align:right;transition:border-color .2s}
.calc-field input:focus{border-color:#3949ab;outline:none}
.calc-field input:disabled{background:#f5f5f5;color:#bbb}
.calc-field .unit{font-size:12px;color:#888}
.calc-param-table{width:100%;border-collapse:collapse;font-size:13px}
.calc-param-table th{background:#e8eaf6;color:#3949ab;font-weight:600;
  padding:7px 8px;text-align:center;font-size:12px}
.calc-param-table td{padding:6px 6px;text-align:center;border-bottom:1px solid #f0f0f0}
.calc-param-table td:first-child{font-weight:600;color:#444;text-align:left;padding-left:10px}
.calc-param-table input{width:62px;padding:4px 6px;border:1px solid #ddd;
  border-radius:4px;font-size:12px;text-align:right}
.calc-param-table input:focus{border-color:#3949ab;outline:none}
.calc-btn-row{text-align:center;margin:8px 0 4px}
.calc-btn{background:linear-gradient(135deg,#3949ab,#1a237e);color:#fff;
  border:none;border-radius:8px;padding:11px 36px;font-size:14px;font-weight:600;
  cursor:pointer;letter-spacing:.5px;transition:opacity .2s}
.calc-btn:hover{opacity:.88}
.calc-result-table{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:16px}
.calc-result-table th{background:#e8eaf6;color:#3949ab;font-size:12px;
  padding:8px 12px;text-align:center}
.calc-result-table th:first-child{text-align:left}
.calc-result-table td{padding:9px 12px;border-bottom:1px solid #f0f0f0;text-align:center}
.calc-result-table td:first-child{font-size:12px;color:#666;text-align:left}
.calc-sell-card{background:#fff8e1;border:1px solid #ffcc80;border-radius:8px;
  padding:14px 18px;margin-bottom:16px;font-size:13px;
  display:flex;flex-wrap:wrap;gap:16px;align-items:center}
.calc-sell-card .sell-label{font-weight:600;color:#e65100;min-width:80px}
.calc-sell-item{color:#555}
.calc-sell-item strong{color:#333}
.calc-rec{border-radius:10px;padding:16px 20px;font-size:14px}
.calc-rec.green{background:#e8f5e9;border:1px solid #a5d6a7}
.calc-rec.orange{background:#fff3e0;border:1px solid #ffcc80}
.calc-rec.red{background:#ffebee;border:1px solid #ef9a9a}
.calc-rec .rec-title{font-size:15px;font-weight:700;margin-bottom:6px}
.calc-rec.green .rec-title{color:#1b5e20}
.calc-rec.orange .rec-title{color:#e65100}
.calc-rec.red .rec-title{color:#b71c1c}
.calc-rec .rec-body{color:#444;line-height:1.7}
.calc-note{font-size:11px;color:#bbb;margin-top:12px;text-align:right}
</style>

<div class="section">
  <h2>房产投资计算器</h2>
  <p style="font-size:13px;color:#666;margin-bottom:20px">
    输入您手中具体房产的信息，计算持有出租 vs 卖出的投资对比，获取个性化建议。
  </p>

  <div class="calc-grid">
    <div class="calc-group">
      <h3>基本信息</h3>
      <div class="calc-field">
        <label>建筑面积</label>
        <input type="number" id="c_sqm" value="90" min="1" step="1">
        <span class="unit">㎡</span>
      </div>
      <div class="calc-field">
        <label>市场估值</label>
        <input type="number" id="c_mv" value="__DEFAULT_MV__" min="1" step="1">
        <span class="unit">万元</span>
      </div>
      <div class="calc-field">
        <label>剩余贷款</label>
        <input type="number" id="c_loan" value="0" min="0" step="1" oninput="onLoanChange()">
        <span class="unit">万元 &nbsp;<span style="color:#bbb;font-size:11px">0=已还清</span></span>
      </div>
      <div class="calc-field">
        <label>贷款利率</label>
        <input type="number" id="c_lrate" value="3.15" min="0" step="0.01" disabled oninput="calcMortgage()">
        <span class="unit">%/年</span>
      </div>
      <div class="calc-field">
        <label>剩余还款年限</label>
        <input type="number" id="c_lyrs" value="25" min="1" max="30" step="1" disabled oninput="calcMortgage()">
        <span class="unit">年</span>
      </div>
      <div class="calc-field">
        <label>月供</label>
        <input type="number" id="c_mtg" value="0" min="0" step="10" disabled>
        <span class="unit">元/月 &nbsp;<span style="color:#bbb;font-size:11px">自动推算</span></span>
      </div>
      <div class="calc-field">
        <label>折现率</label>
        <input type="number" id="c_dr" value="5.5" min="0" step="0.1">
        <span class="unit">%/年</span>
      </div>
      <div class="calc-field">
        <label>分析年限</label>
        <input type="number" id="c_yrs" value="20" min="5" max="30" step="1">
        <span class="unit">年</span>
      </div>
    </div>

    <div class="calc-group">
      <h3>各模式租金参数（可编辑）</h3>
      <table class="calc-param-table">
        <thead>
          <tr>
            <th>模式</th>
            <th>月租<br>(元/㎡)</th>
            <th>出租率<br>(%)</th>
	            <th>运营<br>费率(%)</th>
	            <th>年增长<br>(%)</th>
	            <th>初始投入<br>(万)</th>
	            <th>退出Cap<br>(%)</th>
	          </tr>
	        </thead>
	        <tbody>
          <tr>
            <td>整租</td>
            <td><input type="number" id="c_rent_zt" value="55"  min="0" step="1"></td>
	            <td><input type="number" id="c_occ_zt"  value="95"  min="0" max="100" step="1"></td>
	            <td><input type="number" id="c_opex_zt" value="10"  min="0" max="100" step="0.5"></td>
	            <td><input type="number" id="c_gr_zt"   value="2"   min="0" step="0.1"></td>
	            <td><input type="number" id="c_capex_zt" value="0" min="0" step="1"></td>
	            <td><input type="number" id="c_tc_zt"    value="4" min="0.5" step="0.1"></td>
	          </tr>
	          <tr>
	            <td>民宿</td>
	            <td><input type="number" id="c_rent_ms" value="110" min="0" step="1"></td>
	            <td><input type="number" id="c_occ_ms"  value="65"  min="0" max="100" step="1"></td>
	            <td><input type="number" id="c_opex_ms" value="40"  min="0" max="100" step="0.5"></td>
	            <td><input type="number" id="c_gr_ms"   value="2"   min="0" step="0.1"></td>
	            <td><input type="number" id="c_capex_ms" value="9" min="0" step="1"></td>
	            <td><input type="number" id="c_tc_ms"    value="6" min="0.5" step="0.1"></td>
	          </tr>
	          <tr>
	            <td>隔断分租</td>
	            <td><input type="number" id="c_rent_gd" value="80"  min="0" step="1"></td>
	            <td><input type="number" id="c_occ_gd"  value="90"  min="0" max="100" step="1"></td>
	            <td><input type="number" id="c_opex_gd" value="15"  min="0" max="100" step="0.5"></td>
	            <td><input type="number" id="c_gr_gd"   value="1.5" min="0" step="0.1"></td>
	            <td><input type="number" id="c_capex_gd" value="0" min="0" step="1"></td>
	            <td><input type="number" id="c_tc_gd"    value="4" min="0.5" step="0.1"></td>
	          </tr>
        </tbody>
      </table>
      <p style="font-size:11px;color:#aaa;margin-top:10px">
	        月租金 = 每平米每月租金。年租金 = 面积 × 月租 × 12 × 出租率 × (1 - 运营费率)
      </p>
    </div>
  </div>

  <div class="calc-btn-row">
    <button class="calc-btn" onclick="calcInvestment()">开始计算</button>
  </div>

  <div id="calc-result" style="display:none">
    <h3 style="font-size:14px;font-weight:600;color:#3949ab;margin:24px 0 12px;
               border-left:3px solid #3949ab;padding-left:10px">持有策略对比</h3>
    <table class="calc-result-table">
      <thead>
        <tr>
          <th>指标</th>
          <th id="th_zt">整租</th>
          <th id="th_ms">民宿</th>
          <th id="th_gd">隔断分租</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>年净收入（万元/年）</td>
          <td id="r_noi_zt">—</td><td id="r_noi_ms">—</td><td id="r_noi_gd">—</td>
        </tr>
        <tr>
          <td>月均净现金流（元/月）</td>
          <td id="r_mcf_zt">—</td><td id="r_mcf_ms">—</td><td id="r_mcf_gd">—</td>
        </tr>
        <tr>
          <td>资本化率（%）</td>
          <td id="r_cap_zt">—</td><td id="r_cap_ms">—</td><td id="r_cap_gd">—</td>
        </tr>
        <tr>
          <td>IRR — 以市值为机会成本（%）</td>
          <td id="r_irr_zt">—</td><td id="r_irr_ms">—</td><td id="r_irr_gd">—</td>
        </tr>
        <tr>
          <td id="r_cum_label">20年累计净收益（万元）</td>
          <td id="r_cum_zt">—</td><td id="r_cum_ms">—</td><td id="r_cum_gd">—</td>
        </tr>
      </tbody>
    </table>

    <h3 style="font-size:14px;font-weight:600;color:#e65100;margin:20px 0 10px;
               border-left:3px solid #e65100;padding-left:10px">卖出参考</h3>
    <div class="calc-sell-card">
      <span class="sell-label">净得估算</span>
      <span class="calc-sell-item">扣除剩余贷款 + 1.5% 交易成本：<strong id="r_net">—</strong> 万元</span>
      <span class="calc-sell-item">若年化 5% 投资：<strong id="r_inc5">—</strong> 万/年</span>
      <span class="calc-sell-item">若年化 3% 存款：<strong id="r_inc3">—</strong> 万/年</span>
    </div>

    <div id="r_rec" class="calc-rec green">
      <div class="rec-title" id="r_rec_title">—</div>
      <div class="rec-body"  id="r_rec_body">—</div>
    </div>
    <p class="calc-note">
	      * IRR 以当前市场估值和初始投入为机会成本，各模式可单独设置退出资本化率。计算结果仅供参考，不构成投资建议。
    </p>
  </div>
</div>

<script>
function onLoanChange() {
  var loan = parseFloat(document.getElementById('c_loan').value) || 0;
  var has = loan > 0;
  ['c_lrate','c_lyrs','c_mtg'].forEach(function(id) {
    document.getElementById(id).disabled = !has;
  });
  if (has) { calcMortgage(); } else { document.getElementById('c_mtg').value = 0; }
}

function calcMortgage() {
  var loan = (parseFloat(document.getElementById('c_loan').value) || 0) * 10000;
  var rate = (parseFloat(document.getElementById('c_lrate').value) || 3.15) / 100;
  var yrs  = parseFloat(document.getElementById('c_lyrs').value) || 25;
  var mr = rate / 12, n = yrs * 12;
  if (loan <= 0 || mr <= 0) { document.getElementById('c_mtg').value = 0; return; }
  var pmt = loan * mr * Math.pow(1+mr,n) / (Math.pow(1+mr,n) - 1);
  document.getElementById('c_mtg').value = Math.round(pmt);
}

function _irr(cfs) {
  if (!cfs[0] || cfs[0] >= 0) return null;
  var r = 0.08;
  for (var i = 0; i < 200; i++) {
    var npv = 0, d = 0;
    for (var t = 0; t < cfs.length; t++) {
      var pv = cfs[t] / Math.pow(1+r,t);
      npv += pv; d -= t * pv / (1+r);
    }
    if (Math.abs(d) < 1e-12) break;
    var r2 = r - npv / d;
    if (Math.abs(r2 - r) < 1e-8) { r = r2; break; }
    if (r2 < -0.9 || r2 > 20) return null;
    r = r2;
  }
  return (isNaN(r) || r < -0.9 || r > 20) ? null : r;
}

function calcInvestment() {
  var sqm  = parseFloat(document.getElementById('c_sqm').value)  || 90;
  var mv   = parseFloat(document.getElementById('c_mv').value)   || __DEFAULT_MV__;
  var loan = parseFloat(document.getElementById('c_loan').value) || 0;
  var mtg  = parseFloat(document.getElementById('c_mtg').value)  || 0;
  var dr   = (parseFloat(document.getElementById('c_dr').value)  || 5.5) / 100;
  var yrs  = parseInt(document.getElementById('c_yrs').value)    || 20;
	  var modes   = ['整租','民宿','隔断分租'];
  var modeIds = ['zt','ms','gd'];
  var results = {};

  for (var i = 0; i < modes.length; i++) {
    var id   = modeIds[i];
    var rsqm = parseFloat(document.getElementById('c_rent_'+id).value) || 0;
    var occ  = (parseFloat(document.getElementById('c_occ_'+id).value)  || 0) / 100;
    var opex = (parseFloat(document.getElementById('c_opex_'+id).value) || 0) / 100;
    var gr   = (parseFloat(document.getElementById('c_gr_'+id).value)   || 0) / 100;
    var capex = (parseFloat(document.getElementById('c_capex_'+id).value) || 0) * 10000;
    var tc = ((parseFloat(document.getElementById('c_tc_'+id).value) || 4) / 100) || 0.04;
    var annMtg = mtg * 12;

    var cfs = [-(mv * 10000 + capex)];
    var cumNet = 0;
    for (var y = 1; y <= yrs; y++) {
      var noi = sqm * rsqm * 12 * occ * Math.pow(1+gr, y-1) * (1-opex);
      var net = noi - annMtg;
      cumNet += net; cfs.push(net);
    }
    var noiLast = sqm * rsqm * 12 * occ * Math.pow(1+gr, yrs-1) * (1-opex);
    cfs[cfs.length-1] += noiLast * (1+gr) / tc;

    var y1noi = sqm * rsqm * 12 * occ * (1-opex);
    results[modes[i]] = {
	      y1noi:     y1noi / 10000,
	      monthlyCF: (y1noi - annMtg) / 12,
	      capRate:   y1noi / (mv * 10000) * 100,
	      irr:       _irr(cfs),
	      cumNet:    (cumNet - capex) / 10000
	    };
    if (results[modes[i]].irr !== null) results[modes[i]].irr *= 100;
  }

  var sorted = modes.slice().sort(function(a,b) { return results[b].y1noi - results[a].y1noi; });
  var bestMode = sorted[0];
  var bestR = results[bestMode];

  function fmtN(v, dec) { return v == null ? 'N/A' : v.toFixed(dec); }
  function fmtCF(v) {
    var sign = v >= 0 ? '' : '-';
    return sign + Math.round(Math.abs(v)).toLocaleString('zh-CN');
  }

  modes.forEach(function(m, i) {
    var id = modeIds[i]; var r = results[m]; var best = (m === bestMode);
    document.getElementById('r_noi_'+id).textContent = fmtN(r.y1noi, 2);
    document.getElementById('r_mcf_'+id).textContent = fmtCF(r.monthlyCF);
    document.getElementById('r_cap_'+id).textContent = fmtN(r.capRate, 2) + '%';
    document.getElementById('r_irr_'+id).textContent = r.irr !== null ? fmtN(r.irr,2)+'%' : 'N/A';
    document.getElementById('r_cum_'+id).textContent = fmtN(r.cumNet, 1);
    var th = document.getElementById('th_'+id);
    th.style.background  = best ? '#c5cae9' : '#e8eaf6';
    th.style.fontWeight  = best ? '800' : '600';
    ['r_noi_','r_mcf_','r_cap_','r_irr_','r_cum_'].forEach(function(p) {
      var el = document.getElementById(p+id);
      el.style.fontWeight = best ? '700' : '';
      el.style.color      = best ? '#1a237e' : '';
      el.style.background = best ? '#f3f4ff' : '';
    });
  });

  document.getElementById('r_cum_label').textContent = yrs + '年累计净收益（万元）';

  var netProc = mv - loan - mv * 0.015;
  var inc5 = netProc * 0.05, inc3 = netProc * 0.03;
  document.getElementById('r_net').textContent  = netProc.toFixed(1);
  document.getElementById('r_inc5').textContent = inc5.toFixed(2);
  document.getElementById('r_inc3').textContent = inc3.toFixed(2);

  var drPct = dr * 100, recClass, recTitle, recBody;
  if (bestR.irr !== null && bestR.irr > drPct) {
    recClass = 'green';
    recTitle = '建议持有出租（' + bestMode + '）';
    recBody  = '「' + bestMode + '」IRR ' + fmtN(bestR.irr,2) + '% 高于折现率基准 ' + drPct.toFixed(1) +
               '%，持有回报优于等风险投资。年净收入 ' + fmtN(bestR.y1noi,2) +
               ' 万元（月均 ' + fmtCF(bestR.monthlyCF) + ' 元），资本化率 ' + fmtN(bestR.capRate,2) + '%。';
  } else if (bestR.y1noi > inc5) {
    recClass = 'orange';
    recTitle = '持有略优，但优势有限';
    recBody  = '「' + bestMode + '」年净收入 ' + fmtN(bestR.y1noi,2) + ' 万元高于卖出后5%年化 ' +
               inc5.toFixed(2) + ' 万/年；但 IRR ' + (bestR.irr !== null ? fmtN(bestR.irr,2)+'%' : 'N/A') +
               ' 未超折现率基准（' + drPct.toFixed(1) + '%），需综合考虑管理精力与流动性。';
  } else {
    recClass = 'red';
    recTitle = '建议出售';
    recBody  = '最优出租「' + bestMode + '」年净收入 ' + fmtN(bestR.y1noi,2) +
               ' 万元，低于卖出后5%年化 ' + inc5.toFixed(2) + ' 万/年。卖出净得约 ' +
               netProc.toFixed(1) + ' 万元，资金效率更高。';
  }

  var rec = document.getElementById('r_rec');
  rec.className = 'calc-rec ' + recClass;
  document.getElementById('r_rec_title').textContent = recTitle;
  document.getElementById('r_rec_body').textContent  = recBody;
  document.getElementById('calc-result').style.display = 'block';
}
</script>
"""


def _calculator_section(default_mv: float) -> str:
    mv = int(round(default_mv)) if default_mv else 307
    return _CALCULATOR_TEMPLATE.replace("__DEFAULT_MV__", str(mv))
