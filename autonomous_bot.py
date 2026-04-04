import asyncio
import os
import random
import time
from playwright.async_api import async_playwright, BrowserContext, Page
from playwright_stealth import Stealth

class DeepSeekConnector:
    """深度优化版 DeepSeek 网页版自动登录与问答连接器"""

    def __init__(self, username: str = None, password: str = None, headless: bool = False):
        self.username = username or os.getenv("DEEPSEEK_USERNAME")
        self.password = password or os.getenv("DEEPSEEK_PASSWORD")
        if not self.username or not self.password:
            raise ValueError("请提供 DeepSeek 账号密码，或设置环境变量 DEEPSEEK_USERNAME 和 DEEPSEEK_PASSWORD")
        self.headless = headless
        self._playwright = None
        self._context: BrowserContext = None
        self._page: Page = None
        self._logged_in = False
        self._user_data_dir = os.getenv("DEEPSEEK_PROFILE_DIR", "./deepseek_profile")

    async def start(self):
        """使用持久化上下文启动浏览器，隐藏自动化指纹"""
        self._playwright = await async_playwright().start()
        # 关键优化：使用持久化上下文 + 真实浏览器参数
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._user_data_dir,
            headless=self.headless,
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process',
                '--no-sandbox',
                '--disable-setuid-sandbox'
            ]
        )
        self._page = await self._context.new_page()
        await self._login()

    async def _login(self):
        """执行或复用登录流程，应用反检测隐身技术"""
        # 访问首页
        await self._page.goto("https://chat.deepseek.com/")
        await self._page.wait_for_load_state("networkidle")
        # 模拟人类首次访问的随机停留
        await asyncio.sleep(random.uniform(1.5, 3.5))

        # 检查是否已经登录（通过 URL 或页面元素）
        if "sign_in" in self._page.url:
            print("🔐 未检测到登录状态，开始自动登录...")
            # 应用 playwright_stealth 隐藏自动化特征
            await Stealth().apply_stealth_async(self._page)

            # 等待登录表单出现
            await self._page.wait_for_selector("input[placeholder*='手机号/邮箱'], input[type='email']", timeout=15000)
            # 模拟人类输入账号
            await self._page.fill("input[placeholder*='手机号/邮箱'], input[type='email']", self.username)
            await asyncio.sleep(random.uniform(0.3, 0.8))
            # 模拟人类输入密码
            await self._page.fill("input[type='password']", self.password)
            await asyncio.sleep(random.uniform(0.3, 0.8))
            # 点击登录按钮
            await self._page.click("button:has-text('登录')")
            # 等待登录成功跳转
            try:
                await self._page.wait_for_url("**/chat*", timeout=60000)
                print("✅ 登录成功，状态已持久化。")
            except:
                # 如果跳转失败，检查是否有验证码等
                print("⚠️ 登录可能失败，请检查账号或是否存在验证码")
                raise
        else:
            print("✅ 检测到有效登录状态，直接使用。")

        # 等待聊天界面完全加载
        await self._page.wait_for_selector("textarea[placeholder*='提问']", timeout=30000)
        self._logged_in = True

    async def set_deep_think(self, enable: bool = False):
        """设置深度思考开关（默认关闭）"""
        try:
            # 深度思考按钮可能通过 role 或文本定位
            btn = await self._page.query_selector("button[aria-label*='深度思考'], text=深度思考")
            if btn:
                is_active = await btn.get_attribute("aria-checked")
                if (enable and is_active != "true") or (not enable and is_active == "true"):
                    await btn.click()
                    print(f"🔘 深度思考已{'开启' if enable else '关闭'}")
        except Exception as e:
            print(f"⚠️ 设置深度思考失败: {e}")

    async def set_web_search(self, enable: bool = False):
        """设置联网搜索开关（默认关闭）"""
        try:
            btn = await self._page.query_selector("button[aria-label*='联网搜索'], text=联网搜索")
            if btn:
                is_active = await btn.get_attribute("aria-checked")
                if (enable and is_active != "true") or (not enable and is_active == "true"):
                    await btn.click()
                    print(f"🔘 联网搜索已{'开启' if enable else '关闭'}")
        except Exception as e:
            print(f"⚠️ 设置联网搜索失败: {e}")

    async def new_conversation(self):
        """新建对话，清空上下文"""
        try:
            # 新建对话按钮
            await self._page.click("button:has-text('新建对话')", timeout=10000)
            await asyncio.sleep(1)
            print("🔄 已新建对话")
        except Exception as e:
            print(f"⚠️ 新建对话失败（可能已在新建状态）: {e}")

    async def ask(self, question: str, max_wait: int = 180) -> str:
        """发送问题并等待回答完成"""
        if not self._logged_in:
            raise RuntimeError("未登录，请先调用 start()")
        # 定位输入框
        input_area = await self._page.wait_for_selector("textarea[placeholder*='提问']", timeout=15000)
        # 模拟人类输入，加入随机延迟
        await input_area.fill(question)
        await asyncio.sleep(random.uniform(0.2, 0.5))
        await input_area.press("Enter")
        print(f"📤 已发送问题: {question[:50]}...")

        # 等待回答开始生成（出现“停止生成”按钮）
        try:
            await self._page.wait_for_selector("button:has-text('停止生成')", timeout=15000)
        except:
            pass
        # 等待回答完成（“停止生成”按钮消失）
        try:
            await self._page.wait_for_selector("button:has-text('停止生成')", state="hidden", timeout=max_wait * 1000)
        except:
            pass
        # 获取最后一个助手的回答
        assistant_messages = await self._page.query_selector_all("div[class*='message'][class*='assistant']")
        if assistant_messages:
            last = assistant_messages[-1]
            answer = await last.inner_text()
            return answer.strip()
        return "未获取到回答"

    async def close(self):
        """关闭浏览器上下文"""
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
