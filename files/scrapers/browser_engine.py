# =============================================================================
# scrapers/browser_engine.py
# Playwright 浏览器引擎 —— 浏览器复用与访问状态检测
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


class BeikeAccessError(RuntimeError):
    """贝壳页面进入登录/验证码/不可用状态时抛出，避免无意义重试。"""


async def detect_beike_page_state(page: Page) -> str:
    """返回 normal / captcha / login / not_found / blocked，用于温和停止采集。"""
    url = page.url
    if "hip.ke.com/captcha" in url or "captcha" in url:
        return "captcha"
    if "login" in url or "passport" in url:
        return "login"

    try:
        title = await page.title()
    except Exception:
        title = ""

    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text(timeout=2_000)).strip()
    except Exception:
        pass

    page_text = f"{title}\n{body_text}"
    if "未找到页面" in page_text or "网址失效" in page_text:
        return "not_found"
    if "访问过于频繁" in page_text or "安全验证" in page_text or "异常访问" in page_text:
        return "blocked"
    if "登录" in page_text and ("验证码" in page_text or "手机号" in page_text):
        return "login"
    return "normal"


async def raise_for_beike_access_issue(page: Page, url: str) -> None:
    state = await detect_beike_page_state(page)
    if state == "normal":
        return

    if state in ("captcha", "login"):
        print(f"\n[贝壳] 检测到 {state} 页面，请在弹出的浏览器窗口中手动处理（完成验证码或手机号登录）")
        print("[贝壳] 处理完成后回到终端，按 Enter 继续采集...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input, "")
        state = await detect_beike_page_state(page)
        if state == "normal":
            print("[贝壳] 已恢复正常，继续采集\n")
            return

    raise BeikeAccessError(f"贝壳页面状态为 {state}，停止本次采集: {url} -> {page.url}")


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
# Cookie 持久化
# =============================================================================

COOKIES_FILE    = Path(__file__).parent / "beike_session.json"
USER_DATA_DIR   = Path(__file__).parent / "browser_profile"   # 持久化浏览器 Profile


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

    使用持久化 Profile（USER_DATA_DIR）启动，浏览器保留完整历史和本地存储，
    避免贝壳检测到无状态自动化浏览器后不断刷新/重置登录表单。
    Cookie 文件存在且 lianjia_ssid 未过期则跳过；否则强制重新登录。
    """
    if _is_session_valid():
        print("[登录] 检测到有效的贝壳 Cookie，跳过登录")
        return

    if COOKIES_FILE.exists():
        print("[登录] 上次保存的 Cookie 已过期，需要重新登录...")
        COOKIES_FILE.unlink()

    print("[登录] 即将打开浏览器窗口，请完成登录...")
    print("[登录] 提示：输入手机号 → 验证码 → 登录完成页面跳回首页后，再回到终端按 Enter")

    USER_DATA_DIR.mkdir(exist_ok=True)

    async with async_playwright() as p:
        # launch_persistent_context 保留浏览器 Profile，减少反爬触发
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = await context.new_page()
        await page.goto("https://gz.ke.com/", wait_until="load", timeout=60_000)
        # 等待 JS 完全执行、页面状态稳定
        await asyncio.sleep(3)

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
