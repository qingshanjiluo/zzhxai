import asyncio
import os
from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PlaywrightTimeoutError

class DeepSeekConnector:
    """DeepSeek 网页版自动登录与问答（优化版，增强超时处理）"""

    def __init__(self, username: str = None, password: str = None, headless: bool = False):
        self.username = username or os.getenv("DEEPSEEK_USERNAME")
        self.password = password or os.getenv("DEEPSEEK_PASSWORD")
        if not self.username or not self.password:
            raise ValueError("请提供 DeepSeek 账号密码，或设置环境变量 DEEPSEEK_USERNAME 和 DEEPSEEK_PASSWORD")
        self.headless = headless
        self._playwright = None
        self._browser: Browser = None
        self._page: Page = None
        self._logged_in = False

    async def start(self):
        """启动浏览器并自动登录"""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless, args=['--no-sandbox'])
        self._page = await self._browser.new_page()
        await self._login()

    async def _login(self):
        print("🔐 正在打开登录页面...")
        try:
            await self._page.goto("https://platform.deepseek.com/sign_in", timeout=60000)
            await self._page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            print("⚠️ 页面加载超时，请检查网络连接")
            raise

        # 尝试点击“密码登录”按钮（若存在）
        try:
            password_login_btn = await self._page.query_selector("text=密码登录")
            if password_login_btn and await password_login_btn.is_visible():
                await password_login_btn.click()
                print("👉 已点击“密码登录”")
                await self._page.wait_for_timeout(1000)
        except Exception as e:
            print(f"点击密码登录按钮失败（可忽略）: {e}")

        # 等待账号输入框出现（增加超时时间）
        try:
            await self._page.wait_for_selector("input[placeholder*='手机号/邮箱']", timeout=30000)
        except PlaywrightTimeoutError:
            print("❌ 未找到账号输入框，登录页面可能已变更")
            raise

        await self._page.fill("input[placeholder*='手机号/邮箱']", self.username)
        await self._page.fill("input[type='password']", self.password)
        await self._page.click("button:has-text('登录')")
        print("⏳ 提交登录，等待跳转...")

        # 等待登录完成（URL 变为 /chat 或出现输入框）
        try:
            await self._page.wait_for_url("**/chat*", timeout=60000)
        except PlaywrightTimeoutError:
            # 若 URL 未变，则等待聊天输入框
            try:
                await self._page.wait_for_selector("textarea[placeholder*='提问']", timeout=30000)
            except PlaywrightTimeoutError:
                print("❌ 登录后未进入聊天页面，请检查账号密码是否正确或是否有验证码")
                raise

        await self._page.wait_for_load_state("networkidle", timeout=15000)
        print("✅ 登录成功，进入聊天页面")

        # 等待聊天输入框完全加载
        await self._page.wait_for_selector("textarea[placeholder*='提问']", timeout=30000)
        self._logged_in = True

    async def set_deep_think(self, enable: bool = False):
        try:
            btn = await self._page.query_selector("text=深度思考")
            if btn:
                is_active = await btn.get_attribute("aria-checked")
                if (enable and is_active != "true") or (not enable and is_active == "true"):
                    await btn.click()
                    print(f"🔘 深度思考已{'开启' if enable else '关闭'}")
        except Exception as e:
            print(f"⚠️ 设置深度思考失败: {e}")

    async def set_web_search(self, enable: bool = False):
        try:
            btn = await self._page.query_selector("text=联网搜索")
            if btn:
                is_active = await btn.get_attribute("aria-checked")
                if (enable and is_active != "true") or (not enable and is_active == "true"):
                    await btn.click()
                    print(f"🔘 联网搜索已{'开启' if enable else '关闭'}")
        except Exception as e:
            print(f"⚠️ 设置联网搜索失败: {e}")

    async def new_conversation(self):
        try:
            await self._page.click("button:has-text('新建对话')", timeout=10000)
            await self._page.wait_for_timeout(1000)
            print("🔄 已新建对话")
        except Exception as e:
            print(f"⚠️ 新建对话失败（可能已在新建状态）: {e}")

    async def ask(self, question: str, max_wait: int = 180) -> str:
        if not self._logged_in:
            raise RuntimeError("未登录，请先调用 start()")
        input_area = await self._page.wait_for_selector("textarea[placeholder*='提问']", timeout=30000)
        await input_area.fill(question)
        await input_area.press("Enter")
        print(f"📤 已发送问题: {question[:50]}...")
        try:
            await self._page.wait_for_selector("button:has-text('停止生成')", timeout=15000)
        except:
            pass
        try:
            await self._page.wait_for_selector("button:has-text('停止生成')", state="hidden", timeout=max_wait * 1000)
        except:
            pass
        assistant_messages = await self._page.query_selector_all("div[class*='message'][class*='assistant']")
        if assistant_messages:
            last = assistant_messages[-1]
            answer = await last.inner_text()
            return answer.strip()
        return "未获取到回答"

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
