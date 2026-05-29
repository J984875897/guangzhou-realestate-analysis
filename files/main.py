# =============================================================================
# main.py
# 项目总调度入口 —— 广州市海珠区房产投资分析
#
# 流程：
#   1. 采集贝壳二手房地图价格
#   2. 采集贝壳租房地图价格
#   3. 采集土巴兔装修报价
#   4. 数据清洗
#   5. 计算租售比
#   6. DCF 估值对比（三种商业模式）
#   7. 敏感性分析
#   8. 采集地铁站和学校 POI
#   9. Nominatim 地理编码
#  10. 地理特征工程 + 逐小区 IRR
#  11. IRR 相关性回归分析
#  12. 生成可视化图表
#  13. 生成决策端 HTML 报告
# =============================================================================

import asyncio
import argparse
import json
import random
from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd

from scrapers.beike_map_scraper  import batch_scrape_city
from scrapers.beike_rent_scraper import batch_scrape_rent
from scrapers.browser_engine     import ensure_beike_login
from scrapers.tubatu_scraper     import TubatuScraper
from scrapers.poi_scraper        import POIScraper, geocode_communities
from processing.cleaner          import clean_house_data, calc_rent_sale_ratio
from processing.geo_features     import add_geo_features, calc_per_community_irr
from models.dcf                  import calc_dcf
from models.regression           import run_all_regressions
from models.sensitivity          import run_sensitivity
from visualization.charts        import generate_all_charts
from visualization.report        import generate_dashboard


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
    "scrape": {
        "use_cache": True,       # 默认优先使用最近一次贝壳 CSV，减少重复访问
        "refresh_beike": False,  # 只有显式刷新时才重新抓贝壳
        "require_login": False,  # 公开列表页默认不强制登录
        "min_house_rows": 10,
        "min_rent_rows": 10,
        "beike_min_pages":   5,   # 每次至少翻 N 页
        "beike_min_records": 150, # 同时满足 N 条才停止
        "beike_max_pages":   100, # 安全上限，防止无限翻页
        "tubatu_pages":      10,  # 土巴兔固定翻页数
    },
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
        "opex_ratio":   0.40,    # 清洁/平台抽成/耗材/客服/维修等综合成本
        "rent_growth":  0.02,    # 长期基准调低，避免将短期旺季增速外推20年
        "terminal_cap": 0.06,    # 民宿经营现金流风险高于长租，退出 cap 单独提高
        "initial_capex": 90000,  # 初始软装、家具家电、布草和拍摄等投入
    },
    "隔断分租": {
        "rent_per_sqm": 80,      # 分租后每㎡收益高于整租
        "occ_rate":     0.90,
        "opex_ratio":   0.15,
        "rent_growth":  0.015,
    },
}


# =============================================================================
# 主流程（编排层）
# =============================================================================

async def run_full_pipeline(
    mode: str = "full",
    data_dir: Path = None,
    *,
    refresh_beike: bool = None,
    use_cache: bool = None,
    require_login: bool = None,
    scrape_steps: Optional[set] = None,
):
    """
    mode:
      full    — 完整流程（爬虫 + 分析），默认行为
      scrape  — 仅爬虫（Steps 1-3），保存 CSV 后退出
      analyze — 仅分析（Steps 4-13），从 data_dir 加载最新 CSV
    """
    CONFIG["output_dir"].mkdir(parents=True, exist_ok=True)
    data_dir = data_dir or CONFIG["output_dir"]
    data_dir.mkdir(parents=True, exist_ok=True)

    scrape_cfg = CONFIG["scrape"]
    if refresh_beike is None:
        refresh_beike = scrape_cfg["refresh_beike"]
    if use_cache is None:
        use_cache = scrape_cfg["use_cache"]
    if require_login is None:
        require_login = scrape_cfg["require_login"]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    print(f"\n{'='*60}")
    print(f"  房产投资分析系统  |  {CONFIG['city']}海珠区  |  {timestamp}  |  模式: {mode}")
    print(f"{'='*60}\n")

    scrape_out = await _run_scrape_phase(
        mode=mode,
        data_dir=data_dir,
        scrape_cfg=scrape_cfg,
        timestamp=timestamp,
        refresh_beike=refresh_beike,
        use_cache=use_cache,
        require_login=require_login,
        scrape_steps=scrape_steps,
    )
    if scrape_out["early_exit"]:
        return scrape_out["result"]

    analysis_out = _run_analysis_phase(scrape_out)
    report_out   = _run_report_phase(scrape_out, analysis_out, CONFIG["output_dir"])

    return {
        "beike_df":           analysis_out["clean_df"],
        "rent_df":            scrape_out["rent_df"],
        "cases_df":           scrape_out["cases_df"],
        "reno_summary":       scrape_out["reno_summary"],
        "dcf_results":        analysis_out["dcf_results"],
        "ratio_result":       analysis_out["ratio_result"],
        "geo_df":             analysis_out["geo_df"],
        "metro_df":           analysis_out["metro_df"],
        "school_df":          analysis_out["school_df"],
        "regression_results": analysis_out["regression_results"],
        "chart_paths":        report_out["chart_paths"],
        "dashboard_path":     report_out["dashboard_path"],
    }


# =============================================================================
# 爬虫阶段（Steps 1-3）
# =============================================================================

async def _run_scrape_phase(
    mode: str,
    data_dir: Path,
    scrape_cfg: dict,
    timestamp: str,
    refresh_beike: bool,
    use_cache: bool,
    require_login: bool,
    scrape_steps: Optional[set],
) -> dict:
    """
    Returns a dict with all scraped data and metadata.
    Includes {"early_exit": True, "result": {...}} when mode == "scrape".
    """
    if mode == "analyze":
        loaded = _load_latest_scraped_data(data_dir)
        reno_summary = TubatuScraper.summarize_by_level(loaded["cases_df"])
        return {**loaded, "reno_summary": reno_summary, "early_exit": False}

    if require_login:
        await ensure_beike_login()
    else:
        print("[登录] 默认跳过贝壳登录；公开列表页如遇验证码/登录页会停止并提示")

    _steps = scrape_steps if (mode == "scrape" and scrape_steps is not None) else {1, 2, 3}
    beike_df = rent_df = cases_df = None
    reno_summary = pd.DataFrame()
    reno_data_source = "not_scraped"
    tubatu_stats: dict = {}
    beike_scraped_count = rent_scraped_count = 0
    beike_is_mock = rent_is_mock = True

    # ------------------------------------------------------------------
    # Step 1：采集或加载贝壳二手房价格
    # ------------------------------------------------------------------
    if 1 in _steps:
        print("[Step 1/3] 获取贝壳二手房价格...")

        beike_from_cache = False
        if use_cache and not refresh_beike:
            beike_df = _try_load_latest_csv(
                data_dir=data_dir,
                pattern="beike_prices_*.csv",
                label="贝壳买房",
                min_rows=scrape_cfg["beike_min_records"],
            )
            beike_from_cache = beike_df is not None

        if beike_df is None:
            beike_df = await batch_scrape_city(
                city=CONFIG["city"],
                districts=CONFIG["districts"],
                min_pages=scrape_cfg["beike_min_pages"],
                min_records=scrape_cfg["beike_min_records"],
                max_pages=scrape_cfg["beike_max_pages"],
            )
            if (beike_df.empty or len(beike_df) < scrape_cfg["min_house_rows"]) and use_cache:
                cached_df = _try_load_latest_csv(
                    data_dir=data_dir,
                    pattern="beike_prices_*.csv",
                    label="贝壳买房备用缓存",
                    min_rows=scrape_cfg["min_house_rows"],
                )
                if cached_df is not None:
                    beike_df = cached_df
                    beike_from_cache = True

        if beike_from_cache and len(beike_df) < scrape_cfg["beike_min_records"]:
            print(f"\n[警告] !! 爬虫数据量不足（{len(beike_df)} 条，目标 {scrape_cfg['beike_min_records']} 条）")
            print(f"[警告]    很可能是被验证码拦截导致。建议：python main.py --login 登录贝壳后重试\n")

        beike_scraped_count = len(beike_df)
        beike_is_mock = beike_df.empty or len(beike_df) < 10
        if beike_is_mock:
            print(f"[警告] 贝壳买房数据量不足（{len(beike_df)} 条），使用模拟数据继续...")
            beike_df = _mock_beike_data()
        elif not beike_from_cache:
            save_path = _save_csv(beike_df, data_dir, "beike_prices_*.csv", f"beike_prices_{timestamp}.csv")
            print(f"[Step 1] ✓ 已保存: {save_path}")

        print(f"  小区总数: {len(beike_df)}")
        print(f"  均价中位数: {beike_df['unit_price'].median():,.0f} 元/㎡")
        print(f"  均价范围: {beike_df['unit_price'].min():,.0f} ~ {beike_df['unit_price'].max():,.0f} 元/㎡\n")
    else:
        print("[Step 1/3] 跳过贝壳二手房采集\n")


    # ------------------------------------------------------------------
    # Step 2：采集或加载贝壳租房价格
    # ------------------------------------------------------------------
    if 2 in _steps:
        print("[Step 2/3] 获取贝壳租房价格...")

        rent_from_cache = False
        if use_cache and not refresh_beike:
            rent_df = _try_load_latest_csv(
                data_dir=data_dir,
                pattern="beike_rent_*.csv",
                label="贝壳租房",
                min_rows=scrape_cfg["beike_min_records"],
            )
            rent_from_cache = rent_df is not None

        if rent_df is None:
            rent_df = await batch_scrape_rent(
                city=CONFIG["city"],
                districts=CONFIG["districts"],
                min_pages=scrape_cfg["beike_min_pages"],
                min_records=scrape_cfg["beike_min_records"],
                max_pages=scrape_cfg["beike_max_pages"],
            )
            if (rent_df.empty or len(rent_df) < scrape_cfg["min_rent_rows"]) and use_cache:
                cached_df = _try_load_latest_csv(
                    data_dir=data_dir,
                    pattern="beike_rent_*.csv",
                    label="贝壳租房备用缓存",
                    min_rows=scrape_cfg["min_rent_rows"],
                )
                if cached_df is not None:
                    rent_df = cached_df
                    rent_from_cache = True

        if rent_from_cache and len(rent_df) < scrape_cfg["beike_min_records"]:
            print(f"\n[警告] !! 租房数据量不足（{len(rent_df)} 条，目标 {scrape_cfg['beike_min_records']} 条）")
            print(f"[警告]    很可能是被验证码拦截导致。建议：python main.py --login 登录贝壳后重试\n")

        rent_scraped_count = len(rent_df)
        rent_is_mock = rent_df.empty or len(rent_df) < 10
        if rent_is_mock:
            print(f"[警告] 租房数据量不足（{len(rent_df)} 条），使用估算值继续（租售比分析精度受影响）...")
            rent_df = _mock_rent_data()
        elif not rent_from_cache:
            save_path = _save_csv(rent_df, data_dir, "beike_rent_*.csv", f"beike_rent_{timestamp}.csv")
            print(f"[Step 2] ✓ 已保存: {save_path}")

        print(f"  租房数据量: {len(rent_df)} 条")
        print(f"  月租中位数: {rent_df['monthly_rent_per_sqm'].median():.1f} 元/㎡/月\n")
    else:
        print("[Step 2/3] 跳过贝壳租房采集\n")


    # ------------------------------------------------------------------
    # Step 3：采集土巴兔装修报价
    # ------------------------------------------------------------------
    if 3 in _steps:
        print("[Step 3/3] 采集土巴兔装修报价...")

        tubatu = TubatuScraper(city=CONFIG["city"])
        cases  = await tubatu.scrape_cases(area_range=CONFIG["area_range"], pages=scrape_cfg["tubatu_pages"])
        tubatu_stats = getattr(tubatu, "last_case_stats", {}) or {}
        if cases:
            cases_df = TubatuScraper.cases_to_dataframe(cases)
            reno_data_source = "to8to_real"
            print(f"[Step 3] ✓ 土巴兔真实案例采集成功：{len(cases_df)} 条")
        else:
            cases_df = _mock_tubatu_data()
            reno_data_source = "mock_fallback"
            print("[Step 3] ⚠ 土巴兔真实采集失败，已使用模拟装修数据兜底")
        reno_summary = TubatuScraper.summarize_by_level(cases_df)

        save_path = _save_csv(cases_df, CONFIG["output_dir"], "tubatu_cases_*.csv", f"tubatu_cases_{timestamp}.csv")
        print(f"[Step 3] ✓ 已保存: {save_path}")
        print("\n  装修档次汇总：")
        print(reno_summary.to_string(index=True))
        print()
    else:
        print("[Step 3/3] 跳过土巴兔采集\n")

    out = {
        "beike_df":            beike_df,
        "rent_df":             rent_df,
        "cases_df":            cases_df,
        "reno_summary":        reno_summary,
        "reno_data_source":    reno_data_source,
        "tubatu_stats":        tubatu_stats,
        "beike_scraped_count": beike_scraped_count,
        "beike_is_mock":       beike_is_mock,
        "rent_scraped_count":  rent_scraped_count,
        "rent_is_mock":        rent_is_mock,
    }

    if mode == "scrape":
        ran = sorted(_steps)
        step_names = {1: "贝壳二手房", 2: "贝壳租房", 3: "土巴兔"}
        print(f"[完成] 爬虫数据已保存（已采集：{', '.join(step_names[s] for s in ran)}）。")
        print(f"  下次运行分析请使用: python main.py --mode analyze")
        result: dict = {}
        if beike_df is not None:
            result["beike_df"] = beike_df
        if rent_df is not None:
            result["rent_df"] = rent_df
        if cases_df is not None:
            result["cases_df"] = cases_df
            result["reno_summary"] = reno_summary
        return {**out, "early_exit": True, "result": result}

    return {**out, "early_exit": False}


# =============================================================================
# 分析阶段（Steps 4-11）
# =============================================================================

def _run_analysis_phase(scrape_out: dict) -> dict:
    beike_df            = scrape_out["beike_df"]
    rent_df             = scrape_out["rent_df"]
    cases_df            = scrape_out["cases_df"]
    reno_data_source    = scrape_out["reno_data_source"]
    tubatu_stats        = scrape_out["tubatu_stats"]
    beike_scraped_count = scrape_out["beike_scraped_count"]
    beike_is_mock       = scrape_out["beike_is_mock"]
    rent_scraped_count  = scrape_out["rent_scraped_count"]
    rent_is_mock        = scrape_out["rent_is_mock"]

    # ------------------------------------------------------------------
    # Step 4：数据清洗
    # ------------------------------------------------------------------
    print("[Step 4/13] 数据清洗...")
    clean_df = clean_house_data(beike_df) if "unit_price" in beike_df.columns else beike_df
    print(f"[Step 4] ✓ 清洗后 {len(clean_df)} 条（去除异常值 {len(beike_df)-len(clean_df)} 条）\n")

    data_source_info = {
        "house": {
            "name":    "贝壳买房",
            "is_mock": beike_is_mock,
            "scraped": beike_scraped_count,
            "success": len(clean_df),
            "failed":  (beike_scraped_count - len(clean_df)) if not beike_is_mock else 0,
        },
        "rent": {
            "name":    "贝壳租房",
            "is_mock": rent_is_mock,
            "scraped": rent_scraped_count,
            "success": len(rent_df),
            "failed":  0,
        },
        "reno": {
            "name":           "土巴兔",
            "is_mock":        reno_data_source == "mock_fallback",
            "total_cases":    int(len(cases_df)),
            "success_pages":  int(tubatu_stats.get("success_pages", 0)),
            "failed_pages":   int(tubatu_stats.get("failed_pages", 0)),
            "detail_success": int(tubatu_stats.get("detail_success", 0)),
            "detail_failed":  int(tubatu_stats.get("detail_failed", 0)),
        },
    }

    # ------------------------------------------------------------------
    # Step 5：计算租售比
    # ------------------------------------------------------------------
    print("[Step 5/13] 计算租售比指标...")
    ratio_result = calc_rent_sale_ratio(clean_df, rent_df)

    print(f"  挂牌均价（中位）: {ratio_result['avg_unit_price']:,.0f} 元/㎡")
    print(f"  月租金（中位）:   {ratio_result['avg_monthly_rent']:.1f} 元/㎡")
    print(f"  租售比:           {ratio_result['ratio']}")
    print(f"  年化毛收益率:     {ratio_result['annual_yield']:.2f}%\n")

    # ------------------------------------------------------------------
    # Step 6：DCF 估值对比
    # ------------------------------------------------------------------
    print("[Step 6/13] DCF 估值对比（三种商业模式）...\n")

    area_median = clean_df["unit_price"].median()
    total_price = area_median * CONFIG["dcf_params"]["sqm"]
    dcf_results = {}

    print(f"  房屋参数：{CONFIG['dcf_params']['sqm']}㎡，总价约 {total_price/10000:.0f} 万元")
    print(f"  首付 {CONFIG['dcf_params']['down_pct']*100:.0f}%：{total_price*CONFIG['dcf_params']['down_pct']/10000:.0f} 万元\n")
    print(f"  {'模式':<10} {'年化毛收益':>10} {'初始投入(万)':>13} {'月供(元)':>12} {'10年NPV(万)':>13} {'IRR':>10}")
    print("  " + "-" * 72)

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
            terminal_cap  = _mode_terminal_cap(mode_params),
            initial_capex = _mode_initial_capex(mode_params, CONFIG["dcf_params"]["sqm"]),
        )

        gross_yield = (mode_params["rent_per_sqm"] * 12 * mode_params["occ_rate"] /
                      (total_price / CONFIG["dcf_params"]["sqm"]) * 100)
        npv_10yr = -dcf_result["initial_investment"] + sum(
            cf / (1 + CONFIG["dcf_params"]["discount_rate"]) ** y
            for y, cf in enumerate(dcf_result["cash_flows"][:10], 1)
        )

        dcf_results[mode_name] = dcf_result
        print(f"  {mode_name:<10} {gross_yield:>9.2f}%  "
              f"{dcf_result['initial_investment']/10000:>11.1f}万  "
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
            terminal_cap  = _mode_terminal_cap(mode_params),
            initial_capex = _mode_initial_capex(mode_params, CONFIG["dcf_params"]["sqm"]),
        )

    # 已持有情景：不计原始购房成本，仅分析未来 NOI 现金流（沉没成本不计入）
    owned_results = {}
    for mode_name in BUSINESS_MODES:
        cfs = no_loan_results[mode_name]["cash_flows"]
        year1_noi = cfs[0]
        owned_results[mode_name] = {
            "cash_flows":     cfs,
            "year1_noi":      year1_noi,
            "cumulative_noi": sum(cfs),
            "cap_rate":       year1_noi / total_price,
        }

    # ------------------------------------------------------------------
    # Step 7：敏感性分析
    # ------------------------------------------------------------------
    print("\n[Step 7/13] 敏感性分析...")
    sensitivity_results = run_sensitivity(
        base_total_price  = total_price,
        dcf_params        = CONFIG["dcf_params"],
        business_modes    = BUSINESS_MODES,
        terminal_cap_base = 0.04,
    )

    # ------------------------------------------------------------------
    # Step 8：采集地铁站和学校 POI
    # ------------------------------------------------------------------
    print("\n[Step 8/13] 采集地铁站和学校 POI（Overpass API）...")

    poi = POIScraper()
    metro_df, school_df = poi.fetch_all()

    print(f"  地铁站: {len(metro_df)} 条")
    print(f"  学校:   {len(school_df)} 条\n")

    # ------------------------------------------------------------------
    # Step 9：Nominatim 地理编码（小区名称 → BD-09 坐标）
    # ------------------------------------------------------------------
    print("[Step 9/13] 查询小区坐标（Nominatim）...")

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
        print("  [跳过] 地理编码无结果，Step 10 将使用 mock 数据模式\n")

    # ------------------------------------------------------------------
    # Step 10：地理特征工程 + 逐小区 IRR 计算
    # ------------------------------------------------------------------
    print("[Step 10/13] 地理特征工程 + 逐小区 IRR 估算...")

    geo_df = add_geo_features(clean_df, metro_df, school_df)
    geo_df = calc_per_community_irr(
        geo_df,
        dcf_params     = CONFIG["dcf_params"],
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
    # Step 11：IRR 相关性回归分析
    # ------------------------------------------------------------------
    print("\n[Step 11/13] IRR 与地铁/学校距离回归分析...")

    regression_results = run_all_regressions(geo_df.copy(), list(BUSINESS_MODES.keys()))

    return {
        "clean_df":            clean_df,
        "data_source_info":    data_source_info,
        "ratio_result":        ratio_result,
        "dcf_results":         dcf_results,
        "no_loan_results":     no_loan_results,
        "owned_results":       owned_results,
        "sensitivity_results": sensitivity_results,
        "geo_df":              geo_df,
        "metro_df":            metro_df,
        "school_df":           school_df,
        "regression_results":  regression_results,
    }


# =============================================================================
# 报告阶段（Steps 12-13）
# =============================================================================

def _run_report_phase(
    scrape_out: dict,
    analysis_out: dict,
    output_dir: Path,
) -> dict:
    clean_df            = analysis_out["clean_df"]
    rent_df             = scrape_out["rent_df"]
    reno_summary        = scrape_out["reno_summary"]
    cases_df            = scrape_out["cases_df"]
    tubatu_stats        = scrape_out["tubatu_stats"]
    reno_data_source    = scrape_out["reno_data_source"]
    data_source_info    = analysis_out["data_source_info"]
    ratio_result        = analysis_out["ratio_result"]
    dcf_results         = analysis_out["dcf_results"]
    no_loan_results     = analysis_out["no_loan_results"]
    owned_results       = analysis_out["owned_results"]
    sensitivity_results = analysis_out["sensitivity_results"]
    geo_df              = analysis_out["geo_df"]
    metro_df            = analysis_out["metro_df"]
    school_df           = analysis_out["school_df"]
    regression_results  = analysis_out["regression_results"]

    # ------------------------------------------------------------------
    # Step 12：生成全部可视化图表
    # ------------------------------------------------------------------
    print("\n[Step 12/13] 生成可视化图表...")

    chart_paths = generate_all_charts(
        house_df            = clean_df,
        rent_df             = rent_df,
        reno_summary        = reno_summary,
        dcf_results         = dcf_results,
        dcf_params          = CONFIG["dcf_params"],
        output_dir          = output_dir / "charts",
        geo_df              = geo_df,
        metro_df            = metro_df,
        school_df           = school_df,
        regression_results  = regression_results,
        no_loan_results     = no_loan_results,
        owned_results       = owned_results,
        sensitivity_results = sensitivity_results,
        data_source_info    = data_source_info,
    )

    # ------------------------------------------------------------------
    # Step 13：保存 KPI 摘要 + 生成决策端 HTML 报告
    # ------------------------------------------------------------------
    print("\n[Step 13/13] 生成决策端 HTML 报告...")

    # 提取最优商业模式 IRR（有贷款）
    best_mode, best_irr = "—", None
    for mode_name, r in dcf_results.items():
        irr = r.get("irr")
        if irr is not None and not pd.isna(irr):
            if best_irr is None or irr > best_irr:
                best_mode, best_irr = mode_name, irr

    # 提取最优商业模式 IRR（无贷款）
    best_nl_mode, best_nl_irr = "—", None
    for mode_name, r in no_loan_results.items():
        irr = r.get("irr")
        if irr is not None and not pd.isna(irr):
            if best_nl_irr is None or irr > best_nl_irr:
                best_nl_mode, best_nl_irr = mode_name, irr

    # 提取已持有情景最优模式（按 Year1 NOI 排序）
    best_owned_mode, best_owned_noi, best_owned_cap = "—", None, None
    for mode_name, r in owned_results.items():
        noi = r.get("year1_noi")
        if noi is not None and (best_owned_noi is None or noi > best_owned_noi):
            best_owned_mode = mode_name
            best_owned_noi  = noi
            best_owned_cap  = r.get("cap_rate")

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
        "has_regression":       (output_dir / "charts/IRR回归分析.png").exists(),
        "best_no_loan_mode":    best_nl_mode,
        "best_no_loan_irr_pct": (round(best_nl_irr * 100, 2) if best_nl_irr is not None else None),
        "has_no_loan":          (output_dir / "charts/无贷款IRR对比.html").exists(),
        "best_owned_mode":         best_owned_mode,
        "best_owned_noi_wan":      (round(best_owned_noi / 10000, 2) if best_owned_noi is not None else None),
        "best_owned_monthly_wan":  (round(best_owned_noi / 12 / 10000, 2) if best_owned_noi is not None else None),
        "best_owned_cap_rate_pct": (round(best_owned_cap * 100, 2) if best_owned_cap is not None else None),
        "has_owned":               (output_dir / "charts/已持有NOI对比.html").exists(),
        "has_sensitivity":         (output_dir / "charts/敏感性分析表.html").exists(),
        "reno_data_source":        reno_data_source,
        "reno_case_count":         int(len(cases_df)),
        "reno_success_pages":      int(tubatu_stats.get("success_pages", 0)),
        "reno_failed_pages":       int(tubatu_stats.get("failed_pages", 0)),
        "reno_detail_success":     int(tubatu_stats.get("detail_success", 0)),
        "reno_detail_failed":      int(tubatu_stats.get("detail_failed", 0)),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    dashboard_path = generate_dashboard(output_dir)
    print(f"  KPI 摘要已保存: {output_dir / 'summary.json'}")
    print(f"  决策报告已生成: {dashboard_path.absolute()}")

    print(f"\n[完成] 全部分析完成！")
    print(f"  输出目录: {output_dir.absolute()}")
    print(f"  图表数量: {len(chart_paths)} 个")
    print(f"  决策报告: {dashboard_path.name}")

    return {"chart_paths": chart_paths, "dashboard_path": dashboard_path}


# =============================================================================
# CSV 辅助 + 分析模式加载
# =============================================================================

def _save_csv(df: pd.DataFrame, data_dir: Path, pattern: str, filename: str) -> Path:
    """删除旧的同类文件后保存新文件，确保目录里每种数据只保留最新一份。"""
    for old in data_dir.glob(pattern):
        old.unlink()
    save_path = data_dir / filename
    df.to_csv(save_path, index=False, encoding="utf-8-sig")
    return save_path


def _try_load_latest_csv(
    data_dir: Path,
    pattern: str,
    label: str,
    min_rows: int = 1,
):
    """加载最近一次缓存 CSV；数据量不足时返回 None 让爬虫刷新。"""
    matches = sorted(data_dir.glob(pattern), reverse=True)
    if not matches:
        print(f"[缓存] 未找到 {label} 缓存（{pattern}），将尝试重新采集")
        return None

    latest = matches[0]
    try:
        df = pd.read_csv(latest)
    except Exception as e:
        print(f"[缓存] {label} 缓存读取失败: {latest.name} | {e}")
        return None

    if len(df) < min_rows:
        print(f"[缓存] {label} 缓存数据量不足: {latest.name} ({len(df)} 条)")
        return None

    print(f"[缓存] 使用 {label} 缓存: {latest.name} ({len(df)} 条)")
    return df


def _mode_terminal_cap(mode_params: dict, default: float = 0.04) -> float:
    return float(mode_params.get("terminal_cap", default))


def _mode_initial_capex(mode_params: dict, sqm: float) -> float:
    return (
        float(mode_params.get("initial_capex", 0.0))
        + float(mode_params.get("initial_capex_per_sqm", 0.0)) * sqm
    )


def _load_latest_scraped_data(data_dir: Path) -> dict:
    """
    从 data_dir 中找到最新的三份爬虫 CSV，加载并返回 dict。
    文件名含时间戳（beike_prices_YYYYMMDD_HHMM.csv），倒序排列取第一个即为最新。
    """
    def _latest(pattern: str) -> Path:
        matches = sorted(data_dir.glob(pattern), reverse=True)
        if not matches:
            raise FileNotFoundError(
                f"在 {data_dir} 找不到 {pattern}。\n"
                f"请先用 --mode scrape 或 --mode full 采集数据。"
            )
        return matches[0]

    beike_path  = _latest("beike_prices_*.csv")
    rent_path   = _latest("beike_rent_*.csv")
    tubatu_path = _latest("tubatu_cases_*.csv")

    print(f"[分析模式] 买房数据: {beike_path.name}")
    print(f"[分析模式] 租房数据: {rent_path.name}")
    print(f"[分析模式] 装修数据: {tubatu_path.name}\n")

    beike_df  = pd.read_csv(beike_path)
    rent_df   = pd.read_csv(rent_path)
    cases_df  = pd.read_csv(tubatu_path)

    return {
        "beike_df":            beike_df,
        "rent_df":             rent_df,
        "cases_df":            cases_df,
        "beike_scraped_count": len(beike_df),
        "beike_is_mock":       False,
        "rent_scraped_count":  len(rent_df),
        "rent_is_mock":        False,
        "reno_data_source":    "to8to_real",
        "tubatu_stats":        {},
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

def _select_scrape_steps() -> set:
    print()
    print("  请选择要采集的步骤（多选，逗号分隔，直接回车 = 全部）：")
    print("  [1] 贝壳二手房价格")
    print("  [2] 贝壳租房价格")
    print("  [3] 土巴兔装修报价")
    print()
    while True:
        raw = input("  请输入（如 1,3）：").strip()
        if not raw:
            return {1, 2, 3}
        parts = [p.strip() for p in raw.split(",")]
        if all(p in ("1", "2", "3") for p in parts):
            return {int(p) for p in parts}
        print("  无效输入，请输入 1、2、3 的任意组合（逗号分隔）")


def _select_mode() -> tuple:
    print(f"\n{'='*60}")
    print(f"  广州海珠区房产投资分析系统")
    print(f"{'='*60}")
    print("  请选择运行模式：")
    print("  [1] 完整流程（爬虫 + 分析）")
    print("  [2] 仅爬虫  （采集数据，保存 CSV 后退出）")
    print("  [3] 仅分析  （加载已有 CSV，跳过爬虫）")
    print()
    mapping = {"1": "full", "2": "scrape", "3": "analyze"}
    while True:
        choice = input("  请输入 1 / 2 / 3：").strip()
        if choice in mapping:
            mode = mapping[choice]
            steps = _select_scrape_steps() if mode == "scrape" else None
            return mode, steps
        print("  无效输入，请输入 1、2 或 3")


def _parse_args():
    parser = argparse.ArgumentParser(description="广州海珠区房产投资分析系统")
    parser.add_argument(
        "--mode",
        choices=["full", "scrape", "analyze"],
        help="运行模式；不传时进入交互选择",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=CONFIG["output_dir"],
        help="读取/写入爬虫 CSV 的目录，默认 output",
    )
    parser.add_argument(
        "--refresh-beike",
        action="store_true",
        help="忽略贝壳缓存，重新抓取买房和租房公开列表页",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="不使用缓存；与 --refresh-beike 类似，但保留语义给后续其他数据源使用",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="运行前打开贝壳登录窗口；默认不登录",
    )
    parser.add_argument(
        "--steps",
        type=str,
        default=None,
        metavar="1,2,3",
        help="指定爬虫步骤（仅 --mode scrape 时有效），逗号分隔；默认全部。1=贝壳买房 2=贝壳租房 3=土巴兔",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    scrape_steps = None
    if args.mode:
        mode = args.mode
        if args.steps:
            scrape_steps = {int(s.strip()) for s in args.steps.split(",")
                            if s.strip() in ("1", "2", "3")}
    else:
        mode, scrape_steps = _select_mode()
    asyncio.run(
        run_full_pipeline(
            mode=mode,
            data_dir=args.data_dir,
            refresh_beike=args.refresh_beike,
            use_cache=not args.no_cache,
            require_login=args.login,
            scrape_steps=scrape_steps,
        )
    )
