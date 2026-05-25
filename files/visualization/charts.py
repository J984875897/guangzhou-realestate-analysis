# =============================================================================
# visualization/charts.py
# 广州市海珠区房产分析可视化模块
#
# 生成 8 张图表（Matplotlib PNG + Plotly 交互 HTML）：
#   1. 二手房单价分布
#   2. 租金单价分布
#   3. 租售比与年化收益率
#   4. 三种商业模式 DCF 现金流（20年）
#   5. NPV / IRR 对比
#   6. 装修档次成本与工期对比
#   7. 各小区 IRR 地理分布（叠加地铁/学校）
#   8. IRR 与地铁/学校距离的回归分析
# =============================================================================

from pathlib import Path

import matplotlib
matplotlib.use("Agg")   # 非交互后端，服务器/脚本环境必须
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

try:
    import plotly.graph_objects as go
    import plotly.io as pio
    from plotly.subplots import make_subplots
    _PLOTLY_AVAILABLE = True
except ImportError:
    _PLOTLY_AVAILABLE = False
    print("[可视化] plotly 未安装，跳过交互式 HTML 图表；只输出 PNG")


# 全局中文字体配置（macOS 可用字体：STHeiti / Hiragino Sans GB / Songti SC）
plt.rcParams.update({
    "font.family":    ["STHeiti", "Hiragino Sans GB", "Songti SC", "DejaVu Sans"],
    "axes.unicode_minus": False,
})

# 配色方案
COLORS = {
    "整租":   "#4C72B0",
    "民宿":   "#DD8452",
    "隔断分租": "#55A868",
    "经济型": "#4C72B0",
    "中档":   "#55A868",
    "高档":   "#C44E52",
    "豪装":   "#8172B2",
    "accent": "#E74C3C",
    "bg":     "#F8F9FA",
}


def _hex_alpha(hex_color: str, alpha: float) -> str:
    """Convert '#RRGGBB' + alpha (0–1) to 'rgba(r,g,b,a)' for Plotly."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# =============================================================================
# 对外接口
# =============================================================================

def generate_all_charts(
    house_df: pd.DataFrame,
    rent_df: pd.DataFrame,
    reno_summary: pd.DataFrame,
    dcf_results: dict,
    dcf_params: dict,
    output_dir: Path,
    geo_df: pd.DataFrame = None,
    metro_df: pd.DataFrame = None,
    school_df: pd.DataFrame = None,
    regression_results: dict = None,
    no_loan_results: dict = None,
    owned_results: dict = None,
) -> dict[str, Path]:
    """
    生成全部图表，保存到 output_dir。

    geo_df / metro_df / school_df / regression_results 为可选参数；
    传入时额外生成图表 7（地理分布）和图表 8（回归分析）。

    返回 {图表名: 文件路径} 字典。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}

    tasks = [
        ("房价分布",     _chart_price_distribution,  (house_df,)),
        ("租金分布",     _chart_rent_distribution,   (rent_df,)),
        ("租售比",       _chart_rent_sale_ratio,     (house_df, rent_df)),
        ("DCF现金流",    _chart_dcf_cashflow,        (dcf_results, dcf_params)),
        ("NPV_IRR对比",  _chart_npv_irr,             (dcf_results,)),
        ("装修成本对比", _chart_renovation_cost,     (reno_summary,)),
    ]

    # 可选：地理分布图 + 回归分析图
    if geo_df is not None and not geo_df.empty:
        tasks.append(("IRR地理分布", _chart_geo_irr_map,
                      (geo_df, metro_df, school_df)))
    if regression_results and geo_df is not None:
        tasks.append(("IRR回归分析", _chart_regression_analysis,
                      (regression_results, geo_df)))
    if no_loan_results and dcf_results:
        tasks.append(("无贷款IRR对比", _chart_no_loan_irr,
                      (dcf_results, no_loan_results, dcf_params)))
        tasks.append(("无贷款现金流", _chart_no_loan_cashflow,
                      (dcf_results, no_loan_results, dcf_params)))
    if owned_results:
        tasks.append(("已持有NOI对比", _chart_owned_noi, (owned_results, dcf_params)))
        tasks.append(("已持有累计收益", _chart_owned_cumulative, (owned_results, dcf_params)))

    for name, func, args in tasks:
        try:
            png_path  = output_dir / f"{name}.png"
            html_path = output_dir / f"{name}.html"

            result = func(*args)
            # _placeholder_chart 只返回一个 fig，其余函数返回 (fig, fig_plotly)
            if isinstance(result, tuple):
                fig_mpl, fig_plotly = result
            else:
                fig_mpl, fig_plotly = result, None

            if fig_mpl is not None:
                fig_mpl.savefig(png_path, dpi=150, bbox_inches="tight",
                                facecolor=COLORS["bg"])
                plt.close(fig_mpl)
                paths[f"{name}_png"] = png_path
                print(f"  [图表] {name}.png 已保存")

            if fig_plotly is not None and _PLOTLY_AVAILABLE:
                pio.write_html(fig_plotly, str(html_path), auto_open=False)
                paths[f"{name}_html"] = html_path
                print(f"  [图表] {name}.html 已保存")

        except Exception as e:
            print(f"  [警告] 图表 {name} 生成失败: {e}")

    return paths


# =============================================================================
# 图表1：二手房单价分布
# =============================================================================

def _chart_price_distribution(house_df: pd.DataFrame):
    if house_df.empty or "unit_price" not in house_df.columns:
        return None, None

    prices = house_df["unit_price"].dropna() / 10000   # 转为 万元/㎡

    fig, ax = plt.subplots(figsize=(10, 5), facecolor=COLORS["bg"])
    ax.set_facecolor(COLORS["bg"])

    ax.hist(prices, bins=30, color=COLORS["整租"], edgecolor="white",
            linewidth=0.5, alpha=0.85, label="小区数量")

    median_val = prices.median()
    ax.axvline(median_val, color=COLORS["accent"], linewidth=2,
               linestyle="--", label=f"中位数 {median_val:.1f} 万元/㎡")

    ax.set_title("广州市海珠区 二手房挂牌单价分布", fontsize=14, pad=12)
    ax.set_xlabel("挂牌单价（万元/㎡）", fontsize=11)
    ax.set_ylabel("小区数量", fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    _add_data_note(ax, house_df)

    fig_plotly = None
    if _PLOTLY_AVAILABLE:
        fig_plotly = go.Figure()
        fig_plotly.add_trace(go.Histogram(
            x=prices, nbinsx=30,
            marker_color=COLORS["整租"],
            opacity=0.8,
            name="小区数量",
        ))
        fig_plotly.add_vline(
            x=median_val,
            line_dash="dash", line_color=COLORS["accent"],
            annotation_text=f"中位数 {median_val:.2f} 万/㎡",
            annotation_position="top right",
        )
        fig_plotly.update_layout(
            title="广州市海珠区 二手房挂牌单价分布",
            xaxis_title="挂牌单价（万元/㎡）",
            yaxis_title="小区数量",
            template="plotly_white",
        )

    return fig, fig_plotly


# =============================================================================
# 图表2：租金单价分布
# =============================================================================

def _chart_rent_distribution(rent_df: pd.DataFrame):
    if rent_df is None or rent_df.empty or "monthly_rent_per_sqm" not in rent_df.columns:
        return _placeholder_chart("租金数据暂缺（爬虫未采集到数据）"), None

    rents = rent_df["monthly_rent_per_sqm"].dropna()

    fig, ax = plt.subplots(figsize=(10, 5), facecolor=COLORS["bg"])
    ax.set_facecolor(COLORS["bg"])

    ax.hist(rents, bins=25, color=COLORS["民宿"], edgecolor="white",
            linewidth=0.5, alpha=0.85)

    median_val = rents.median()
    ax.axvline(median_val, color=COLORS["accent"], linewidth=2,
               linestyle="--", label=f"中位数 {median_val:.1f} 元/㎡/月")

    ax.set_title("广州市海珠区 月租金单价分布", fontsize=14, pad=12)
    ax.set_xlabel("月租金单价（元/㎡/月）", fontsize=11)
    ax.set_ylabel("房源数量", fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    _add_data_note(ax, rent_df)

    fig_plotly = None
    if _PLOTLY_AVAILABLE:
        fig_plotly = go.Figure(go.Histogram(
            x=rents, nbinsx=25,
            marker_color=COLORS["民宿"],
            opacity=0.8,
        ))
        fig_plotly.add_vline(
            x=median_val,
            line_dash="dash", line_color=COLORS["accent"],
            annotation_text=f"中位数 {median_val:.1f} 元/㎡/月",
        )
        fig_plotly.update_layout(
            title="广州市海珠区 月租金单价分布",
            xaxis_title="月租金单价（元/㎡/月）",
            yaxis_title="房源数量",
            template="plotly_white",
        )

    return fig, fig_plotly


# =============================================================================
# 图表3：租售比与年化收益率
# =============================================================================

def _chart_rent_sale_ratio(house_df: pd.DataFrame, rent_df: pd.DataFrame):
    has_rent = (rent_df is not None and not rent_df.empty
                and "monthly_rent_per_sqm" in rent_df.columns)

    avg_price = house_df["unit_price"].median() if not house_df.empty else 40000
    avg_rent  = rent_df["monthly_rent_per_sqm"].median() if has_rent else 55.0

    ratio        = avg_price / avg_rent
    annual_yield = avg_rent * 12 / avg_price * 100

    # 参考基准（国际一般标准 / 广州典型值）
    benchmarks = {"健康上限 1:200": 200, "广州平均 ~1:450": 450}

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), facecolor=COLORS["bg"])
    fig.suptitle("广州市海珠区 租售比分析", fontsize=14, y=1.02)

    # 左图：租售比横向对比
    ax = axes[0]
    ax.set_facecolor(COLORS["bg"])
    labels   = ["海珠区实测"] + list(benchmarks.keys())
    values   = [ratio] + list(benchmarks.values())
    bar_cols = [COLORS["accent"]] + [COLORS["整租"]] * len(benchmarks)

    bars = ax.barh(labels, values, color=bar_cols, edgecolor="white", height=0.5)
    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + 5, bar.get_y() + bar.get_height() / 2,
                f"1:{val:.0f}", va="center", fontsize=10)

    ax.set_xlabel("租售比（1:N，N 越小越划算）", fontsize=10)
    ax.set_title("租售比对比", fontsize=12)
    ax.grid(axis="x", alpha=0.3)
    ax.invert_yaxis()

    # 右图：年化收益率仪表盘式展示
    ax2 = axes[1]
    ax2.set_facecolor(COLORS["bg"])
    zones = [("高收益\n>5%", 5, 10, "#55A868"),
             ("合理\n3-5%",  3,  5, "#4C72B0"),
             ("偏低\n<3%",   0,  3, "#C44E52")]

    for label, lo, hi, color in zones:
        ax2.barh([label], [hi - lo], left=lo, color=color, alpha=0.7, height=0.4)

    ax2.axvline(annual_yield, color=COLORS["accent"], linewidth=2.5,
                linestyle="-", label=f"实测 {annual_yield:.2f}%")
    ax2.set_xlabel("年化毛收益率（%）", fontsize=10)
    ax2.set_title("年化收益率区间", fontsize=12)
    ax2.legend(fontsize=10)
    ax2.set_xlim(0, 10)
    ax2.grid(axis="x", alpha=0.3)

    if not has_rent:
        for ax_ in axes:
            ax_.text(0.5, -0.15, "⚠ 租金使用估算值，建议运行租房爬虫后重新生成",
                     transform=ax_.transAxes, ha="center", fontsize=9,
                     color="gray")

    plt.tight_layout()

    fig_plotly = None
    if _PLOTLY_AVAILABLE:
        fig_plotly = go.Figure()
        fig_plotly.add_trace(go.Bar(
            x=values, y=labels,
            orientation="h",
            marker_color=bar_cols,
            text=[f"1:{v:.0f}" for v in values],
            textposition="outside",
        ))
        fig_plotly.update_layout(
            title=f"广州市海珠区 租售比对比（实测 1:{ratio:.0f}，年化收益 {annual_yield:.2f}%）",
            xaxis_title="租售比（1:N）",
            template="plotly_white",
        )

    return fig, fig_plotly


# =============================================================================
# 图表4：三种商业模式 DCF 现金流（20年趋势）
# =============================================================================

def _chart_dcf_cashflow(dcf_results: dict, dcf_params: dict):
    if not dcf_results:
        return _placeholder_chart("DCF 数据缺失"), None

    years = list(range(1, dcf_params.get("years", 20) + 1))

    fig, ax = plt.subplots(figsize=(12, 6), facecolor=COLORS["bg"])
    ax.set_facecolor(COLORS["bg"])

    for mode, result in dcf_results.items():
        cfs = [cf / 10000 for cf in result["cash_flows"]]   # 转为 万元
        color = COLORS.get(mode, "#888888")
        ax.plot(years, cfs, marker="o", markersize=4, linewidth=2,
                color=color, label=mode)

    ax.axhline(0, color="black", linewidth=0.8, linestyle="--", alpha=0.5)
    ax.set_title("三种商业模式 年度净现金流（20年）", fontsize=14, pad=12)
    ax.set_xlabel("投资年份", fontsize=11)
    ax.set_ylabel("年度净现金流（万元）", fontsize=11)
    ax.legend(fontsize=10, loc="lower right")
    ax.grid(alpha=0.3)
    ax.xaxis.set_major_locator(mticker.MultipleLocator(2))

    sqm    = dcf_params.get("sqm", 90)
    note   = f"房屋面积：{sqm}㎡ | 首付：{dcf_params.get('down_pct',0.3)*100:.0f}% | 贷款利率：{dcf_params.get('loan_rate',0.0315)*100:.2f}%"
    ax.text(0.01, 0.02, note, transform=ax.transAxes, fontsize=8, color="gray")

    fig_plotly = None
    if _PLOTLY_AVAILABLE:
        fig_plotly = go.Figure()
        for mode, result in dcf_results.items():
            cfs = [cf / 10000 for cf in result["cash_flows"]]
            fig_plotly.add_trace(go.Scatter(
                x=years, y=cfs,
                mode="lines+markers",
                name=mode,
                line=dict(color=COLORS.get(mode, "#888"), width=2),
            ))
        fig_plotly.add_hline(y=0, line_dash="dash", line_color="black", opacity=0.4)
        fig_plotly.update_layout(
            title="三种商业模式 年度净现金流（20年）",
            xaxis_title="投资年份",
            yaxis_title="年度净现金流（万元）",
            template="plotly_white",
            hovermode="x unified",
        )

    return fig, fig_plotly


# =============================================================================
# 图表5：NPV / IRR 对比
# =============================================================================

def _chart_npv_irr(dcf_results: dict):
    if not dcf_results:
        return _placeholder_chart("DCF 数据缺失"), None

    modes = list(dcf_results.keys())
    npvs  = [dcf_results[m]["npv"] / 10000 for m in modes]    # 万元
    irrs  = [dcf_results[m]["irr"] * 100 for m in modes]       # %
    colors = [COLORS.get(m, "#888888") for m in modes]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), facecolor=COLORS["bg"])
    fig.suptitle("三种商业模式 投资回报对比", fontsize=14, y=1.02)

    # NPV 柱状图
    ax1.set_facecolor(COLORS["bg"])
    bars = ax1.bar(modes, npvs, color=colors, edgecolor="white", width=0.5)
    ax1.axhline(0, color="black", linewidth=0.8, linestyle="--", alpha=0.5)
    for bar, val in zip(bars, npvs):
        y_pos = val + (max(npvs) * 0.02) if val >= 0 else val - (max(abs(v) for v in npvs) * 0.04)
        ax1.text(bar.get_x() + bar.get_width() / 2, y_pos,
                 f"{val:+.0f}万", ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax1.set_title("净现值 NPV（20年期）", fontsize=12)
    ax1.set_ylabel("净现值（万元）", fontsize=10)
    ax1.grid(axis="y", alpha=0.3)

    # IRR 柱状图
    ax2.set_facecolor(COLORS["bg"])
    bars2 = ax2.bar(modes, irrs, color=colors, edgecolor="white", width=0.5)
    # 绘制折现率基准线
    discount_rate_pct = 5.5   # 与 CONFIG["dcf_params"]["discount_rate"] 对应
    ax2.axhline(discount_rate_pct, color=COLORS["accent"], linewidth=1.5,
                linestyle="--", label=f"折现率基准 {discount_rate_pct}%")
    for bar, val in zip(bars2, irrs):
        ax2.text(bar.get_x() + bar.get_width() / 2,
                 val + 0.1, f"{val:.2f}%",
                 ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax2.set_title("内部收益率 IRR", fontsize=12)
    ax2.set_ylabel("IRR（%）", fontsize=10)
    ax2.legend(fontsize=9)
    ax2.grid(axis="y", alpha=0.3)

    plt.tight_layout()

    fig_plotly = None
    if _PLOTLY_AVAILABLE:
        fig_plotly = make_subplots(
            rows=1, cols=2,
            subplot_titles=("净现值 NPV（20年期）", "内部收益率 IRR"),
        )
        fig_plotly.add_trace(go.Bar(
            name="NPV（万元）",
            x=modes, y=npvs,
            marker_color=colors,
            text=[f"{v:+.0f}万" for v in npvs],
            textposition="outside",
            showlegend=False,
        ), row=1, col=1)
        fig_plotly.add_trace(go.Bar(
            name="IRR（%）",
            x=modes, y=irrs,
            marker_color=[_hex_alpha(c, 0.6) for c in colors],
            text=[f"{v:.2f}%" for v in irrs],
            textposition="outside",
            showlegend=False,
        ), row=1, col=2)
        fig_plotly.add_hline(
            y=discount_rate_pct, line_dash="dash",
            line_color=COLORS["accent"], line_width=1.5,
            annotation_text=f"折现率 {discount_rate_pct}%",
            annotation_position="top right",
            row=1, col=2,
        )
        fig_plotly.update_yaxes(title_text="净现值（万元）", row=1, col=1)
        fig_plotly.update_yaxes(title_text="IRR（%）", row=1, col=2)
        fig_plotly.update_layout(
            title="三种商业模式 NPV / IRR 对比",
            template="plotly_white",
        )

    return fig, fig_plotly


# =============================================================================
# 图表6：装修档次成本与工期对比
# =============================================================================

def _chart_renovation_cost(reno_summary: pd.DataFrame):
    if reno_summary is None or reno_summary.empty:
        return _placeholder_chart("装修数据缺失"), None

    levels = reno_summary.index.tolist()
    prices = reno_summary.get("中位单价", reno_summary.get("平均单价", pd.Series())).values
    days   = reno_summary.get("平均工期", pd.Series([0] * len(levels))).values

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), facecolor=COLORS["bg"])
    fig.suptitle("广州市装修成本与工期对比", fontsize=14, y=1.02)

    level_colors = [COLORS.get(l, "#888888") for l in levels]

    # 左图：单价柱状图
    ax1.set_facecolor(COLORS["bg"])
    bars = ax1.bar(levels, prices, color=level_colors, edgecolor="white", width=0.5)
    for bar, val in zip(bars, prices):
        ax1.text(bar.get_x() + bar.get_width() / 2,
                 bar.get_height() + 15, f"{val:.0f}",
                 ha="center", va="bottom", fontsize=10)
    ax1.set_title("装修综合单价（元/㎡）", fontsize=12)
    ax1.set_ylabel("单价（元/㎡）", fontsize=10)
    ax1.grid(axis="y", alpha=0.3)

    # 右图：工期柱状图
    ax2.set_facecolor(COLORS["bg"])
    if any(d > 0 for d in days):
        bars2 = ax2.bar(levels, days, color=level_colors, edgecolor="white", width=0.5)
        for bar, val in zip(bars2, days):
            ax2.text(bar.get_x() + bar.get_width() / 2,
                     bar.get_height() + 0.5, f"{val:.0f}天",
                     ha="center", va="bottom", fontsize=10)
        ax2.set_ylabel("施工工期（天）", fontsize=10)
    else:
        ax2.text(0.5, 0.5, "工期数据暂缺", transform=ax2.transAxes,
                 ha="center", va="center", fontsize=12, color="gray")
    ax2.set_title("施工工期（天）", fontsize=12)
    ax2.grid(axis="y", alpha=0.3)

    plt.tight_layout()

    fig_plotly = None
    if _PLOTLY_AVAILABLE:
        fig_plotly = go.Figure()
        fig_plotly.add_trace(go.Bar(
            name="综合单价（元/㎡）",
            x=levels, y=prices,
            marker_color=level_colors,
            text=[f"{v:.0f}" for v in prices],
            textposition="outside",
        ))
        fig_plotly.update_layout(
            title="装修档次综合单价对比",
            xaxis_title="装修档次",
            yaxis_title="单价（元/㎡）",
            template="plotly_white",
        )

    return fig, fig_plotly


# =============================================================================
# 图表7：各小区 IRR 地理分布（叠加地铁/学校标记）
# =============================================================================

def _chart_geo_irr_map(
    geo_df: pd.DataFrame,
    metro_df: pd.DataFrame = None,
    school_df: pd.DataFrame = None,
):
    """
    散点图：各小区位置以 IRR（整租）着色，叠加地铁站（▲）和学校（★）标记。
    geo_df 需含 wgs_lng, wgs_lat, irr_整租 列。
    """
    irr_col = "irr_整租"
    has_irr = (irr_col in geo_df.columns and
               geo_df[irr_col].notna().any() and
               "wgs_lng" in geo_df.columns)

    if not has_irr:
        return _placeholder_chart("地理坐标或 IRR 数据缺失，无法生成地图"), None

    sub = geo_df.dropna(subset=["wgs_lng", "wgs_lat", irr_col])
    irr_vals = sub[irr_col] * 100   # 转为 %

    fig, ax = plt.subplots(figsize=(10, 8), facecolor=COLORS["bg"])
    ax.set_facecolor(COLORS["bg"])

    # 小区散点（IRR 着色）
    sc = ax.scatter(
        sub["wgs_lng"], sub["wgs_lat"],
        c=irr_vals, cmap="RdYlGn",
        s=60, alpha=0.85, zorder=3,
        vmin=irr_vals.quantile(0.05),
        vmax=irr_vals.quantile(0.95),
    )
    cbar = plt.colorbar(sc, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("IRR（整租，%）", fontsize=10)

    # 地铁站标记
    if metro_df is not None and not metro_df.empty:
        ax.scatter(
            metro_df["lng"], metro_df["lat"],
            marker="^", s=80, color="#2196F3",
            zorder=4, alpha=0.9, label="地铁站",
        )

    # 学校标记
    if school_df is not None and not school_df.empty:
        ax.scatter(
            school_df["lng"], school_df["lat"],
            marker="*", s=100, color="#FF9800",
            zorder=4, alpha=0.9, label="学校",
        )

    ax.set_title("广州市海珠区 各小区 IRR 地理分布", fontsize=14, pad=12)
    ax.set_xlabel("经度", fontsize=10)
    ax.set_ylabel("纬度", fontsize=10)
    if metro_df is not None or school_df is not None:
        ax.legend(fontsize=10, loc="upper left")
    ax.grid(alpha=0.3)

    # Plotly 交互版本
    fig_plotly = None
    if _PLOTLY_AVAILABLE:
        hover_text = [
            f"{row.get('name', '')}<br>IRR: {row[irr_col]*100:.2f}%<br>单价: {row.get('unit_price', 0)/10000:.1f}万/㎡"
            for _, row in sub.iterrows()
        ]
        fig_plotly = go.Figure()
        fig_plotly.add_trace(go.Scatter(
            x=sub["wgs_lng"], y=sub["wgs_lat"],
            mode="markers",
            name="小区",
            text=hover_text,
            hoverinfo="text",
            marker=dict(
                color=irr_vals,
                colorscale="RdYlGn",
                size=8,
                colorbar=dict(title="IRR（整租，%）"),
                showscale=True,
            ),
        ))
        if metro_df is not None and not metro_df.empty:
            fig_plotly.add_trace(go.Scatter(
                x=metro_df["lng"], y=metro_df["lat"],
                mode="markers",
                name="地铁站",
                text=metro_df.get("name", pd.Series()).tolist(),
                hoverinfo="text",
                marker=dict(symbol="triangle-up", size=10, color="#2196F3"),
            ))
        if school_df is not None and not school_df.empty:
            fig_plotly.add_trace(go.Scatter(
                x=school_df["lng"], y=school_df["lat"],
                mode="markers",
                name="学校",
                text=school_df.get("name", pd.Series()).tolist(),
                hoverinfo="text",
                marker=dict(symbol="star", size=10, color="#FF9800"),
            ))
        fig_plotly.update_layout(
            title="广州市海珠区 各小区 IRR 地理分布",
            xaxis_title="经度", yaxis_title="纬度",
            template="plotly_white",
        )

    return fig, fig_plotly


# =============================================================================
# 图表8：IRR 与地铁/学校距离的回归分析
# =============================================================================

def _chart_regression_analysis(
    regression_results: dict,
    geo_df: pd.DataFrame,
):
    """
    上排：IRR vs 地铁距离 / IRR vs 学校距离的散点图 + OLS 拟合线（整租模式）
    下排：三种模式的回归系数柱状图（含 95% 置信区间误差条）
    """
    if not regression_results:
        return _placeholder_chart("回归结果缺失"), None

    modes   = list(regression_results.keys())
    success = [m for m in modes if regression_results[m].get("success")]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10), facecolor=COLORS["bg"])
    fig.suptitle("IRR 与地铁/学校距离的相关性回归分析", fontsize=14, y=1.01)

    # ── 上排：散点 + 拟合线（使用第一个成功的模式） ──
    ref_mode = success[0] if success else modes[0]
    irr_col  = f"irr_{ref_mode}"
    sub = geo_df.dropna(subset=[irr_col]).copy() if irr_col in geo_df.columns else pd.DataFrame()
    irr_pct = sub[irr_col] * 100 if not sub.empty else pd.Series(dtype=float)

    for ax_idx, (feat_col, feat_label) in enumerate([
        ("dist_nearest_metro",  "最近地铁距离（km）"),
        ("dist_nearest_school", "最近学校距离（km）"),
    ]):
        ax = axes[0][ax_idx]
        ax.set_facecolor(COLORS["bg"])

        if not sub.empty and feat_col in sub.columns:
            x = sub[feat_col].dropna()
            y = irr_pct.loc[x.index]
            ax.scatter(x, y, alpha=0.5, s=30,
                       color=COLORS.get(ref_mode, "#4C72B0"), label="各小区")

            # OLS 拟合线
            valid = pd.DataFrame({"x": x, "y": y}).dropna()
            if len(valid) >= 3:
                coeffs = np.polyfit(valid["x"], valid["y"], 1)
                x_line = np.linspace(valid["x"].min(), valid["x"].max(), 100)
                ax.plot(x_line, np.polyval(coeffs, x_line),
                        color=COLORS["accent"], linewidth=2, label="拟合线")
        else:
            ax.text(0.5, 0.5, "数据不足", transform=ax.transAxes,
                    ha="center", va="center", color="gray")

        ax.set_xlabel(feat_label, fontsize=10)
        ax.set_ylabel(f"IRR（{ref_mode}，%）", fontsize=10)
        ax.set_title(f"IRR vs {feat_label}", fontsize=11)
        ax.legend(fontsize=9)
        ax.grid(alpha=0.3)

    # ── 下排：各模式回归系数柱状图 ──
    key_features = ["dist_nearest_metro", "dist_nearest_school", "metro_count_1km", "school_count_1km"]
    feature_short = {
        "dist_nearest_metro":   "地铁距离",
        "dist_nearest_school":  "学校距离",
        "metro_count_1km":      "地铁数量",
        "school_count_1km":     "学校数量",
    }

    for ax_idx, mode in enumerate(modes[:2]):   # 最多展示前两个模式
        ax = axes[1][ax_idx]
        ax.set_facecolor(COLORS["bg"])
        result = regression_results.get(mode, {})

        if not result.get("success"):
            ax.text(0.5, 0.5, f"{mode}：回归未成功", transform=ax.transAxes,
                    ha="center", va="center", color="gray")
            ax.set_title(f"{mode} 回归系数", fontsize=11)
            continue

        params   = result["params"]
        conf_int = result["conf_int"]
        feats    = [f for f in key_features if f in params.index]
        labels   = [feature_short.get(f, f) for f in feats]
        coefs    = [params[f] for f in feats]
        # 误差条：95% CI 半宽
        errs = [
            (params[f] - conf_int.loc[f, 0],
             conf_int.loc[f, 1] - params[f])
            for f in feats
        ]
        err_neg = [e[0] for e in errs]
        err_pos = [e[1] for e in errs]
        bar_colors = [COLORS["accent"] if c < 0 else COLORS["整租"] for c in coefs]

        bars = ax.bar(labels, coefs, color=bar_colors, edgecolor="white",
                      width=0.5, alpha=0.85)
        ax.errorbar(
            range(len(feats)), coefs,
            yerr=[err_neg, err_pos],
            fmt="none", color="black", capsize=4, linewidth=1.2,
        )
        ax.axhline(0, color="black", linewidth=0.8, linestyle="--", alpha=0.5)

        # 显著性标注
        pvals = result["pvalues"]
        for i, (feat, coef) in enumerate(zip(feats, coefs)):
            p = pvals.get(feat, 1.0)
            stars = "***" if p < 0.01 else "**" if p < 0.05 else "*" if p < 0.10 else ""
            if stars:
                y_pos = coef + (max(abs(c) for c in coefs) * 0.05) * (1 if coef >= 0 else -1)
                ax.text(i, y_pos, stars, ha="center", va="bottom", fontsize=11, color="black")

        r2 = result.get("rsquared", np.nan)
        ax.set_title(f"{mode} 回归系数（R²={r2:.3f}）", fontsize=11)
        ax.set_ylabel("系数（IRR 变动百分点）", fontsize=9)
        ax.grid(axis="y", alpha=0.3)
        ax.tick_params(axis="x", labelsize=9)

    plt.tight_layout()

    return fig, None


# =============================================================================
# 图表9：无贷款情景 IRR 对比
# =============================================================================

def _chart_no_loan_irr(dcf_results: dict, no_loan_results: dict, dcf_params: dict):
    if not dcf_results or not no_loan_results:
        return _placeholder_chart("无贷款对比数据缺失"), None

    modes = list(dcf_results.keys())
    loan_irrs    = [dcf_results[m]["irr"] * 100    for m in modes]
    no_loan_irrs = [no_loan_results[m]["irr"] * 100 for m in modes]
    discount_pct = dcf_params.get("discount_rate", 0.055) * 100

    x     = np.arange(len(modes))
    width = 0.35

    fig, ax = plt.subplots(figsize=(10, 5), facecolor=COLORS["bg"])
    ax.set_facecolor(COLORS["bg"])

    bars1 = ax.bar(x - width / 2, loan_irrs, width,
                   label="有贷款（首付30%）",
                   color=COLORS["整租"], edgecolor="white")
    bars2 = ax.bar(x + width / 2, no_loan_irrs, width,
                   label="无贷款（全款）",
                   color=COLORS["隔断分租"], edgecolor="white")

    for bar, val in zip(bars1, loan_irrs):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.06,
                f"{val:.2f}%", ha="center", va="bottom", fontsize=9, fontweight="bold")
    for bar, val in zip(bars2, no_loan_irrs):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.06,
                f"{val:.2f}%", ha="center", va="bottom", fontsize=9, fontweight="bold")

    ax.axhline(discount_pct, color=COLORS["accent"], linewidth=1.5,
               linestyle="--", label=f"折现率基准 {discount_pct:.1f}%")

    ax.set_xticks(x)
    ax.set_xticklabels(modes, fontsize=11)
    ax.set_ylabel("年化 IRR（%）", fontsize=10)
    ax.set_title("有贷款 vs 无贷款：三种模式 IRR 对比", fontsize=13, pad=12)
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()

    fig_plotly = None
    if _PLOTLY_AVAILABLE:
        fig_plotly = go.Figure()
        fig_plotly.add_trace(go.Bar(
            name="有贷款（首付30%）",
            x=modes, y=loan_irrs,
            marker_color=COLORS["整租"],
            text=[f"{v:.2f}%" for v in loan_irrs],
            textposition="outside",
        ))
        fig_plotly.add_trace(go.Bar(
            name="无贷款（全款）",
            x=modes, y=no_loan_irrs,
            marker_color=COLORS["隔断分租"],
            text=[f"{v:.2f}%" for v in no_loan_irrs],
            textposition="outside",
        ))
        fig_plotly.add_hline(
            y=discount_pct, line_dash="dash", line_color=COLORS["accent"],
            annotation_text=f"折现率基准 {discount_pct:.1f}%",
            annotation_position="top right",
        )
        fig_plotly.update_layout(
            title="有贷款 vs 无贷款：三种模式 IRR 对比",
            barmode="group",
            yaxis_title="年化 IRR（%）",
            template="plotly_white",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )

    return fig, fig_plotly


# =============================================================================
# 图表10：无贷款情景现金流对比（最优模式）
# =============================================================================

def _chart_no_loan_cashflow(dcf_results: dict, no_loan_results: dict, dcf_params: dict):
    if not dcf_results or not no_loan_results:
        return _placeholder_chart("无贷款对比数据缺失"), None

    best_mode = max(
        no_loan_results.keys(),
        key=lambda m: no_loan_results[m].get("irr") or -999,
    )

    loan_cfs    = [cf / 10000 for cf in dcf_results[best_mode]["cash_flows"]]
    no_loan_cfs = [cf / 10000 for cf in no_loan_results[best_mode]["cash_flows"]]
    years = list(range(1, len(loan_cfs) + 1))

    fig, ax = plt.subplots(figsize=(10, 5), facecolor=COLORS["bg"])
    ax.set_facecolor(COLORS["bg"])

    ax.plot(years, loan_cfs, color=COLORS["整租"], linewidth=2,
            marker="o", markersize=4, label="有贷款（首付30%）")
    ax.plot(years, no_loan_cfs, color=COLORS["隔断分租"], linewidth=2,
            marker="s", markersize=4, label="无贷款（全款）")
    ax.axhline(0, color="gray", linewidth=1, linestyle="--", alpha=0.6)

    ax.set_xlabel("持有年限（年）", fontsize=10)
    ax.set_ylabel("年净现金流（万元）", fontsize=10)
    ax.set_title(f"年净现金流对比 — {best_mode}模式", fontsize=13, pad=12)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    plt.tight_layout()

    fig_plotly = None
    if _PLOTLY_AVAILABLE:
        fig_plotly = go.Figure()
        fig_plotly.add_trace(go.Scatter(
            name="有贷款（首付30%）",
            x=years, y=loan_cfs,
            mode="lines+markers",
            line=dict(color=COLORS["整租"], width=2),
            marker=dict(size=5),
        ))
        fig_plotly.add_trace(go.Scatter(
            name="无贷款（全款）",
            x=years, y=no_loan_cfs,
            mode="lines+markers",
            line=dict(color=COLORS["隔断分租"], width=2),
            marker=dict(size=5, symbol="square"),
        ))
        fig_plotly.add_hline(y=0, line_dash="dash", line_color="gray",
                              annotation_text="盈亏平衡", annotation_position="top right")
        fig_plotly.update_layout(
            title=f"年净现金流对比 — {best_mode}模式",
            xaxis_title="持有年限（年）",
            yaxis_title="年净现金流（万元）",
            template="plotly_white",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )

    return fig, fig_plotly


# =============================================================================
# 图表11：已持有情景 — 各模式年净收入对比（第1年 vs 第N年）
# =============================================================================

def _chart_owned_noi(owned_results: dict, dcf_params: dict):
    if not owned_results:
        return _placeholder_chart("已持有收益数据缺失"), None

    modes     = list(owned_results.keys())
    n_years   = dcf_params.get("years", 20)
    year1     = [owned_results[m]["year1_noi"] / 10000        for m in modes]
    year_n    = [owned_results[m]["cash_flows"][-1] / 10000   for m in modes]
    cap_rates = [owned_results[m]["cap_rate"] * 100           for m in modes]
    colors    = [COLORS.get(m, "#888888")                     for m in modes]

    x, width = np.arange(len(modes)), 0.35

    fig, ax = plt.subplots(figsize=(10, 5), facecolor=COLORS["bg"])
    ax.set_facecolor(COLORS["bg"])

    bars1 = ax.bar(x - width / 2, year1,  width, color=colors, edgecolor="white",
                   label="第1年 NOI")
    bars2 = ax.bar(x + width / 2, year_n, width, color=colors, edgecolor="white",
                   alpha=0.5, label=f"第{n_years}年 NOI")

    for bar, val, rate in zip(bars1, year1, cap_rates):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 0.01,
                f"{val:.2f}万\n({rate:.2f}%)", ha="center", va="bottom",
                fontsize=8, fontweight="bold")
    for bar, val in zip(bars2, year_n):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 0.01,
                f"{val:.2f}万", ha="center", va="bottom", fontsize=8)

    ax.set_xticks(x)
    ax.set_xticklabels(modes, fontsize=11)
    ax.set_ylabel("年净收入（万元）", fontsize=10)
    ax.set_title(f"已持有情景：各模式年净收入（第1年 vs 第{n_years}年）", fontsize=13, pad=12)
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()

    fig_plotly = None
    if _PLOTLY_AVAILABLE:
        fig_plotly = go.Figure()
        fig_plotly.add_trace(go.Bar(
            name="第1年 NOI",
            x=modes, y=year1,
            marker_color=colors,
            text=[f"{v:.2f}万 (收益率{r:.2f}%)" for v, r in zip(year1, cap_rates)],
            textposition="outside",
        ))
        fig_plotly.add_trace(go.Bar(
            name=f"第{n_years}年 NOI",
            x=modes, y=year_n,
            marker_color=[_hex_alpha(c, 0.6) for c in colors],
            text=[f"{v:.2f}万" for v in year_n],
            textposition="outside",
        ))
        fig_plotly.update_layout(
            title=f"已持有情景：各模式年净收入（第1年 vs 第{n_years}年）",
            barmode="group",
            yaxis_title="年净收入（万元）",
            template="plotly_white",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )

    return fig, fig_plotly


# =============================================================================
# 图表12：已持有情景 — 各模式累计净收益（20年）
# =============================================================================

def _chart_owned_cumulative(owned_results: dict, dcf_params: dict):
    if not owned_results:
        return _placeholder_chart("已持有收益数据缺失"), None

    n_years = dcf_params.get("years", 20)
    years   = list(range(1, n_years + 1))

    fig, ax = plt.subplots(figsize=(10, 5), facecolor=COLORS["bg"])
    ax.set_facecolor(COLORS["bg"])

    for mode, data in owned_results.items():
        cfs        = data["cash_flows"]
        cumulative = [sum(cfs[:i + 1]) / 10000 for i in range(len(cfs))]
        color      = COLORS.get(mode, "#888888")
        ax.plot(years, cumulative, marker="o", markersize=3, linewidth=2,
                color=color, label=mode)
        ax.text(years[-1] + 0.2, cumulative[-1],
                f" {cumulative[-1]:.0f}万", va="center", fontsize=9, color=color)

    ax.set_xlabel("持有年限（年）", fontsize=10)
    ax.set_ylabel("累计净收益（万元）", fontsize=10)
    ax.set_title("已持有情景：各模式累计净收益（不计购房本金）", fontsize=13, pad=12)
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    ax.xaxis.set_major_locator(mticker.MultipleLocator(2))
    plt.tight_layout()

    fig_plotly = None
    if _PLOTLY_AVAILABLE:
        fig_plotly = go.Figure()
        for mode, data in owned_results.items():
            cfs        = data["cash_flows"]
            cumulative = [sum(cfs[:i + 1]) / 10000 for i in range(len(cfs))]
            fig_plotly.add_trace(go.Scatter(
                x=years, y=cumulative,
                mode="lines+markers",
                name=mode,
                line=dict(color=COLORS.get(mode, "#888888"), width=2),
                marker=dict(size=4),
            ))
        fig_plotly.update_layout(
            title="已持有情景：各模式累计净收益（不计购房本金）",
            xaxis_title="持有年限（年）",
            yaxis_title="累计净收益（万元）",
            template="plotly_white",
            hovermode="x unified",
        )

    return fig, fig_plotly


# =============================================================================
# 工具函数
# =============================================================================

def _placeholder_chart(message: str):
    """当数据缺失时生成占位图"""
    fig, ax = plt.subplots(figsize=(8, 4), facecolor=COLORS["bg"])
    ax.text(0.5, 0.5, message, transform=ax.transAxes,
            ha="center", va="center", fontsize=13, color="gray",
            style="italic")
    ax.set_axis_off()
    return fig


def _add_data_note(ax, df: pd.DataFrame):
    """在图表底部添加数据来源注释"""
    if "scraped_at" in df.columns:
        ts = pd.to_datetime(df["scraped_at"]).max()
        note = f"数据截至 {ts.strftime('%Y-%m-%d')}  共 {len(df)} 条记录"
    else:
        note = f"共 {len(df)} 条记录"
    ax.text(0.99, 0.01, note, transform=ax.transAxes,
            ha="right", va="bottom", fontsize=8, color="gray")
