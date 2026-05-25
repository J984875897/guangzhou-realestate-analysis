# =============================================================================
# scrapers/beike_map_scraper.py
# 贝壳二手房列表/地图接口批量采集房价
#
# 核心思路：
#   贝壳找房的地图页（map.ke.com）在前端用 Ajax 调用内部 API，
#   把地图上每个小区的位置和均价渲染成气泡。
#   我们用 Playwright 打开地图页，拦截这些 Ajax 请求，
#   直接拿 JSON 数据，比解析 HTML 稳定得多。
#
# 相比直接爬列表页的优势：
#   1. 一次请求覆盖整个地图区域内所有小区（几十到几百个）
#   2. 数据是结构化 JSON，不需要解析 HTML
#   3. 地图接口更新频率低，不容易因页面改版而失效
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
    is_peak_hour, wait_for_peak_hour, load_session_cookies,
)


# =============================================================================
# 贝壳地图接口参数说明
#
# 坐标系说明：贝壳使用百度坐标系（BD-09），不是 GPS 坐标（WGS-84）
# =============================================================================

CITY_CONFIG = {
    "上海": {"city_id": "310000", "city_code": "sh",  "map_url": "https://sh.ke.com/map/", "district_paths": {}},
    "北京": {"city_id": "110000", "city_code": "bj",  "map_url": "https://bj.ke.com/map/", "district_paths": {}},
    "广州": {
        "city_id": "440100",
        "city_code": "gz",
        "map_url": "https://gz.ke.com/map/",
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
    "深圳": {"city_id": "440300", "city_code": "sz",  "map_url": "https://sz.ke.com/map/", "district_paths": {}},
}

BEIKE_MAP_API = "https://map.ke.com/proxyApi/i.o.selectByHouseStatus/json"


# =============================================================================
# 核心类：贝壳地图爬虫
# =============================================================================

class BeikeMapScraper:
    """
    通过拦截贝壳地图页面的 Ajax 请求，批量获取小区均价

    使用方法：
        scraper = BeikeMapScraper(city="广州")
        df = await scraper.scrape_district(district="海珠区")
    """

    def __init__(self, city: str = "广州"):
        if city not in CITY_CONFIG:
            raise ValueError(f"不支持的城市，可选：{list(CITY_CONFIG.keys())}")
        self.city = city
        self.config = CITY_CONFIG[city]
        self.captured_data = []

    async def scrape_district(self, district: str, max_retries: int = 3) -> pd.DataFrame:
        wait_for_peak_hour()

        for attempt in range(1, max_retries + 1):
            try:
                print(f"[贝壳] 开始爬取 {self.city} - {district}（第{attempt}次尝试）")
                result = await self._do_scrape(district)
                if result is not None and len(result) > 0:
                    print(f"[贝壳] 成功获取 {len(result)} 个小区数据")
                    return result
            except Exception as e:
                print(f"[贝壳] 第{attempt}次失败: {e}")
                if attempt < max_retries:
                    wait_time = 30 * (2 ** attempt)
                    print(f"[贝壳] {wait_time}秒后重试...")
                    await asyncio.sleep(wait_time)

        print(f"[贝壳] {max_retries}次重试后仍失败，返回空数据")
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

                print("[贝壳] 二手房列表页未采到数据，尝试地图接口...")
                await page.route(
                    "**/proxyApi/i.o.selectByHouseStatus/**",
                    self._intercept_api_response
                )
                await page.route(
                    "**/map/ershoufang/**",
                    self._intercept_listing_response
                )

                district_path = self._district_path(district)
                map_url = f"{self.config['map_url']}ershoufang/{district_path}/"
                success = await engine.goto(page, map_url)
                if not success:
                    return None

                if not await self._wait_for_map_ready(page, map_url):
                    return await self._fallback_list_scrape(page, engine, district)

                await async_human_delay(2.0, 4.0)
                await self._simulate_map_interaction(page)
                await async_human_delay(3.0, 5.0)

                if not self.captured_data:
                    print("[贝壳] 没有拦截到 API 响应，尝试小区列表页解析...")
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
                "[贝壳] 未找到地图界面，可能是区划路径、登录态或反爬页导致。\n"
                f"       目标URL: {expected_url}\n"
                f"       当前URL: {page.url}\n"
                f"       页面标题: {title}\n"
                f"       页面文本: {body_text}"
            )
            return False

    async def _fallback_list_scrape(
        self,
        page,
        engine,
        district: str,
        pages: int = 5,
    ) -> Optional[pd.DataFrame]:
        """采集用户可正常打开的二手房搜索列表页。"""
        records = []
        keyword = quote(district, safe="")

        for page_num in range(1, pages + 1):
            page_part = "" if page_num == 1 else f"pg{page_num}"
            list_url = f"https://{self.config['city_code']}.ke.com/ershoufang/{page_part}rs{keyword}/"
            print(f"[贝壳] 二手房列表页 {page_num}/{pages}: {list_url}")

            success = await engine.goto(page, list_url)
            if not success:
                continue

            try:
                await page.wait_for_selector(".sellListContent li, li.clear.LOGCLICKDATA", timeout=20_000)
            except Exception:
                title = await page.title()
                print(f"[贝壳] 二手房列表页未找到房源卡片: {page.url} | {title}")
                continue

            await human_scroll(page, scroll_times=3)
            items = await page.query_selector_all(".sellListContent li, li.clear.LOGCLICKDATA")

            for el in items:
                try:
                    data = await el.evaluate("""
                        (el) => {
                            const titleLink = el.querySelector('.title a');
                            const communityLink = el.querySelector('.positionInfo a[href*="/xiaoqu/"]');
                            const houseInfoEl = el.querySelector('.houseInfo');
                            const totalPriceEl = el.querySelector('.totalPrice span');
                            const unitPriceEl = el.querySelector('.unitPrice span, .unitPrice');
                            return {
                                name: communityLink?.textContent || titleLink?.textContent || '',
                                house_title: titleLink?.textContent || '',
                                href: titleLink?.href || '',
                                community_href: communityLink?.href || '',
                                house_info: houseInfoEl?.textContent || '',
                                total_price_text: totalPriceEl?.textContent || '',
                                unit_price_text: unitPriceEl?.textContent || '',
                            };
                        }
                    """)

                    unit_price = self._extract_unit_price(data.get("unit_price_text", ""))
                    total_price = self._extract_total_price(data.get("total_price_text", ""))
                    area = self._extract_area(data.get("house_info", ""))

                    if unit_price <= 0:
                        if total_price > 0 and area > 0:
                            unit_price = total_price / area
                        else:
                            continue

                    id_match = re.search(r"/xiaoqu/([^/]+)/?", data.get("community_href", ""))
                    if not id_match:
                        id_match = re.search(r"/ershoufang/([^/]+)\.html", data.get("href", ""))

                    name = data.get("name", "").strip()
                    if not name:
                        continue
                    community_id = id_match.group(1) if id_match else name

                    records.append({
                        "community_id":  community_id,
                        "name":          name,
                        "house_title":   data.get("house_title", "").strip(),
                        "lng":           None,
                        "lat":           None,
                        "area":          area or None,
                        "total_price":   total_price or None,
                        "unit_price":    round(unit_price, 0),
                        "listing_count": 1,
                        "source":        "贝壳二手房列表页",
                        "scraped_at":    pd.Timestamp.now().isoformat(),
                    })

                except Exception:
                    continue

            await async_human_delay(1.5, 3.0)

        if not records:
            return None

        df = pd.DataFrame(records)
        df = df[df["unit_price"].notna() & (df["unit_price"] > 0)]
        df = df.drop_duplicates(subset=["community_id", "house_title", "unit_price"])
        df = df.sort_values("unit_price", ascending=False).reset_index(drop=True)
        return df

    def _extract_unit_price(self, text: str) -> float:
        match = re.search(r"(\d[\d,]*)", text or "")
        return float(match.group(1).replace(",", "")) if match else 0.0

    def _extract_total_price(self, text: str) -> float:
        match = re.search(r"(\d+\.?\d*)", text or "")
        return float(match.group(1)) * 10000 if match else 0.0

    def _extract_area(self, text: str) -> float:
        match = re.search(r"(\d+\.?\d*)\s*平", text or "")
        return float(match.group(1)) if match else 0.0

    async def _intercept_api_response(self, route, request):
        response = await route.fetch()

        try:
            body = await response.body()
            data = json.loads(body.decode("utf-8"))

            if data.get("errno") == 0 and "data" in data:
                map_points = data["data"].get("mapPoints", [])
                if map_points:
                    print(f"[拦截] 捕获到 {len(map_points)} 个小区数据点")
                    self.captured_data.extend(map_points)

        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"[拦截] 处理响应出错: {e}")
        finally:
            await route.fulfill(response=response)

    async def _intercept_listing_response(self, route, request):
        await route.continue_()

    async def _simulate_map_interaction(self, page):
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

        directions = [(100, 0), (0, 100), (-100, 0), (0, -100)]
        for dx, dy in random.sample(directions, 2):
            await page.mouse.move(center_x, center_y)
            await page.mouse.down()
            await page.mouse.move(center_x + dx, center_y + dy, steps=10)
            await page.mouse.up()
            await async_human_delay(1.5, 3.0)

    def _process_captured_data(self) -> pd.DataFrame:
        records = []
        seen_ids = set()

        for point in self.captured_data:
            community_id = point.get("id", "")
            if community_id in seen_ids:
                continue
            seen_ids.add(community_id)

            records.append({
                "community_id":  community_id,
                "name":          point.get("name", ""),
                "lng":           point.get("lng"),
                "lat":           point.get("lat"),
                "unit_price":    point.get("unitPrice"),
                "listing_count": point.get("totalCount", 0),
                "source":        "贝壳地图",
                "scraped_at":    pd.Timestamp.now().isoformat(),
            })

        df = pd.DataFrame(records)
        df = df[df["unit_price"].notna() & (df["unit_price"] > 0)]
        df = df.sort_values("unit_price", ascending=False).reset_index(drop=True)

        return df


# =============================================================================
# 多区域批量采集入口
# =============================================================================

async def batch_scrape_city(city: str, districts: list[str]) -> pd.DataFrame:
    scraper = BeikeMapScraper(city=city)
    all_dfs = []

    for i, district in enumerate(districts):
        print(f"\n[进度] {i+1}/{len(districts)}：{district}")

        df = await scraper.scrape_district(district)
        if not df.empty:
            df["district"] = district
            all_dfs.append(df)

        if i < len(districts) - 1:
            wait = random.uniform(30, 90)
            print(f"[调度] 等待 {wait:.0f} 秒后采集下一个区域...")
            await asyncio.sleep(wait)

    if not all_dfs:
        return pd.DataFrame()

    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.drop_duplicates(subset=["community_id"])

    print(f"\n[完成] 共采集 {len(combined)} 个小区，覆盖 {len(districts)} 个区域")
    return combined


if __name__ == "__main__":
    async def main():
        df = await batch_scrape_city(city="广州", districts=["海珠区"])
        if not df.empty:
            print(df.head(10).to_string())
            df.to_csv("beike_prices.csv", index=False, encoding="utf-8-sig")

    asyncio.run(main())
