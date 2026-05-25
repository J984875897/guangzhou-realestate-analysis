# =============================================================================
# scrapers/tubatu_scraper.py
# 土巴兔装修报价数据采集
# =============================================================================

import asyncio
import re
import random
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from scrapers.browser_engine import (
    BrowserEngine,
    async_human_delay,
    human_scroll,
    wait_for_peak_hour,
)


@dataclass
class RenovationCase:
    case_id:        str   = ""
    city:           str   = ""
    area:           float = 0.0
    style:          str   = ""
    level:          str   = ""
    total_price:    float = 0.0
    unit_price:     float = 0.0
    duration_days:  int   = 0
    company:        str   = ""
    year:           int   = 0
    labor_cost:     float = 0.0
    material_cost:  float = 0.0
    auxiliary_cost: float = 0.0
    design_cost:    float = 0.0
    furniture_cost: float = 0.0


@dataclass
class MaterialPrice:
    name:     str   = ""
    category: str   = ""
    brand:    str   = ""
    unit:     str   = ""
    price:    float = 0.0
    level:    str   = ""


class TubatuScraper:
    CASE_LIST_URL  = "https://www.tubatu.com/cases/?city={city}&area={area_code}&style=0&page={page}"
    PRICE_CALC_URL = "https://www.tubatu.com/price/?city={city}&area={area}"
    MATERIAL_URL   = "https://www.tubatu.com/cailiao/{city}/pg{page}/"

    AREA_CODE_MAP = {
        (0,   60):  "1",
        (60,  90):  "2",
        (90,  120): "3",
        (120, 150): "4",
        (150, 200): "5",
        (200, 999): "6",
    }

    LEVEL_KEYWORDS = {
        "豪装":   ["豪装", "豪华", "顶配", "进口"],
        "高档":   ["高档", "高端", "品质", "精装", "全屋定制"],
        "中档":   ["中档", "中等", "标准", "舒适"],
        "经济型": ["经济", "简装", "基础", "实惠", "性价比"],
    }

    def __init__(self, city: str = "广州"):
        self.city = city
        self.city_path = {
            "上海": "shanghai", "北京": "beijing",
            "广州": "guangzhou", "深圳": "shenzhen",
            "杭州": "hangzhou",  "成都": "chengdu",
        }.get(city, city)

    async def scrape_cases(
        self,
        area_range: tuple[int, int] = (80, 100),
        pages: int = 5,
    ) -> list[RenovationCase]:
        wait_for_peak_hour()

        area_code = self._get_area_code(area_range)
        cases = []

        async with BrowserEngine(headless=True) as engine:
            context, page = await engine.new_page()

            try:
                for page_num in range(1, pages + 1):
                    url = self.CASE_LIST_URL.format(
                        city=self.city,
                        area_code=area_code,
                        page=page_num
                    )
                    print(f"[土巴兔] 案例列表第 {page_num}/{pages} 页: {url}")

                    success = await engine.goto(page, url)
                    if not success:
                        continue

                    await page.wait_for_selector(".case-list-item", timeout=20_000)
                    await human_scroll(page, scroll_times=4)

                    page_cases = await self._parse_case_list(page)
                    cases.extend(page_cases)
                    print(f"[土巴兔] 第{page_num}页解析到 {len(page_cases)} 个案例")

                    if page_num < pages:
                        await async_human_delay(3.0, 7.0)

            finally:
                await context.close()

        print(f"[土巴兔] 共采集 {len(cases)} 个装修案例")
        return cases

    async def _parse_case_list(self, page) -> list[RenovationCase]:
        cases = []
        case_elements = await page.query_selector_all(".case-list-item")

        for el in case_elements:
            try:
                data = await el.evaluate("""
                    (el) => ({
                        case_id:    el.dataset.id || el.getAttribute('data-id') || '',
                        area_text:  el.querySelector('.area')?.textContent || '',
                        price_text: el.querySelector('.price, .total-price')?.textContent || '',
                        style_text: el.querySelector('.style, .tag-style')?.textContent || '',
                        company:    el.querySelector('.company-name')?.textContent || '',
                        year_text:  el.querySelector('.year, .date')?.textContent || '',
                    })
                """)

                case = self._parse_case_data(data)
                if case.total_price > 0 and case.area > 0:
                    cases.append(case)

            except Exception:
                continue

        return cases

    def _parse_case_data(self, raw: dict) -> RenovationCase:
        case = RenovationCase(city=self.city)
        case.case_id = raw.get("case_id", "")

        area_match = re.search(r"(\d+\.?\d*)", raw.get("area_text", ""))
        if area_match:
            case.area = float(area_match.group(1))

        price_text = raw.get("price_text", "")
        price_match = re.search(r"(\d+\.?\d*)\s*万", price_text)
        if price_match:
            case.total_price = float(price_match.group(1)) * 10000
        else:
            price_match = re.search(r"(\d+)", price_text.replace(",", ""))
            if price_match:
                case.total_price = float(price_match.group(1))

        if case.area > 0 and case.total_price > 0:
            case.unit_price = case.total_price / case.area

        case.style   = raw.get("style_text", "").strip()
        case.company = raw.get("company", "").strip()

        year_match = re.search(r"(20\d{2})", raw.get("year_text", ""))
        if year_match:
            case.year = int(year_match.group(1))

        case.level = self._infer_level(case.unit_price)
        return case

    def _infer_level(self, unit_price: float) -> str:
        if unit_price >= 3000:
            return "豪装"
        elif unit_price >= 1500:
            return "高档"
        elif unit_price >= 800:
            return "中档"
        else:
            return "经济型"

    async def scrape_material_prices(
        self,
        categories: list[str] = None,
        pages_per_category: int = 3,
    ) -> list[MaterialPrice]:
        wait_for_peak_hour()

        if categories is None:
            categories = [
                "diban",       # 地板
                "cituan",      # 瓷砖
                "tuciliao",    # 涂料
                "weiyujianji", # 卫浴洁具
                "chufang",     # 厨房
                "menchuang",   # 门窗（已修正西里尔字母 typo）
            ]

        all_materials = []

        async with BrowserEngine(headless=True) as engine:
            context, page = await engine.new_page()

            try:
                for category in categories:
                    for pg in range(1, pages_per_category + 1):
                        url = self.MATERIAL_URL.format(
                            city=self.city_path,
                            page=pg
                        ) + f"?cat={category}"

                        success = await engine.goto(page, url)
                        if not success:
                            continue

                        await page.wait_for_selector(".material-item", timeout=15_000)
                        await human_scroll(page, scroll_times=3)

                        materials = await self._parse_material_list(page, category)
                        all_materials.extend(materials)

                        await async_human_delay(2.0, 6.0)

            finally:
                await context.close()

        return all_materials

    async def _parse_material_list(self, page, category: str) -> list[MaterialPrice]:
        materials = []
        items = await page.query_selector_all(".material-item")

        for el in items:
            try:
                data = await el.evaluate("""
                    (el) => ({
                        name:       el.querySelector('.name, .title')?.textContent || '',
                        brand:      el.querySelector('.brand')?.textContent || '',
                        price_text: el.querySelector('.price')?.textContent || '',
                        unit:       el.querySelector('.unit')?.textContent || '',
                        spec:       el.querySelector('.spec, .model')?.textContent || '',
                    })
                """)

                price_match = re.search(r"(\d+\.?\d*)", data.get("price_text", ""))
                if not price_match:
                    continue

                m = MaterialPrice(
                    name=data.get("name", "").strip(),
                    category=category,
                    brand=data.get("brand", "").strip(),
                    unit=data.get("unit", "元/㎡").strip(),
                    price=float(price_match.group(1)),
                )
                m.level = self._infer_material_level(m.price, category)
                materials.append(m)

            except Exception:
                continue

        return materials

    def _infer_material_level(self, price: float, category: str) -> str:
        thresholds = {
            "diban":        {"high": 300,  "mid": 100},
            "cituan":       {"high": 200,  "mid":  60},
            "tuciliao":     {"high": 500,  "mid": 200},
            "weiyujianji":  {"high": 3000, "mid": 800},
            "chufang":      {"high": 8000, "mid": 3000},
        }
        t = thresholds.get(category, {"high": 200, "mid": 80})

        if price >= t["high"]:
            return "高档"
        elif price >= t["mid"]:
            return "中档"
        else:
            return "经济型"

    @staticmethod
    def cases_to_dataframe(cases: list[RenovationCase]) -> pd.DataFrame:
        return pd.DataFrame([vars(c) for c in cases])

    @staticmethod
    def summarize_by_level(df: pd.DataFrame) -> pd.DataFrame:
        summary = df.groupby("level").agg(
            案例数=    ("case_id",       "count"),
            平均单价=  ("unit_price",    "mean"),
            中位单价=  ("unit_price",    "median"),
            最低单价=  ("unit_price",    "min"),
            最高单价=  ("unit_price",    "max"),
            平均面积=  ("area",          "mean"),
            平均工期=  ("duration_days", "mean"),
        ).round(0)

        level_order = ["经济型", "中档", "高档", "豪装"]
        summary = summary.reindex([l for l in level_order if l in summary.index])
        return summary

    def _get_area_code(self, area_range: tuple) -> str:
        target_min, target_max = area_range
        for (code_min, code_max), code in self.AREA_CODE_MAP.items():
            if code_min <= target_min and target_max <= code_max:
                return code
        return "3"


if __name__ == "__main__":
    async def main():
        scraper = TubatuScraper(city="广州")
        cases = await scraper.scrape_cases(area_range=(70, 110), pages=5)
        df = TubatuScraper.cases_to_dataframe(cases)
        print(TubatuScraper.summarize_by_level(df).to_string())
        df.to_csv("tubatu_cases.csv", index=False, encoding="utf-8-sig")

    asyncio.run(main())
