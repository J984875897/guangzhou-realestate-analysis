# =============================================================================
# main.py
# 项目总调度入口 —— 广州市海珠区房产投资分析
#
# 流程：
#   1. 采集贝壳二手房地图价格
#   2. 采集土巴兔装修报价
#   3. 采集贝壳租房地图价格
#   4. 数据清洗
#   5. 计算租售比
#   6. DCF 估值对比（三种商业模式）
#   7. 生成可视化图表
# =============================================================================

import asyncio
import random
from pathlib import Path
from datetime import datetime

import pandas as pd

from scrapers.beike_map_scraper  import batch_scrape_city
from scrapers.beike_rent_scraper import batch_scrape_rent
from scrapers.browser_engine     import ensure_beike_login
from scrapers.tubatu_scraper     import TubatuScraper
from scrapers.poi_scraper        import POIScraper
from processing.cleaner          import clean_house_data, calc_rent_sale_ratio
from processing.geo_features     import add_geo_features, calc_per_community_irr
from models.dcf                  import calc_dcf
from models.regression           import run_all_regressions
from visualization.charts        import generate_all_charts


# =============================================================================
# 项目配置
# =============================================================================
CONFIG = {
    "city":       "广州",
    "districts":  ["海珠区"],
    "area_range": (70, 110),             # 海珠区主力户型面积段
    "dcf_params": {
        "sqm":           90,             # 代表性分析面积（㎡）
        "down_pct":      0.30,           # 首付30%
        "loan_rate":     0.0315,         # 广州 LPR-20BP 实际利率（2024年）
        "discount_rate": 0.055,          # 折现率 5.5%（个人最低回报要求）
        "years":         20,
    },
    "output_dir": Path("output"),
}

# 三种商业模式参数（基于广州海珠区2024年市场行情）
BUSINESS_MODES = {
    "整租": {
        "rent_per_sqm": 55,      # 整租月租金单价（元/㎡）
        "occ_rate":     0.95,    # 出租率
        "opex_ratio":   0.10,    # 运营成本占租金比
        "rent_growth":  0.02,    # 年租金增长率
    },
    "民宿": {
        "rent_per_sqm": 110,     # 民宿折算月均收入（海珠临江/琶洲溢价明显）
        "occ_rate":     0.65,    # 淡旺季综合出租率
        "opex_ratio":   0.25,    # 清洁/平台抽成/消耗品成本高
        "rent_growth":  0.04,    # 旅游民宿市场增速
    },
    "隔断分租": {
        "rent_per_sqm": 80,      # 分租后每㎡收益高于整租
        "occ_rate":     0.90,
        "opex_ratio":   0.15,
        "rent_growth":  0.015,
    },
}


# =============================================================================
# 主流程
# =============================================================================

async def run_full_pipeline():
    CONFIG["output_dir"].mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    print(f"\n{'='*60}")
    print(f"  房产投资分析系统  |  {CONFIG['city']}海珠区  |  {timestamp}")
    print(f"{'='*60}\n")

    # ------------------------------------------------------------------
    # 前置：确保贝壳登录（首次运行会弹出浏览器窗口）
    # ------------------------------------------------------------------
    await ensure_beike_login()

    # ------------------------------------------------------------------
    # Step 1：采集贝壳地图二手房价格
    # ------------------------------------------------------------------
    print("[Step 1/7] 采集贝壳地图小区均价...")

    beike_df = await batch_scrape_city(
        city=CONFIG["city"],
        districts=CONFIG["districts"]
    )

    if beike_df.empty or len(beike_df) < 10:
        print(f"[警告] 贝壳买房数据量不足（{len(beike_df)} 条），使用模拟数据继续...")
        beike_df = _mock_beike_data()
    else:
        save_path = CONFIG["output_dir"] / f"beike_prices_{timestamp}.csv"
        beike_df.to_csv(save_path, index=False, encoding="utf-8-sig")
        print(f"[Step 1] ✓ 已保存: {save_path}")

    print(f"  小区总数: {len(beike_df)}")
    print(f"  均价中位数: {beike_df['unit_price'].median():,.0f} 元/㎡")
    print(f"  均价范围: {beike_df['unit_price'].min():,.0f} ~ {beike_df['unit_price'].max():,.0f} 元/㎡\n")


    # ------------------------------------------------------------------
    # Step 2：采集土巴兔装修报价
    # ------------------------------------------------------------------
    print("[Step 2/7] 采集土巴兔装修报价...")

    tubatu = TubatuScraper(city=CONFIG["city"])
    cases  = await tubatu.scrape_cases(area_range=CONFIG["area_range"], pages=5)
    cases_df     = TubatuScraper.cases_to_dataframe(cases) if cases else _mock_tubatu_data()
    reno_summary = TubatuScraper.summarize_by_level(cases_df)

    save_path = CONFIG["output_dir"] / f"tubatu_cases_{timestamp}.csv"
    cases_df.to_csv(save_path, index=False, encoding="utf-8-sig")
    print(f"[Step 2] ✓ 已保存: {save_path}")
    print("\n  装修档次汇总：")
    print(reno_summary.to_string(index=True))
    print()


    # ------------------------------------------------------------------
    # Step 3：采集贝壳租房价格
    # ------------------------------------------------------------------
    print("[Step 3/7] 采集贝壳租房地图数据...")

    rent_df = await batch_scrape_rent(
        city=CONFIG["city"],
        districts=CONFIG["districts"]
    )

    if rent_df.empty or len(rent_df) < 10:
        print(f"[警告] 租房数据量不足（{len(rent_df)} 条），使用估算值继续（租售比分析精度受影响）...")
        rent_df = _mock_rent_data()
    else:
        save_path = CONFIG["output_dir"] / f"beike_rent_{timestamp}.csv"
        rent_df.to_csv(save_path, index=False, encoding="utf-8-sig")
        print(f"[Step 3] ✓ 已保存: {save_path}")

    print(f"  租房数据量: {len(rent_df)} 条")
    print(f"  月租中位数: {rent_df['monthly_rent_per_sqm'].median():.1f} 元/㎡/月\n")


    # ------------------------------------------------------------------
    # Step 4：数据清洗
    # ------------------------------------------------------------------
    print("[Step 4/7] 数据清洗...")
    clean_df = clean_house_data(beike_df) if "unit_price" in beike_df.columns else beike_df
    print(f"[Step 4] ✓ 清洗后 {len(clean_df)} 条（去除异常值 {len(beike_df)-len(clean_df)} 条）\n")


    # ------------------------------------------------------------------
    # Step 5：计算租售比
    # ------------------------------------------------------------------
    print("[Step 5/7] 计算租售比指标...")
    ratio_result = calc_rent_sale_ratio(clean_df, rent_df)

    print(f"  挂牌均价（中位）: {ratio_result['avg_unit_price']:,.0f} 元/㎡")
    print(f"  月租金（中位）:   {ratio_result['avg_monthly_rent']:.1f} 元/㎡")
    print(f"  租售比:           {ratio_result['ratio']}")
    print(f"  年化毛收益率:     {ratio_result['annual_yield']:.2f}%\n")


    # ------------------------------------------------------------------
    # Step 6：DCF 估值对比
    # ------------------------------------------------------------------
    print("[Step 6/7] DCF 估值对比（三种商业模式）...\n")

    area_median = clean_df["unit_price"].median()
    total_price = area_median * CONFIG["dcf_params"]["sqm"]
    results = {}

    print(f"  房屋参数：{CONFIG['dcf_params']['sqm']}㎡，总价约 {total_price/10000:.0f} 万元")
    print(f"  首付 {CONFIG['dcf_params']['down_pct']*100:.0f}%：{total_price*CONFIG['dcf_params']['down_pct']/10000:.0f} 万元\n")
    print(f"  {'模式':<10} {'年化毛收益':>10} {'月供(元)':>12} {'10年NPV(万)':>13} {'IRR':>10}")
    print("  " + "-" * 58)

    for mode_name, mode_params in BUSINESS_MODES.items():
        dcf_result = calc_dcf(
            total_price   = total_price,
            down_pct      = CONFIG["dcf_params"]["down_pct"],
            loan_rate     = CONFIG["dcf_params"]["loan_rate"],
            rent_per_sqm  = mode_params["rent_per_sqm"],
            sqm           = CONFIG["dcf_params"]["sqm"],
            occ_rate      = mode_params["occ_rate"],
            opex_ratio    = mode_params["opex_ratio"],
            rent_growth   = mode_params["rent_growth"],
            discount_rate = CONFIG["dcf_params"]["discount_rate"],
            years         = CONFIG["dcf_params"]["years"],
        )

        gross_yield = (mode_params["rent_per_sqm"] * 12 * mode_params["occ_rate"] /
                      (total_price / CONFIG["dcf_params"]["sqm"]) * 100)
        npv_10yr = sum(
            cf / (1 + CONFIG["dcf_params"]["discount_rate"]) ** y
            for y, cf in enumerate(dcf_result["cash_flows"][:10], 1)
        )

        results[mode_name] = dcf_result
        print(f"  {mode_name:<10} {gross_yield:>9.2f}%  "
              f"{dcf_result['monthly_mortgage']:>10,.0f}  "
              f"{npv_10yr/10000:>11.1f}万  "
              f"{dcf_result['irr']*100:>8.2f}%")


    # 无房贷情景：down_pct=1.0，loan=0，初始投入=全款
    no_loan_results = {}
    for mode_name, mode_params in BUSINESS_MODES.items():
        no_loan_results[mode_name] = calc_dcf(
            total_price   = total_price,
            down_pct      = 1.0,
            loan_rate     = CONFIG["dcf_params"]["loan_rate"],
            rent_per_sqm  = mode_params["rent_per_sqm"],
            sqm           = CONFIG["dcf_params"]["sqm"],
            occ_rate      = mode_params["occ_rate"],
            opex_ratio    = mode_params["opex_ratio"],
            rent_growth   = mode_params["rent_growth"],
            discount_rate = CONFIG["dcf_params"]["discount_rate"],
            years         = CONFIG["dcf_params"]["years"],
        )

    # 已持有情景：不计原始购房成本，仅分析未来 NOI 现金流（沉没成本不计入）
    owned_results = {}
    for mode_name in BUSINESS_MODES:
        cfs = no_loan_results[mode_name]["cash_flows"]   # 即各年 NOI（无月供）
        year1_noi = cfs[0]
        owned_results[mode_name] = {
            "cash_flows":     cfs,
            "year1_noi":      year1_noi,
            "cumulative_noi": sum(cfs),
            "cap_rate":       year1_noi / total_price,   # 隐含年化回报率
        }

    # ------------------------------------------------------------------
    # Step 7：可视化图表（基础 6 张）
    # ------------------------------------------------------------------
    print("\n[Step 7/10] 生成基础可视化图表...")

    chart_paths = generate_all_charts(
        house_df        = clean_df,
        rent_df         = rent_df,
        reno_summary    = reno_summary,
        dcf_results     = results,
        dcf_params      = CONFIG["dcf_params"],
        output_dir      = CONFIG["output_dir"] / "charts",
        no_loan_results = no_loan_results,
        owned_results   = owned_results,
    )


    # ------------------------------------------------------------------
    # Step 8：采集地铁站和学校 POI
    # ------------------------------------------------------------------
    print("\n[Step 8/10] 采集地铁站和学校 POI（Overpass API）...")

    poi = POIScraper()
    metro_df, school_df = poi.fetch_all()

    print(f"  地铁站: {len(metro_df)} 条")
    print(f"  学校:   {len(school_df)} 条\n")


    # ------------------------------------------------------------------
    # Step 8.5：Nominatim 地理编码（小区名称 → BD-09 坐标）
    # ------------------------------------------------------------------
    print("[地理编码] 查询小区坐标（Nominatim）...")
    from scrapers.poi_scraper import geocode_communities

    community_names = clean_df["name"].dropna().unique().tolist()
    coords_map = geocode_communities(community_names, district="海珠区")

    if coords_map:
        clean_df["lng"] = clean_df["name"].map(
            lambda n: coords_map.get(n, {}).get("lng")
        )
        clean_df["lat"] = clean_df["name"].map(
            lambda n: coords_map.get(n, {}).get("lat")
        )
        found = int(clean_df["lng"].notna().sum())
        print(f"  成功编码 {found}/{len(community_names)} 个小区\n")
    else:
        print("  [跳过] 地理编码无结果，Step 9 将使用 mock 数据模式\n")


    # ------------------------------------------------------------------
    # Step 9：地理特征工程 + 逐小区 IRR 计算
    # ------------------------------------------------------------------
    print("[Step 9/10] 地理特征工程 + 逐小区 IRR 估算...")

    rent_median = (
        rent_df["monthly_rent_per_sqm"].median()
        if not rent_df.empty and "monthly_rent_per_sqm" in rent_df.columns
        else 55.0
    )

    geo_df = add_geo_features(clean_df, metro_df, school_df)
    geo_df = calc_per_community_irr(
        geo_df,
        rent_per_sqm  = rent_median,
        dcf_params    = CONFIG["dcf_params"],
        business_modes = BUSINESS_MODES,
    )

    has_geo = ("dist_nearest_metro" in geo_df.columns and
               geo_df["dist_nearest_metro"].notna().any())

    if has_geo:
        print(f"  平均最近地铁距离: {geo_df['dist_nearest_metro'].median():.2f} km")
        print(f"  平均最近学校距离: {geo_df['dist_nearest_school'].median():.2f} km")
    else:
        print("  [提示] 小区坐标缺失（mock 数据已补充），地理特征将在含真实坐标时生效")


    # ------------------------------------------------------------------
    # Step 10：IRR 相关性回归分析
    # ------------------------------------------------------------------
    print("\n[Step 10/10] IRR 与地铁/学校距离回归分析...")

    geo_reg_df = geo_df.copy()
    modes = list(BUSINESS_MODES.keys())

    regression_results = run_all_regressions(geo_reg_df, modes)


    # ------------------------------------------------------------------
    # 补充图表（地理分布 + 回归分析）
    # ------------------------------------------------------------------
    print("\n生成地理分布和回归分析图表...")

    extra_paths = generate_all_charts(
        house_df           = clean_df,
        rent_df            = rent_df,
        reno_summary       = reno_summary,
        dcf_results        = results,
        dcf_params         = CONFIG["dcf_params"],
        output_dir         = CONFIG["output_dir"] / "charts",
        geo_df             = geo_df,
        metro_df           = metro_df,
        school_df          = school_df,
        regression_results = regression_results,
        no_loan_results    = no_loan_results,
        owned_results      = owned_results,
    )
    chart_paths.update(extra_paths)


    # ------------------------------------------------------------------
    # Step 11：保存 KPI 摘要 + 生成决策端 HTML 报告
    # ------------------------------------------------------------------
    print("\n[Step 11/11] 生成决策端 HTML 报告...")
    import json
    from visualization.report import generate_dashboard

    # 提取最优商业模式 IRR（有贷款）
    best_mode, best_irr = "—", None
    for mode, r in results.items():
        irr = r.get("irr")
        if irr is not None and not pd.isna(irr):
            if best_irr is None or irr > best_irr:
                best_mode, best_irr = mode, irr

    # 提取最优商业模式 IRR（无贷款）
    best_nl_mode, best_nl_irr = "—", None
    for mode, r in no_loan_results.items():
        irr = r.get("irr")
        if irr is not None and not pd.isna(irr):
            if best_nl_irr is None or irr > best_nl_irr:
                best_nl_mode, best_nl_irr = mode, irr

    # 提取已持有情景最优模式（按 Year1 NOI 排序）
    best_owned_mode, best_owned_noi, best_owned_cap = "—", None, None
    for mode, r in owned_results.items():
        noi = r.get("year1_noi")
        if noi is not None and (best_owned_noi is None or noi > best_owned_noi):
            best_owned_mode = mode
            best_owned_noi  = noi
            best_owned_cap  = r.get("cap_rate")

    # 处理租售比（可能是 "1:407" 字符串或数值）
    raw_ratio = ratio_result.get("ratio", 0)
    if isinstance(raw_ratio, str) and ":" in raw_ratio:
        ratio_num = int(raw_ratio.split(":")[-1])
    else:
        ratio_num = int(round(float(raw_ratio))) if raw_ratio else 0

    summary = {
        "generated_at":      datetime.now().strftime("%Y-%m-%d %H:%M"),
        "avg_price_wan":     round(float(clean_df["unit_price"].median()) / 10000, 2),
        "avg_rent_sqm":      (round(float(rent_df["monthly_rent_per_sqm"].median()), 1)
                              if not rent_df.empty else None),
        "annual_yield_pct":  round(float(ratio_result.get("annual_yield", 0)), 2),
        "rent_sale_ratio":   ratio_num,
        "best_mode":         best_mode,
        "best_irr_pct":      (round(best_irr * 100, 2) if best_irr is not None else None),
        "discount_rate_pct":    CONFIG["dcf_params"]["discount_rate"] * 100,
        "house_count":          len(clean_df),
        "has_geo":              (bool(geo_df["wgs_lat"].notna().any())
                                 if "wgs_lat" in geo_df.columns else False),
        "has_regression":       (CONFIG["output_dir"] / "charts/IRR回归分析.png").exists(),
        "best_no_loan_mode":    best_nl_mode,
        "best_no_loan_irr_pct": (round(best_nl_irr * 100, 2) if best_nl_irr is not None else None),
        "has_no_loan":          (CONFIG["output_dir"] / "charts/无贷款IRR对比.html").exists(),
        "best_owned_mode":         best_owned_mode,
        "best_owned_noi_wan":      (round(best_owned_noi / 10000, 2) if best_owned_noi is not None else None),
        "best_owned_monthly_wan":  (round(best_owned_noi / 12 / 10000, 2) if best_owned_noi is not None else None),
        "best_owned_cap_rate_pct": (round(best_owned_cap * 100, 2) if best_owned_cap is not None else None),
        "has_owned":               (CONFIG["output_dir"] / "charts/已持有NOI对比.html").exists(),
    }
    (CONFIG["output_dir"] / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    dashboard_path = generate_dashboard(CONFIG["output_dir"])
    print(f"  KPI 摘要已保存: {CONFIG['output_dir'] / 'summary.json'}")
    print(f"  决策报告已生成: {dashboard_path.absolute()}")

    print(f"\n[完成] 全部分析完成！")
    print(f"  输出目录: {CONFIG['output_dir'].absolute()}")
    print(f"  图表数量: {len(chart_paths)} 个")
    print(f"  决策报告: {dashboard_path.name}")

    return {
        "beike_df":            clean_df,
        "rent_df":             rent_df,
        "cases_df":            cases_df,
        "reno_summary":        reno_summary,
        "dcf_results":         results,
        "ratio_result":        ratio_result,
        "geo_df":              geo_df,
        "metro_df":            metro_df,
        "school_df":           school_df,
        "regression_results":  regression_results,
        "chart_paths":         chart_paths,
        "dashboard_path":      dashboard_path,
    }


# =============================================================================
# 模拟数据（当爬虫失败时用于调试）
# =============================================================================

def _mock_beike_data() -> pd.DataFrame:
    """生成模拟的广州海珠区二手房价格数据（含 BD-09 坐标）"""
    random.seed(42)
    n = 60
    # 各子区中心坐标（BD-09）及典型房价段
    sub_areas = {
        "琶洲":   {"center": (113.397, 23.102), "price_mean": 50000},
        "滨江东": {"center": (113.352, 23.107), "price_mean": 48000},
        "江南西": {"center": (113.287, 23.091), "price_mean": 40000},
        "赤岗":   {"center": (113.362, 23.092), "price_mean": 42000},
        "南洲":   {"center": (113.327, 23.057), "price_mean": 35000},
        "海珠湖": {"center": (113.301, 23.071), "price_mean": 38000},
    }
    area_names = list(sub_areas.keys())

    community_ids, names, districts = [], [], []
    unit_prices, listing_counts      = [], []
    lngs, lats                        = [], []

    for i in range(n):
        area = random.choice(area_names)
        cfg  = sub_areas[area]
        cx, cy = cfg["center"]
        community_ids.append(f"GZ_HZ_{i:04d}")
        names.append(f"海珠{area}小区{i:02d}")
        districts.append("海珠区")
        unit_prices.append(random.gauss(cfg["price_mean"], 5000))
        listing_counts.append(random.randint(1, 25))
        lngs.append(random.gauss(cx, 0.008))   # ~0.8km 随机扰动
        lats.append(random.gauss(cy, 0.008))

    return pd.DataFrame({
        "community_id":  community_ids,
        "name":          names,
        "district":      districts,
        "unit_price":    unit_prices,
        "listing_count": listing_counts,
        "lng":           lngs,
        "lat":           lats,
        "scraped_at":    [pd.Timestamp.now().isoformat()] * n,
    })

def _mock_tubatu_data() -> pd.DataFrame:
    """生成模拟的土巴兔装修数据"""
    random.seed(99)
    records = []
    for level, (min_p, max_p) in [("经济型",(500,900)), ("中档",(1000,1600)), ("高档",(1800,3200))]:
        for i in range(20):
            area = random.uniform(*CONFIG["area_range"])
            unit = random.uniform(min_p, max_p)
            records.append({
                "case_id":       f"{level}_{i}",
                "city":          CONFIG["city"],
                "area":          round(area, 1),
                "level":         level,
                "unit_price":    round(unit, 0),
                "total_price":   round(area * unit, 0),
                "duration_days": {"经济型": 25, "中档": 45, "高档": 70}[level],
            })
    return pd.DataFrame(records)

def _mock_rent_data() -> pd.DataFrame:
    """生成模拟的广州海珠区租房数据（整租约55元/㎡/月）"""
    random.seed(77)
    n = 80
    return pd.DataFrame({
        "community_id":         [f"RENT_HZ_{i:04d}" for i in range(n)],
        "name":                 [f"海珠租房{i:02d}" for i in range(n)],
        "district":             ["海珠区"] * n,
        "monthly_rent":         [random.gauss(4500, 800) for _ in range(n)],
        "area":                 [random.uniform(70, 110) for _ in range(n)],
        "monthly_rent_per_sqm": [random.gauss(55, 10) for _ in range(n)],
        "scraped_at":           [pd.Timestamp.now().isoformat()] * n,
    })


# =============================================================================
# 入口
# =============================================================================

if __name__ == "__main__":
    asyncio.run(run_full_pipeline())
