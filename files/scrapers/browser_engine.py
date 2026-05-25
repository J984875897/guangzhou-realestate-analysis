# =============================================================================
# scrapers/browser_engine.py
# Playwright 浏览器引擎 —— 反爬核心模块
# =============================================================================

import asyncio
import json
import random
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import (
    async_playwright,
    Browser,
    Page,
    BrowserContext,
)


# =============================================================================
# 代理 IP 池
# =============================================================================
PROXY_POOL = [
    {"server": "http://proxy1.example.com:8080", "username": "user1", "password": "pass1"},
    {"server": "http://proxy2.example.com:8080", "username": "user2", "password": "pass2"},
    {"server": "http://proxy3.example.com:8080", "username": "user3", "password": "pass3"},
]


def get_random_proxy() -> dict:
    return random.choice(PROXY_POOL)


# =============================================================================
# 非高峰时段检测
# =============================================================================

ALLOWED_HOURS = range(9, 22)

def is_peak_hour() -> bool:
    return datetime.now().hour in ALLOWED_HOURS

def wait_for_peak_hour():
    while not is_peak_hour():
        current = datetime.now()
        next_start = current.replace(hour=9, minute=0, second=0)
        if current.hour >= 22:
            next_start = next_start.replace(day=current.day + 1)
        wait_seconds = (next_start - current).seconds
        print(f"[调度] 当前 {current.strftime('%H:%M')}，非活跃时段，等待 {wait_seconds//60} 分钟后开始...")
        time.sleep(wait_seconds)


# =============================================================================
# Cookie 持久化
# =============================================================================

COOKIES_FILE = Path(__file__).parent / "beike_session.json"


async def save_session_cookies(context: BrowserContext, path: Path = COOKIES_FILE) -> None:
    cookies = await context.cookies()
    path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2))


async def load_session_cookies(context: BrowserContext, path: Path = COOKIES_FILE) -> bool:
    if not path.exists():
        return False
    try:
        cookies = json.loads(path.read_text())
        await context.add_cookies(cookies)
        return True
    except Exception:
        return False


def _is_session_valid(path: Path = COOKIES_FILE) -> bool:
    """检查 lianjia_ssid 等关键会话 Cookie 是否仍在有效期内。"""
    if not path.exists():
        return False
    try:
        cookies = json.loads(path.read_text())
        now = time.time()
        # lianjia_ssid 是贝壳页面鉴权的核心短效 session，30 min TTL
        for c in cookies:
            if c.get("name") == "lianjia_ssid":
                exp = c.get("expires", -1)
                if exp < 0:
                    return True   # 会话级 cookie，不判断有效期
                return exp > now
        # 文件存在但没有 lianjia_ssid 时，也认为失效
        return False
    except Exception:
        return False


async def ensure_beike_login() -> None:
    """
    打开可见浏览器，等待用户手动登录贝壳后保存 Cookie。
    Cookie 文件存在且 lianjia_ssid 未过期则跳过；否则强制重新登录。
    """
    if _is_session_valid():
        print("[登录] 检测到有效的贝壳 Cookie，跳过登录")
        return

    if COOKIES_FILE.exists():
        print("[登录] 上次保存的 Cookie 已过期，需要重新登录...")
        COOKIES_FILE.unlink()

    print("[登录] 即将打开浏览器窗口，请完成登录后回到此终端按 Enter 继续")
    print("[登录] 注意：请只在登录完成、页面跳回搜索结果后再按 Enter，不要在登录页按")

    async with BrowserEngine(headless=False) as engine:
        context = await create_stealth_context(engine._browser, use_proxy=False)
        page = await context.new_page()
        # 从首页进入更自然，降低被反爬识别的概率
        await page.goto("https://gz.ke.com/", wait_until="domcontentloaded", timeout=30_000)
        await async_human_delay(1.5, 2.5)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input, "")

        await save_session_cookies(context)
        await context.close()

    print(f"[登录] Cookie 已保存至 {COOKIES_FILE}")


# =============================================================================
# 随机延迟函数
# =============================================================================

def human_delay(min_sec: float = 2.0, max_sec: float = 8.0):
    time.sleep(random.uniform(min_sec, max_sec))

async def async_human_delay(min_sec: float = 2.0, max_sec: float = 8.0):
    await asyncio.sleep(random.uniform(min_sec, max_sec))


# =============================================================================
# 浏览器上下文工厂
# =============================================================================

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1280, "height": 800},
]

async def create_stealth_context(browser: Browser, use_proxy: bool = True) -> BrowserContext:
    proxy = get_random_proxy() if use_proxy else None

    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport=random.choice(VIEWPORTS),
        proxy=proxy,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        accept_downloads=True,
        # 广州市中心坐标（百度坐标系）
        geolocation={"latitude": 23.1291, "longitude": 113.2644},
        permissions=["geolocation"],
    )

    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en']
        });
        window.chrome = {
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };
    """)

    return context


# =============================================================================
# 页面操作辅助函数
# =============================================================================

async def human_scroll(page: Page, scroll_times: int = 3):
    for _ in range(scroll_times):
        scroll_distance = random.randint(300, 800)
        await page.mouse.wheel(0, scroll_distance)
        await asyncio.sleep(random.uniform(0.5, 2.0))


async def human_mouse_move(page: Page):
    width = page.viewport_size["width"]
    height = page.viewport_size["height"]

    for _ in range(random.randint(2, 5)):
        x = random.randint(100, width - 100)
        y = random.randint(100, height - 100)
        await page.mouse.move(x, y, steps=random.randint(5, 15))
        await asyncio.sleep(random.uniform(0.1, 0.5))


# =============================================================================
# 主浏览器引擎类
# =============================================================================

class BrowserEngine:
    def __init__(self, headless: bool = True, use_proxy: bool = False):
        self.headless = headless
        self.use_proxy = use_proxy
        self._playwright = None
        self._browser = None

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1920,1080",
            ]
        )
        return self

    async def __aexit__(self, *args):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def new_page(self) -> tuple[BrowserContext, Page]:
        context = await create_stealth_context(self._browser, self.use_proxy)
        page = await context.new_page()
        page.set_default_timeout(60_000)
        return context, page

    async def goto(self, page: Page, url: str) -> bool:
        try:
            await page.goto(url, wait_until="networkidle", timeout=60_000)
            await async_human_delay(1.5, 4.0)
            await human_mouse_move(page)
            return True
        except Exception as e:
            print(f"[错误] 访问 {url} 失败: {e}")
            return False
