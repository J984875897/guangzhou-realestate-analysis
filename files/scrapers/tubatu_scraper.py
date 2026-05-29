# =============================================================================
# scrapers/tubatu_scraper.py
# 土巴兔装修报价数据采集
# =============================================================================

import asyncio
import re
import random
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import pandas as pd

from scrapers.browser_engine import (
    BrowserEngine,
    async_human_delay,
    human_scroll,
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
    source_url:     str   = ""


@dataclass
class MaterialPrice:
    name:     str   = ""
    category: str   = ""
    brand:    str   = ""
    unit:     str   = ""
    price:    float = 0.0
    level:    str   = ""


class TubatuScraper:
    # 旧入口 https://www.tubatu.com/cases/ 在当前环境会 TLS handshake failure；
    # 当前可访问的土巴兔整屋案例入口在 xiaoguotu.to8to.com。
    CASE_LIST_URL  = "https://xiaoguotu.to8to.com/case/area{area_code}/p{page}.html"
    PRICE_CALC_URL = "https://www.tubatu.com/price/?city={city}&area={area}"
    MATERIAL_URL   = "https://www.tubatu.com/cailiao/{city}/pg{page}/"
    CASES_PER_PAGE_LIMIT = 12
    DETAIL_CONCURRENCY   = 2

    AREA_BUCKETS = [
        (0,   60,  "1"),
        (61,  80,  "2"),
        (81,  100, "3"),
        (101, 120, "4"),
        (121, 150, "5"),
        (151, 200, "6"),
        (201, 999, "10"),
    ]

    CITY_DETAIL_HOSTS = {
        "上海": {"sh.to8to.com", "shanghai.to8to.com"},
        "北京": {"bj.to8to.com", "beijing.to8to.com"},
        "广州": {"gz.to8to.com", "guangzhou.to8to.com"},
        "深圳": {"sz.to8to.com", "shenzhen.to8to.com"},
        "杭州": {"hz.to8to.com", "hangzhou.to8to.com"},
        "成都": {"cd.to8to.com", "chengdu.to8to.com"},
    }

    LEVEL_KEYWORDS = {
        "豪装":   ["豪装", "豪华", "顶配", "进口"],
        "高档":   ["高档", "高端", "品质", "精装", "全屋定制"],
        "中档":   ["中档", "中等", "标准", "舒适"],
        "经济型": ["经济", "简装", "基础", "实惠", "性价比"],
    }

    def __init__(self, city: str = "广州"):
        self.city = city
        self.last_case_stats = {}
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
        area_codes = self._get_area_codes(area_range)
        cases = []
        seen_ids = set()
        stats = {
            "source": "to8to_xiaoguotu",
            "requested_pages": 0,
            "success_pages": 0,
            "failed_pages": 0,
            "detail_success": 0,
            "detail_failed": 0,
            "failures": [],
        }

        async with BrowserEngine(headless=True) as engine:
            context, page = await engine.new_page()

            try:
                sem = asyncio.Semaphore(self.DETAIL_CONCURRENCY)

                async def _fetch_one(it):
                    async with sem:
                        ctx, dp = await engine.new_page()
                        try:
                            return await self._fetch_case_detail(dp, it)
                        finally:
                            await ctx.close()

                for area_code in area_codes:
                    for page_num in range(1, pages + 1):
                        url = self.CASE_LIST_URL.format(area_code=area_code, page=page_num)
                        stats["requested_pages"] += 1
                        print(f"[土巴兔] area{area_code} 第 {page_num}/{pages} 页: {url}")

                        success, err = await self._goto(page, url)
                        if not success:
                            stats["failed_pages"] += 1
                            stats["failures"].append({"url": url, "error": err})
                            continue

                        try:
                            await page.wait_for_selector(
                                ".xmp_container .item",
                                state="attached",
                                timeout=15_000,
                            )
                            await human_scroll(page, scroll_times=3)
                        except Exception as e:
                            stats["failed_pages"] += 1
                            stats["failures"].append({"url": url, "error": f"列表选择器失败: {e}"})
                            continue

                        list_items = await self._parse_case_list(page)
                        stats["success_pages"] += 1
                        print(f"[土巴兔]   发现 {len(list_items)} 个候选，正在并发采集详情...")

                        new_items = []
                        for item in list_items:
                            cid = item.get("case_id") or item.get("url")
                            if cid not in seen_ids:
                                seen_ids.add(cid)
                                new_items.append(item)

                        results = await asyncio.gather(*[_fetch_one(it) for it in new_items])

                        page_ok = 0
                        for detail in results:
                            if detail is None:
                                stats["detail_failed"] += 1
                                continue
                            stats["detail_success"] += 1
                            page_ok += 1
                            if detail.area > 0 and detail.total_price > 0:
                                cases.append(detail)

                        print(f"[土巴兔]   详情获取 {page_ok}/{len(new_items)} 条，累计 {len(cases)} 条")

                        if page_num < pages:
                            await async_human_delay(1.2, 2.5)

            finally:
                await context.close()

        self.last_case_stats = stats
        print(f"[土巴兔] 共采集 {len(cases)} 个装修案例")
        print(
            f"[土巴兔] 页面成功/失败: {stats['success_pages']}/{stats['failed_pages']}，"
            f"详情成功/失败: {stats['detail_success']}/{stats['detail_failed']}"
        )
        return cases

    async def _goto(
        self,
        page,
        url: str,
        retries: int = 2,
        wait_until: str = "commit",
        timeout: int = 20_000,
        log_errors: bool = True,
    ) -> tuple[bool, str]:
        last_error = ""
        for attempt in range(1, retries + 1):
            try:
                await page.goto(url, wait_until=wait_until, timeout=timeout)
                await async_human_delay(0.8, 1.8)
                return True, ""
            except Exception as e:
                last_error = str(e)
                if log_errors:
                    print(f"[土巴兔] 访问失败({attempt}/{retries}): {url} | {last_error}")
                await async_human_delay(1.0, 2.0)
        return False, last_error

    async def _parse_case_list(self, page) -> list[dict]:
        return await page.evaluate("""
            (limit) => {
                const out = [];
                const seen = new Set();
                const cards = Array.from(document.querySelectorAll('.xmp_container .item'));
                const candidates = cards.length
                    ? cards.map(card => ({
                        card,
                        anchor: card.querySelector('a.item_img[href*="/case/"], a.title[href*="/case/"]')
                    }))
                    : Array.from(document.querySelectorAll('a[href*="/case/"]'))
                        .map(anchor => ({card: anchor.closest('li, .item, .case-item, .media-item, .list-item, .pic-li') || anchor.parentElement, anchor}));

                for (const {card, anchor: a} of candidates) {
                    if (!a) continue;
                    const rawHref = a.getAttribute('href') || '';
                    let href = '';
                    try { href = new URL(rawHref, location.href).href; } catch (e) { continue; }

                    const isCompanyCase = /\\/zs\\/\\d+\\/case\\/\\d+\\.html/.test(href);
                    const isXgtCase = /xiaoguotu\\.to8to\\.com\\/case\\/zxanli\\/t\\d+\\.html/.test(href);
                    if (!isCompanyCase && !isXgtCase) continue;

                    const match = href.match(/\\/case\\/(?:zxanli\\/t)?(\\d+)\\.html/);
                    const caseId = match ? match[1] : href;
                    if (seen.has(caseId)) continue;
                    seen.add(caseId);

                    const text = (card ? card.innerText : a.innerText || '').replace(/\\s+/g, ' ').trim();
                    out.push({case_id: caseId, url: href, list_text: text});
                    if (out.length >= limit) break;
                }
                return out;
            }
        """, self.CASES_PER_PAGE_LIMIT)

    async def _fetch_case_detail(self, page, item: dict) -> Optional[RenovationCase]:
        url = item.get("url", "")
        if self._should_skip_detail_url(url):
            return None

        fallback = self._parse_case_data({
            "case_id": item.get("case_id", ""),
            "source_url": url,
            "list_text": item.get("list_text", ""),
        })
        if fallback.area > 0 and fallback.total_price > 0:
            return fallback

        success, err = await self._goto(
            page,
            url,
            retries=2,
            wait_until="commit",
            timeout=20_000,
            log_errors=False,
        )
        if not success:
            print(f"[土巴兔] 详情页失败，已跳过: {url} | {self._short_error(err)}")
            return None

        try:
            await page.wait_for_selector("body", timeout=8_000)
        except Exception:
            pass

        try:
            raw = await page.evaluate("""
                () => {
                    const desc = document.querySelector('.case-desc');
                    const bodyText = (desc ? desc.innerText : document.body.innerText || '')
                        .replace(/\\s+/g, ' ').trim();
                    const metaDesc = document.querySelector('meta[name="description"]')?.content || '';
                    const title = document.title || '';
                    const company = document.querySelector('.company-name, .name, .company-title')?.innerText || '';
                    return {body_text: bodyText, meta_desc: metaDesc, title, company};
                }
            """)
        except Exception as e:
            print(f"[土巴兔] 详情解析失败: {url} | {e}")
            return None

        raw.update({
            "case_id": item.get("case_id", ""),
            "source_url": url,
            "list_text": item.get("list_text", ""),
        })
        case = self._parse_case_data(raw)
        return case if case.area > 0 and case.total_price > 0 else None

    def _should_skip_detail_url(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        if not host or host == "xiaoguotu.to8to.com":
            return False

        if host.endswith(".to8to.com"):
            allowed_hosts = self.CITY_DETAIL_HOSTS.get(self.city)
            return bool(allowed_hosts and host not in allowed_hosts)

        return False

    def _short_error(self, err: str, limit: int = 160) -> str:
        one_line = re.sub(r"\s+", " ", err or "").strip()
        return one_line[:limit] + ("..." if len(one_line) > limit else "")

    def _parse_case_data(self, raw: dict) -> RenovationCase:
        case = RenovationCase(city=self.city)
        case.case_id = raw.get("case_id", "")
        case.source_url = raw.get("source_url", "")
        text = " ".join([
            raw.get("body_text", ""),
            raw.get("meta_desc", ""),
            raw.get("title", ""),
            raw.get("list_text", ""),
        ])

        area_match = (
            re.search(r"面积[:：]?\s*(\d+\.?\d*)\s*(?:㎡|m²|平)", text)
            or re.search(r"(\d+\.?\d*)\s*(?:㎡|m²|平)", text)
        )
        if area_match:
            case.area = float(area_match.group(1))

        price_match = (
            re.search(r"造价[:：]?\s*(\d+\.?\d*)\s*万元?", text)
            or re.search(r"(\d+\.?\d*)\s*万元?", text)
        )
        if price_match:
            case.total_price = float(price_match.group(1)) * 10000
        else:
            price_text = raw.get("price_text", "")
            price_match = re.search(r"(\d+)", price_text.replace(",", ""))
            if price_match:
                case.total_price = float(price_match.group(1))

        if case.area > 0 and case.total_price > 0:
            case.unit_price = case.total_price / case.area

        style_match = re.search(r"风格[:：]?\s*([\u4e00-\u9fa5A-Za-z0-9]+)", text)
        case.style = (style_match.group(1) if style_match else raw.get("style_text", "")).strip()
        case.company = raw.get("company", "").strip()

        duration_match = re.search(r"装修工期[:：]?\s*(\d+)\s*天", text)
        if duration_match:
            case.duration_days = int(duration_match.group(1))

        year_match = re.search(r"(20\d{2})", raw.get("year_text", "") or text)
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

    def _get_area_codes(self, area_range: tuple) -> list[str]:
        target_min, target_max = area_range
        codes = [
            code
            for code_min, code_max, code in self.AREA_BUCKETS
            if max(target_min, code_min) <= min(target_max, code_max)
        ]
        return codes or ["3"]


if __name__ == "__main__":
    async def main():
        scraper = TubatuScraper(city="广州")
        cases = await scraper.scrape_cases(area_range=(70, 110), pages=5)
        df = TubatuScraper.cases_to_dataframe(cases)
        print(TubatuScraper.summarize_by_level(df).to_string())
        df.to_csv("tubatu_cases.csv", index=False, encoding="utf-8-sig")

    asyncio.run(main())
