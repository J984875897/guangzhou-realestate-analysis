# =============================================================================
# scrapers/beike_rent_scraper.py
# 贝壳租房地图接口批量采集租金数据
#
# 思路与 beike_map_scraper.py 相同：
#   拦截贝壳租房地图页（gz.ke.com/map/zufang/）的 Ajax 请求，
#   直接解析 JSON，获取各小区/板块的挂牌租金数据。
#
# 与买房爬虫的关键差异：
#   - 目标 URL：/map/zufang/ 而非 /map/ershoufang/
#   - API 返回价格字段为月租（元/月），需结合面积换算成 元/㎡/月
#   - 若 API 不返回面积，则在列表页补充采集单条租房面积
# =============================================================================

import asyncio
import json
import random
import re
from typing import Optional
from urllib.parse import quote

import pandas as pd

from scrapers.browser_engine import (
    BrowserEngine, async_human_delay, human_scroll,
    wait_for_peak_hour, load_session_cookies,
)


CITY_CONFIG = {
    "上海": {"city_code": "sh", "map_url": "https://sh.ke.com/map/", "rent_url": "https://sh.zu.ke.com/zufang/", "district_paths": {}},
    "北京": {"city_code": "bj", "map_url": "https://bj.ke.com/map/", "rent_url": "https://bj.zu.ke.com/zufang/", "district_paths": {}},
    "广州": {
        "city_code": "gz",
        "map_url": "https://gz.ke.com/map/",
        "rent_url": "https://gz.zu.ke.com/zufang/",
        "district_paths": {
            "海珠区": "haizhu",
            "天河区": "tianhe",
            "越秀区": "yuexiu",
            "荔湾区": "liwan",
            "白云区": "baiyun",
            "黄埔区": "huangpu",
            "番禺区": "panyu",
            "花都区": "huadu",
            "南沙区": "nansha",
            "增城区": "zengcheng",
            "从化区": "conghua",
        },
    },
    "深圳": {"city_code": "sz", "map_url": "https://sz.ke.com/map/", "rent_url": "https://sz.zu.ke.com/zufang/", "district_paths": {}},
}

# 广州海珠区典型租房面积（用于无法获取精确面积时的备用值）
DEFAULT_AREA_SQM = 90.0


class BeikeRentScraper:
    """
    通过拦截贝壳租房地图 Ajax 请求，批量获取各板块月租金数据

    使用方法：
        scraper = BeikeRentScraper(city="广州")
        df = await scraper.scrape_district(district="海珠区")
    """

    def __init__(self, city: str = "广州"):
        if city not in CITY_CONFIG:
            raise ValueError(f"不支持的城市，可选：{list(CITY_CONFIG.keys())}")
        self.city = city
        self.config = CITY_CONFIG[city]
        self.captured_data: list[dict] = []

    async def scrape_district(self, district: str, max_retries: int = 3) -> pd.DataFrame:
        wait_for_peak_hour()

        for attempt in range(1, max_retries + 1):
            try:
                print(f"[贝壳租房] 开始爬取 {self.city} - {district}（第{attempt}次尝试）")
                result = await self._do_scrape(district)
                if result is not None and len(result) > 0:
                    print(f"[贝壳租房] 成功获取 {len(result)} 条租房数据")
                    return result
            except Exception as e:
                print(f"[贝壳租房] 第{attempt}次失败: {e}")
                if attempt < max_retries:
                    wait_time = 30 * (2 ** attempt)
                    print(f"[贝壳租房] {wait_time}秒后重试...")
                    await asyncio.sleep(wait_time)

        print(f"[贝壳租房] {max_retries}次重试后仍失败，返回空数据")
        return pd.DataFrame()

    async def _do_scrape(self, district: str) -> Optional[pd.DataFrame]:
        self.captured_data = []

        async with BrowserEngine(headless=False, use_proxy=False) as engine:
            context, page = await engine.new_page()
            await load_session_cookies(context)

            try:
                list_df = await self._fallback_list_scrape(page, engine, district)
                if list_df is not None and not list_df.empty:
                    return list_df

                print("[贝壳租房] 租房列表页未采到数据，尝试地图接口...")
                # 拦截租房地图 API —— 贝壳租房地图的端点通常包含 /zufang/ 或 /rent/
                # 同时拦截多个可能的路径，实际生效的取决于页面版本
                for pattern in [
                    "**/proxyApi/i.o.rentMapData/**",
                    "**/proxyApi/i.o.rentMapPoint/**",
                    "**/api/rent/map/**",
                    "**/map/zufang/**",
                ]:
                    await page.route(pattern, self._intercept_rent_response)

                district_path = self._district_path(district)
                rent_url = f"{self.config['map_url']}zufang/{district_path}/"
                success = await engine.goto(page, rent_url)
                if not success:
                    return None

                # 等待地图容器加载
                await self._wait_for_map_ready(page, rent_url)

                await async_human_delay(2.0, 4.0)
                await self._simulate_map_interaction(page)
                await async_human_delay(3.0, 5.0)

                if not self.captured_data:
                    print("[贝壳租房] 地图接口未拦截到数据，返回空数据")
                    return await self._fallback_list_scrape(page, engine, district)

                return self._process_captured_data()

            finally:
                await context.close()

    def _district_path(self, district: str) -> str:
        """贝壳 URL 使用拼音区划路径；未知区划时退回 URL 编码。"""
        district_paths = self.config.get("district_paths", {})
        return district_paths.get(district, quote(district, safe=""))

    async def _wait_for_map_ready(self, page, expected_url: str) -> bool:
        try:
            await page.wait_for_selector(".map-wrapper, .map-container, #map", timeout=30_000)
            return True
        except Exception:
            title = await page.title()
            body_text = ""
            try:
                body_text = (await page.locator("body").inner_text(timeout=2_000)).strip()
            except Exception:
                pass
            if len(body_text) > 180:
                body_text = body_text[:180] + "..."
            print(
                "[贝壳租房] 未找到地图界面，将回退到列表页解析。\n"
                f"           目标URL: {expected_url}\n"
                f"           当前URL: {page.url}\n"
                f"           页面标题: {title}\n"
                f"           页面文本: {body_text}"
            )
            return False

    async def _intercept_rent_response(self, route, request):
        """拦截租房地图 API 响应，提取租金数据"""
        response = await route.fetch()

        try:
            body = await response.body()
            data = json.loads(body.decode("utf-8"))

            # 尝试多种可能的数据结构
            map_points = []
            if data.get("errno") == 0 and "data" in data:
                inner = data["data"]
                # 兼容多种字段名
                map_points = (
                    inner.get("mapPoints") or
                    inner.get("list") or
                    inner.get("houseList") or
                    []
                )
            elif isinstance(data.get("data"), list):
                map_points = data["data"]

            if map_points:
                print(f"[拦截] 捕获到 {len(map_points)} 条租房数据")
                self.captured_data.extend(map_points)

        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        except Exception as e:
            print(f"[拦截] 处理租房响应出错: {e}")
        finally:
            await route.fulfill(response=response)

    async def _fallback_list_scrape(
        self,
        page,
        engine,
        district: str,
        pages: int = 5,
    ) -> Optional[pd.DataFrame]:
        """
        解析用户可正常打开的贝壳租房列表页。
        列表页每条记录含租金和面积，精度高于地图聚合数据。
        """
        records = []
        district_path = self._district_path(district)

        for page_num in range(1, pages + 1):
            page_part = "" if page_num == 1 else f"pg{page_num}/"
            list_url = f"{self.config['rent_url']}{district_path}/{page_part}"
            print(f"[贝壳租房] 租房列表页 {page_num}/{pages}: {list_url}")

            success = await engine.goto(page, list_url)
            if not success:
                continue

            try:
                await page.wait_for_selector(".content__list--item", timeout=20_000)
            except Exception:
                title = await page.title()
                print(f"[贝壳租房] 租房列表页未找到房源卡片: {page.url} | {title}")
                continue

            await human_scroll(page, scroll_times=5)
            items = await page.query_selector_all(".content__list--item")

            for el in items:
                try:
                    data = await el.evaluate("""
                        (el) => {
                            const titleEl = el.querySelector('.content__list--item--title a, .content__list--item--name a, .content__list--item--name');
                            const communityEl = el.querySelector('.content__list--item--des a');
                            const priceEl = el.querySelector('.content__list--item-price');
                            const infoEl = el.querySelector('.content__list--item--des');
                            return {
                                house_title:    titleEl?.textContent || '',
                                community_name: communityEl?.textContent || '',
                                href:           titleEl?.href || '',
                                price_text:     priceEl?.textContent || '',
                                info_text:      infoEl?.textContent || '',
                            };
                        }
                    """)

                    monthly_rent = self._extract_monthly_rent(data.get("price_text", ""))
                    area = self._extract_area(data.get("info_text", "")) or DEFAULT_AREA_SQM

                    if monthly_rent <= 0:
                        continue

                    monthly_rent_per_sqm = monthly_rent / area if area > 0 else 0
                    house_title = data.get("house_title", "").strip()
                    community_name = data.get("community_name", "").strip()
                    href = data.get("href", "")

                    records.append({
                        "community_id":         self._extract_listing_id(href) or house_title,
                        "name":                 community_name,
                        "house_title":          house_title,
                        "district":             district,
                        "monthly_rent":         monthly_rent,
                        "area":                 area,
                        "monthly_rent_per_sqm": round(monthly_rent_per_sqm, 2),
                        "listing_count":        1,
                        "source":               "贝壳租房列表页",
                        "scraped_at":           pd.Timestamp.now().isoformat(),
                    })

                except Exception:
                    continue

            await async_human_delay(1.5, 3.0)

        if not records:
            return None

        df = pd.DataFrame(records)
        df = df[df["monthly_rent_per_sqm"] > 0]
        df = df.drop_duplicates(subset=["community_id", "house_title", "monthly_rent"])
        return df.reset_index(drop=True)

    def _extract_monthly_rent(self, text: str) -> float:
        match = re.search(r"(\d[\d,]*)", text or "")
        return float(match.group(1).replace(",", "")) if match else 0.0

    def _extract_area(self, text: str) -> float:
        match = re.search(r"(\d+\.?\d*)\s*(?:㎡|平)", text or "")
        return float(match.group(1)) if match else 0.0

    def _extract_listing_id(self, href: str) -> str:
        match = re.search(r"/zufang/([^/]+)\.html", href or "")
        return match.group(1) if match else ""

    async def _simulate_map_interaction(self, page):
        """模拟地图交互，触发懒加载数据（与买房爬虫相同逻辑）"""
        map_el = await page.query_selector(".map-wrapper, .map-container, #map")
        if not map_el:
            return

        box = await map_el.bounding_box()
        center_x = box["x"] + box["width"] / 2
        center_y = box["y"] + box["height"] / 2

        await page.mouse.move(center_x, center_y)
        for _ in range(3):
            await page.mouse.wheel(0, -300)
            await asyncio.sleep(random.uniform(0.8, 1.5))

        await async_human_delay(2.0, 3.0)

        directions = [(80, 0), (0, 80), (-80, 0), (0, -80)]
        for dx, dy in random.sample(directions, 2):
            await page.mouse.move(center_x, center_y)
            await page.mouse.down()
            await page.mouse.move(center_x + dx, center_y + dy, steps=10)
            await page.mouse.up()
            await async_human_delay(1.5, 3.0)

    def _process_captured_data(self) -> pd.DataFrame:
        """
        解析拦截到的地图 API 数据。
        贝壳租房地图的数据点可能是小区聚合（显示均价），
        也可能是单个房源（需要除以面积换算）。
        """
        records = []
        seen_ids = set()

        for point in self.captured_data:
            raw_id = point.get("id") or point.get("houseCode") or point.get("communityCode", "")
            if raw_id in seen_ids:
                continue
            seen_ids.add(raw_id)

            # 月租：兼容多种字段名
            monthly_rent = (
                point.get("price") or
                point.get("monthlyRent") or
                point.get("avgPrice") or
                point.get("unitPrice") or  # 有时地图返回统一用 unitPrice
                0
            )

            # 面积：地图聚合数据通常不含面积，用默认值
            area = (
                point.get("buildArea") or
                point.get("area") or
                DEFAULT_AREA_SQM
            )

            if monthly_rent <= 0:
                continue

            monthly_rent_per_sqm = monthly_rent / area if area > 0 else 0

            records.append({
                "community_id":         raw_id,
                "name":                 point.get("name") or point.get("communityName", ""),
                "district":             point.get("district", ""),
                "lng":                  point.get("lng"),
                "lat":                  point.get("lat"),
                "monthly_rent":         monthly_rent,
                "area":                 area,
                "monthly_rent_per_sqm": round(monthly_rent_per_sqm, 2),
                "listing_count":        point.get("totalCount") or point.get("count", 1),
                "source":               "贝壳地图",
                "scraped_at":           pd.Timestamp.now().isoformat(),
            })

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df = df[df["monthly_rent_per_sqm"] > 0]
        df = df.sort_values("monthly_rent_per_sqm", ascending=False).reset_index(drop=True)
        return df


# =============================================================================
# 多区域批量采集入口
# =============================================================================

async def batch_scrape_rent(city: str, districts: list[str]) -> pd.DataFrame:
    scraper = BeikeRentScraper(city=city)
    all_dfs = []

    for i, district in enumerate(districts):
        print(f"\n[进度] {i+1}/{len(districts)}：{district} 租房数据")

        df = await scraper.scrape_district(district)
        if not df.empty:
            if "district" not in df.columns or df["district"].eq("").all():
                df["district"] = district
            all_dfs.append(df)

        if i < len(districts) - 1:
            wait = random.uniform(30, 90)
            print(f"[调度] 等待 {wait:.0f} 秒后采集下一个区域...")
            await asyncio.sleep(wait)

    if not all_dfs:
        return pd.DataFrame()

    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.drop_duplicates(subset=["community_id", "name"])

    print(f"\n[完成] 共采集 {len(combined)} 条租房数据")
    return combined


if __name__ == "__main__":
    async def main():
        df = await batch_scrape_rent(city="广州", districts=["海珠区"])
        if not df.empty:
            print(df.head(10).to_string())
            print(f"\n月租均价: {df['monthly_rent_per_sqm'].median():.1f} 元/㎡/月")
            df.to_csv("beike_rent.csv", index=False, encoding="utf-8-sig")

    asyncio.run(main())
