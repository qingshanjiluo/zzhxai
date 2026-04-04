import asyncio
import os
import json
import time
import re
import random
import traceback
from datetime import datetime, date
from login import BBSTurkeyBotLogin
from post import BBSPoster
from deepseek_connector import DeepSeekConnector

class AutonomousBot:
    def __init__(self):
        # 论坛配置
        self.base_url = os.getenv("BASE_URL", "https://mbbs.zdjl.site/mk48by049.mbbs.cc")
        self.username = os.getenv("BOT_USERNAME")
        self.password = os.getenv("BOT_PASSWORD")
        if not self.username or not self.password:
            raise ValueError("请设置 BOT_USERNAME 和 BOT_PASSWORD 环境变量")

        # 监听板块
        target_categories_str = os.getenv("TARGET_CATEGORIES", "2,5")
        self.target_categories = [int(x) for x in target_categories_str.split(",") if x.strip()]

        # 跳过每个板块最新 N 个帖子
        skip_latest_str = os.getenv("SKIP_LATEST", "5")
        self.skip_latest = int(skip_latest_str) if skip_latest_str else 5

        # 登录重试次数
        login_retries_str = os.getenv("LOGIN_RETRIES", "50")
        self.login_retries = int(login_retries_str) if login_retries_str else 50

        # 管理员删帖 token（可选）
        self.admin_mk49_token = os.getenv("ADMIN_MK49_TOKEN")

        # 黑名单用户ID
        blacklist_str = os.getenv("BLACKLIST_USER_IDS", "")
        self.blacklist = [int(x) for x in blacklist_str.split(",") if x.strip()]

        # 加载风格文档和背景知识
        self.style = self._load_file("style.txt", "你是一个论坛用户，回复风格幽默风趣。")
        self.background = self._load_file("mk48.txt", "")

        # 状态持久化
        self.state_file = "state.json"
        self.state = self._load_state()

        # 操作配额（可由环境变量配置）
        max_reply_threads_str = os.getenv("MAX_REPLY_THREADS", "15")
        self.max_reply_threads = int(max_reply_threads_str) if max_reply_threads_str else 15
        max_create_threads_str = os.getenv("MAX_CREATE_THREADS", "1")
        self.max_create_threads = int(max_create_threads_str) if max_create_threads_str else 1

        # 每日发帖上限（仅针对新帖子）
        daily_limit_str = os.getenv("DAILY_POST_LIMIT", "10")
        self.daily_post_limit = int(daily_limit_str) if daily_limit_str else 10

        # 操作计数
        self.reply_threads_count = 0
        self.create_threads_count = 0
        self.today_posts_count = 0

        # DeepSeek 网页版连接器
        self.ds = None

        # 登录后的凭证
        self.token = None
        self.user_id = None
        self.session = None
        self.poster = None

    def _load_file(self, filename, default):
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return f.read()
        return default

    def _load_state(self):
        default = {
            "processed_threads": [],
            "action_logs": [],
            "daily_stats": {},
            "last_run": None
        }
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            for key, value in default.items():
                if key not in state:
                    state[key] = value
            if "processed_threads" in state:
                state["processed_threads"] = [int(x) for x in state["processed_threads"]]
            return state
        return default

    def _save_state(self):
        self.state["last_run"] = datetime.now().isoformat()
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def _log_action(self, action_type, target, content, success):
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": action_type,
            "target": target,
            "content": content[:100] if content else "",
            "success": success
        }
        self.state["action_logs"].append(log_entry)
        if len(self.state["action_logs"]) > 100:
            self.state["action_logs"] = self.state["action_logs"][-100:]
        self._save_state()

    def _update_daily_stats(self):
        today = date.today().isoformat()
        if today not in self.state["daily_stats"]:
            self.state["daily_stats"][today] = {"posts": 0}
        self.today_posts_count = self.state["daily_stats"][today]["posts"]

    def _increment_daily_count(self):
        today = date.today().isoformat()
        if today not in self.state["daily_stats"]:
            self.state["daily_stats"][today] = {"posts": 0}
        self.state["daily_stats"][today]["posts"] += 1
        self.today_posts_count = self.state["daily_stats"][today]["posts"]
        self._save_state()

    def login(self):
        """登录论坛并刷新权限"""
        print(f"🔐 登录账号: {self.username}")
        login_bot = BBSTurkeyBotLogin(
            base_url=self.base_url,
            username=self.username,
            password=self.password,
            max_retries=self.login_retries
        )
        success, result, session = login_bot.login_with_retry()
        if not success:
            print("❌ 登录失败")
            return False

        user_data = result.get('data', {})
        self.token = user_data.get('token')
        self.user_id = user_data.get('id')
        if not self.token or not self.user_id:
            print("❌ 登录响应缺少 token 或 user_id")
            return False

        self.session = session
        self.poster = BBSPoster(session, self.base_url)

        # 登录后等待5秒并刷新页面（获取管理员权限）
        print("⏳ 等待5秒并刷新页面以获取管理员权限...")
        time.sleep(5)
        try:
            self.session.get(self.base_url, timeout=10)
            print("✅ 页面刷新成功")
        except:
            print("⚠️ 页面刷新失败，但继续运行")

        print(f"✅ 登录成功，用户ID: {self.user_id}")
        if self.admin_mk49_token:
            print("🔑 管理员删帖功能已启用")
        return True

    async def init_deepseek(self):
        """初始化 DeepSeek 网页版连接器"""
        headless = os.getenv("DS_HEADLESS", "False").lower() == "true"
        self.ds = DeepSeekConnector(
            username=os.getenv("DEEPSEEK_USERNAME"),
            password=os.getenv("DEEPSEEK_PASSWORD"),
            headless=headless
        )
        await self.ds.start()
        # 关闭深度思考和联网搜索（可配置）
        deep_think = os.getenv("DS_DEEP_THINK", "False").lower() == "true"
        web_search = os.getenv("DS_WEB_SEARCH", "False").lower() == "true"
        await self.ds.set_deep_think(deep_think)
        await self.ds.set_web_search(web_search)
        await self.ds.new_conversation()
        print("✅ DeepSeek 网页版连接器已就绪")

    def get_new_threads(self):
        """获取未处理的新帖子，跳过黑名单和最新N个"""
        new_threads = []
        for cat_id in self.target_categories:
            threads = self.poster.get_threads(self.token, category_id=cat_id, page_limit=30)
            if not isinstance(threads, list):
                continue
            threads_to_process = threads[self.skip_latest:]
            for t in threads_to_process:
                tid = int(t.get('id'))
                author_id = int(t.get('user_id', 0))
                if tid in self.state['processed_threads']:
                    continue
                if author_id in self.blacklist:
                    print(f"  跳过黑名单用户 {author_id} 的帖子: {t['title'][:30]}")
                    continue
                new_threads.append(t)
        return new_threads

    async def decide_action(self, context, is_thread=True):
        """AI 决策，支持帖子上下文"""
        if is_thread:
            action_prompt = f"""
你是一个论坛用户，你需要根据当前帖子决定做什么。

【你的角色风格】
{self.style}

【背景知识】
{self.background}

【帖子详情】
{context}

【操作配额】（已执行次数/总限额）
- 回复帖子：{self.reply_threads_count}/{self.max_reply_threads}
- 发布新帖：{self.create_threads_count}/{self.max_create_threads}

【你可以执行的操作】
- reply_to_thread: 回复帖子（需要 thread_id 和回复内容）
- like_thread: 给帖子点赞（thread_id）
- create_thread: 发布新帖子（需要 title, content, category_id）
- set_essence: 给帖子加精（thread_id，需管理员权限）
- delete_thread: 删除帖子（thread_id，需管理员权限，仅在必要时使用）
- ignore: 不采取任何行动

【注意】你只能回复帖子本身，不能回复评论。如果配额已满，则只能 ignore。

【输出格式】
只输出一个 JSON 对象。
"""
        else:
            action_prompt = f"""
你是一个论坛用户，你需要根据当前情况决定做什么。

【你的角色风格】
{self.style}

【背景知识】
{self.background}

【当前情况】
{context}

【操作配额】（已执行次数/总限额）
- 发布新帖：{self.create_threads_count}/{self.max_create_threads}

【你可以执行的操作】
- create_thread: 发布新帖子（需要 title, content, category_id）
- ignore: 不采取任何行动

【输出格式】
只输出一个 JSON 对象。
"""
        response = await self.ds.ask(action_prompt, max_wait=120)
        try:
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            else:
                print("AI 返回非 JSON，使用默认忽略")
                return {"action": "ignore", "reason": "解析失败"}
        except Exception as e:
            print(f"解析 AI 决策失败: {e}")
            return {"action": "ignore", "reason": "解析异常"}

    def execute_action(self, decision):
        action = decision.get("action")
        if action == "ignore":
            print(f"⏭️ 忽略: {decision.get('reason', '无理由')}")
            return True

        # 配额检查
        if action == "reply_to_thread" and self.reply_threads_count >= self.max_reply_threads:
            print("⚠️ 回复帖子配额已满，跳过")
            return False
        if action == "create_thread" and self.create_threads_count >= self.max_create_threads:
            print("⚠️ 发布新帖配额已满，跳过")
            return False

        if action == "reply_to_thread":
            thread_id = decision.get("thread_id")
            content = decision.get("content")
            if not thread_id or not content:
                return False
            success = self.poster.create_comment(self.token, thread_id, content)
            if success:
                self.state["processed_threads"].append(thread_id)
                self.reply_threads_count += 1
                self._log_action("reply_to_thread", thread_id, content, True)
                self._save_state()
            else:
                self._log_action("reply_to_thread", thread_id, content, False)
            return success

        elif action == "like_thread":
            thread_id = decision.get("thread_id")
            if not thread_id:
                return False
            success = self.poster.set_thread_like(self.token, thread_id, like=True)
            self._log_action("like_thread", thread_id, "", success)
            return success

        elif action == "create_thread":
            title = decision.get("title")
            content = decision.get("content")
            category_id = decision.get("category_id", 2)
            if not title or not content:
                return False
            # 每日发帖限制
            if self.today_posts_count >= self.daily_post_limit:
                print(f"⚠️ 今日已达发帖上限 {self.daily_post_limit}，跳过创建新帖子")
                return False
            success, _ = self.poster.create_thread(self.token, category_id, title, content)
            if success:
                self.create_threads_count += 1
                self._increment_daily_count()
                self._log_action("create_thread", f"cat{category_id}", title, True)
                self._save_state()
            else:
                self._log_action("create_thread", f"cat{category_id}", title, False)
            return success

        elif action == "set_essence":
            thread_id = decision.get("thread_id")
            if not thread_id:
                return False
            success = self.poster.set_essence(self.token, thread_id, is_essence=True)
            self._log_action("set_essence", thread_id, "", success)
            return success

        elif action == "delete_thread":
            if not self.admin_mk49_token:
                print("❌ 管理员删帖需要 ADMIN_MK49_TOKEN 环境变量")
                return False
            thread_id = decision.get("thread_id")
            if not thread_id:
                return False
            success = self.poster.delete_thread_admin(thread_id, self.admin_mk49_token)
            self._log_action("delete_thread", thread_id, "", success)
            return success

        else:
            print(f"未知操作: {action}")
            return False

    async def run_once(self):
        """单次运行：扫描帖子，AI决策并执行"""
        start_time = time.time()
        try:
            if not self.login():
                return

            await self.init_deepseek()
            self._update_daily_stats()

            # 扫描新帖子并处理
            new_threads = self.get_new_threads()
            print(f"📊 发现 {len(new_threads)} 个新帖子")
            for thread in new_threads:
                if self.reply_threads_count >= self.max_reply_threads:
                    break
                print(f"📄 分析帖子: {thread['title']} (ID: {thread['id']})")
                context = f"""
标题：{thread['title']}
内容：{thread.get('content', '')}
发布者：{thread.get('user', {}).get('nickname', '未知')}
帖子ID：{thread['id']}
"""
                decision = await self.decide_action(context, is_thread=True)
                if decision.get("action") != "ignore":
                    self.execute_action(decision)
                # 随机延迟 30~120 秒，避免过快
                delay = random.uniform(30, 120)
                print(f"⏳ 等待 {delay:.1f} 秒后继续...")
                time.sleep(delay)

            # 如果还有发帖配额，让AI决定是否发新帖
            if self.create_threads_count < self.max_create_threads and self.today_posts_count < self.daily_post_limit:
                context = "现在你可以决定是否发布一个新帖子。如果没有合适的主题，可以选择 ignore。"
                decision = await self.decide_action(context, is_thread=False)
                if decision.get("action") == "create_thread":
                    self.execute_action(decision)

            elapsed = time.time() - start_time
            print(f"✅ 本轮运行完成，耗时 {elapsed/60:.1f} 分钟")
            print(f"📊 统计: 回复帖子 {self.reply_threads_count}/{self.max_reply_threads}, "
                  f"发布新帖 {self.create_threads_count}/{self.max_create_threads}")
        except Exception as e:
            print(f"❌ 运行过程中发生错误: {e}")
            traceback.print_exc()
        finally:
            if self.ds:
                await self.ds.close()
            self._save_state()

if __name__ == "__main__":
    bot = AutonomousBot()
    asyncio.run(bot.run_once())
