# =============================================================================
# scrapers/beike_map_scraper.py
# 贝壳二手房公开列表页批量采集房价
#
# 核心思路：
#   使用用户可直接访问的公开二手房列表页，避免依赖不稳定的地图内部接口。
#   采集过程中遇到验证码/登录/访问受限页面时温和停止，并优先返回断点缓存。
# =============================================================================

import asyncio
import json
import random
import re
from typing import Optional
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from scrapers.browser_engine import (
    BrowserEngine, async_human_delay, human_scroll,
    load_session_cookies, save_session_cookies,
    BeikeAccessError, raise_for_beike_access_issue,
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
CHECKPOINT_DIR = Path(__file__).parents[2] / "output" / "checkpoints"


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

    async def scrape_district(
        self, district: str, max_retries: int = 3,
        min_pages: int = 5, min_records: int = 150, max_pages: int = 100,
    ) -> pd.DataFrame:
        for attempt in range(1, max_retries + 1):
            try:
                print(f"[贝壳] 开始爬取 {self.city} - {district}（第{attempt}次尝试）")
                result = await self._do_scrape(
                    district, min_pages=min_pages, min_records=min_records, max_pages=max_pages,
                )
                if result is not None and len(result) > 0:
                    print(f"[贝壳] 成功获取 {len(result)} 个小区数据")
                    return result
            except BeikeAccessError as e:
                print(f"[贝壳] {e}")
                print("[贝壳] !! 被验证码/登录页拦截，建议重新运行并加 --login 参数完成贝壳账号登录")
                return pd.DataFrame()
            except Exception as e:
                print(f"[贝壳] 第{attempt}次失败: {e}")
                if attempt < max_retries:
                    wait_time = 30 * (2 ** attempt)
                    print(f"[贝壳] {wait_time}秒后重试...")
                    await asyncio.sleep(wait_time)

        print(f"[贝壳] {max_retries}次重试后仍失败，返回空数据")
        return pd.DataFrame()

    async def _do_scrape(
        self, district: str,
        min_pages: int = 5, min_records: int = 150, max_pages: int = 100,
    ) -> Optional[pd.DataFrame]:
        self.captured_data = []

        async with BrowserEngine(headless=False, use_proxy=False) as engine:
            context, page = await engine.new_page()
            await load_session_cookies(context)

            try:
                return await self._fallback_list_scrape(
                    page, engine, district,
                    min_pages=min_pages, min_records=min_records, max_pages=max_pages,
                )

            finally:
                await save_session_cookies(context)
                await context.close()

    def _district_path(self, district: str) -> str:
        """贝壳 URL 使用拼音区划路径；未知区划时退回 URL 编码。"""
        district_paths = self.config.get("district_paths", {})
        return district_paths.get(district, quote(district, safe=""))

    def _checkpoint_path(self, district: str) -> Path:
        safe_name = f"beike_prices_{self.city}_{district}.csv".replace("/", "_")
        return CHECKPOINT_DIR / safe_name

    def _load_checkpoint(self, district: str) -> pd.DataFrame:
        path = self._checkpoint_path(district)
        if not path.exists():
            return pd.DataFrame()
        try:
            df = pd.read_csv(path)
            if not df.empty:
                print(f"[贝壳] 发现断点缓存: {path.name} ({len(df)} 条)")
            return df
        except Exception as e:
            print(f"[贝壳] 断点缓存读取失败: {path.name} | {e}")
            return pd.DataFrame()

    def _save_checkpoint(self, df: pd.DataFrame, district: str) -> None:
        if df.empty:
            return
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        path = self._checkpoint_path(district)
        df.to_csv(path, index=False, encoding="utf-8-sig")

    def _records_to_dataframe(self, records: list[dict]) -> pd.DataFrame:
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records)
        df = df[df["unit_price"].notna() & (df["unit_price"] > 0)]
        df = df.drop_duplicates(subset=["community_id", "house_title", "unit_price"])
        df = df.sort_values("unit_price", ascending=False).reset_index(drop=True)
        return df

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
        min_pages: int = 5,
        min_records: int = 150,
        max_pages: int = 100,
    ) -> Optional[pd.DataFrame]:
        """采集用户可正常打开的二手房搜索列表页。"""
        checkpoint_df = self._load_checkpoint(district)
        records = checkpoint_df.to_dict("records") if not checkpoint_df.empty else []
        keyword = quote(district, safe="")

        page_num = 1
        while page_num <= max_pages:
            page_part = "" if page_num == 1 else f"pg{page_num}"
            list_url = f"https://{self.config['city_code']}.ke.com/ershoufang/{page_part}rs{keyword}/"
            print(f"[贝壳] 二手房列表页 {page_num} (已采集 {len(records)} 条): {list_url}")

            success = await engine.goto(page, list_url)
            if not success:
                continue
            try:
                await raise_for_beike_access_issue(page, list_url)
            except BeikeAccessError:
                if records:
                    print("[贝壳] 页面受限，返回已保存的断点缓存")
                    return self._records_to_dataframe(records)
                raise

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

            current_df = self._records_to_dataframe(records)
            self._save_checkpoint(current_df, district)
            await async_human_delay(1.5, 3.0)

            if page_num >= min_pages and len(records) >= min_records:
                print(f"[贝壳] 已满足停止条件（{page_num} 页 / {len(records)} 条），停止采集")
                break
            page_num += 1

        if not records:
            return None

        df = self._records_to_dataframe(records)
        self._save_checkpoint(df, district)
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

async def batch_scrape_city(
    city: str, districts: list[str],
    min_pages: int = 5, min_records: int = 150, max_pages: int = 100,
) -> pd.DataFrame:
    scraper = BeikeMapScraper(city=city)
    all_dfs = []

    for i, district in enumerate(districts):
        print(f"\n[进度] {i+1}/{len(districts)}：{district}")

        df = await scraper.scrape_district(
            district, min_pages=min_pages, min_records=min_records, max_pages=max_pages,
        )
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
