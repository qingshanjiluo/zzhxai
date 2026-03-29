import os
import json
import time
import re
import random
import traceback
from datetime import datetime, date
from login import BBSTurkeyBotLogin
from post import BBSPoster
from deepseek_client import DeepSeekClient

class AutonomousBot:
    def __init__(self):
        # DeepSeek API 配置
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("请设置 DEEPSEEK_API_KEY 环境变量")
        self.client = DeepSeekClient(api_key=self.api_key)

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
        max_reply_posts_str = os.getenv("MAX_REPLY_POSTS", "10")
        self.max_reply_posts = int(max_reply_posts_str) if max_reply_posts_str else 10
        max_create_threads_str = os.getenv("MAX_CREATE_THREADS", "1")
        self.max_create_threads = int(max_create_threads_str) if max_create_threads_str else 1

        # 每日发帖上限（仅针对新帖子）
        daily_limit_str = os.getenv("DAILY_POST_LIMIT", "10")
        self.daily_post_limit = int(daily_limit_str) if daily_limit_str else 10

        # 操作计数
        self.reply_threads_count = 0
        self.reply_posts_count = 0
        self.create_threads_count = 0
        self.today_posts_count = 0

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
            "processed_posts": [],
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
            if "processed_posts" in state:
                state["processed_posts"] = [int(x) for x in state["processed_posts"]]
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
        # 刷新：请求首页或任意API
        try:
            self.session.get(self.base_url, timeout=10)
            print("✅ 页面刷新成功")
        except:
            print("⚠️ 页面刷新失败，但继续运行")

        print(f"✅ 登录成功，用户ID: {self.user_id}")
        if self.admin_mk49_token:
            print("🔑 管理员删帖功能已启用")
        return True

    def get_new_threads(self):
        """获取未处理的新帖子，跳过黑名单用户和最新N个"""
        new_threads = []
        for cat_id in self.target_categories:
            threads = self.poster.get_threads(self.token, category_id=cat_id, page_limit=30)
            if not isinstance(threads, list):
                continue
            # 跳过最新的 self.skip_latest 个
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

    def get_new_posts(self, thread_id, limit=50):
        """获取帖子下未处理的新评论（包括嵌套），跳过黑名单"""
        new_posts = []
        # 一级评论
        posts = self.poster.get_post_comments(self.token, thread_id)
        if not isinstance(posts, list):
            return []
        for p in posts:
            pid = int(p.get('id'))
            author_id = int(p.get('user_id', 0))
            if pid in self.state['processed_posts']:
                continue
            if author_id in self.blacklist:
                continue
            new_posts.append(p)
            # 获取评论的回复
            replies = self.poster.get_comment_replies(self.token, pid)
            for r in replies:
                rid = int(r.get('id'))
                rauthor_id = int(r.get('user_id', 0))
                if rid in self.state['processed_posts']:
                    continue
                if rauthor_id in self.blacklist:
                    continue
                new_posts.append(r)
        return new_posts

    def get_thread_comments_context(self, thread_id, max_comments=10):
        """获取帖子下的前几条评论作为上下文，供AI分析"""
        posts = self.poster.get_post_comments(self.token, thread_id)
        if not isinstance(posts, list):
            return ""
        context = ""
        for idx, p in enumerate(posts[:max_comments]):
            author = p.get('user', {}).get('nickname', '未知')
            content = p.get('content', '')
            context += f"评论{idx+1}（{author}）：{content}\n"
        return context

    def decide_action(self, context, is_thread=True, thread_comments=""):
        """AI决策，支持帖子上下文和已有评论"""
        if is_thread:
            action_prompt = f"""
你是一个论坛用户，你需要根据当前帖子及其评论决定做什么。

【你的角色风格】
{self.style}

【背景知识】
{self.background}

【帖子详情】
{context}

【已有评论摘要】
{thread_comments if thread_comments else "暂无评论"}

【操作配额】（已执行次数/总限额）
- 回复帖子：{self.reply_threads_count}/{self.max_reply_threads}
- 回复评论：{self.reply_posts_count}/{self.max_reply_posts}
- 发布新帖：{self.create_threads_count}/{self.max_create_threads}

【你可以执行的操作】
- reply_to_thread: 回复帖子（需要 thread_id 和回复内容）
- reply_to_post: 回复某条评论（需要 post_id 和回复内容，可指定 reply_to_post_id 实现嵌套）
- like_thread: 给帖子点赞（thread_id）
- like_post: 给评论点赞（post_id）
- create_thread: 发布新帖子（需要 title, content, category_id）
- set_essence: 给帖子加精（thread_id，需管理员权限）
- delete_thread: 删除帖子（thread_id，需管理员权限，仅在必要时使用）
- ignore: 不采取任何行动

【注意】优先回复帖子或评论，不要刷屏。如果配额已满，则只能 ignore。

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
- 回复帖子：{self.reply_threads_count}/{self.max_reply_threads}
- 回复评论：{self.reply_posts_count}/{self.max_reply_posts}
- 发布新帖：{self.create_threads_count}/{self.max_create_threads}

【你可以执行的操作】
- create_thread: 发布新帖子（需要 title, content, category_id）
- ignore: 不采取任何行动

【输出格式】
只输出一个 JSON 对象。
"""
        response = self.client.generate(action_prompt, max_tokens=350, temperature=0.8)
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
        if action == "reply_to_post" and self.reply_posts_count >= self.max_reply_posts:
            print("⚠️ 回复评论配额已满，跳过")
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

        elif action == "reply_to_post":
            post_id = decision.get("post_id")
            content = decision.get("content")
            reply_to = decision.get("reply_to_post_id")
            if not post_id or not content:
                return False
            success = self.poster.reply_to_comment(self.token, post_id, content, reply_to)
            if success:
                self.state["processed_posts"].append(post_id)
                self.reply_posts_count += 1
                self._log_action("reply_to_post", post_id, content, True)
                self._save_state()
            else:
                self._log_action("reply_to_post", post_id, content, False)
            return success

        elif action == "like_thread":
            thread_id = decision.get("thread_id")
            if not thread_id:
                return False
            success = self.poster.set_thread_like(self.token, thread_id, like=True)
            self._log_action("like_thread", thread_id, "", success)
            return success

        elif action == "like_post":
            post_id = decision.get("post_id")
            if not post_id:
                return False
            success = self.poster.set_post_like(self.token, post_id, like=True)
            self._log_action("like_post", post_id, "", success)
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

    def run_once(self):
        """单次运行：扫描帖子、评论，AI决策并执行，总时长控制在30分钟左右"""
        start_time = time.time()
        if not self.login():
            return

        self._update_daily_stats()

        # 1. 扫描新帖子并处理
        new_threads = self.get_new_threads()
        print(f"📊 发现 {len(new_threads)} 个新帖子")
        for thread in new_threads:
            if self.reply_threads_count >= self.max_reply_threads:
                break
            print(f"📄 分析帖子: {thread['title']} (ID: {thread['id']})")
            # 获取帖子下的评论作为上下文
            comments_context = self.get_thread_comments_context(thread['id'])
            context = f"""
标题：{thread['title']}
内容：{thread.get('content', '')}
发布者：{thread.get('user', {}).get('nickname', '未知')}
帖子ID：{thread['id']}
"""
            decision = self.decide_action(context, is_thread=True, thread_comments=comments_context)
            if decision.get("action") != "ignore":
                self.execute_action(decision)
            # 随机延迟 30~120 秒，避免过快
            delay = random.uniform(30, 120)
            print(f"⏳ 等待 {delay:.1f} 秒后继续...")
            time.sleep(delay)

        # 2. 扫描已处理帖子的新评论（仅对最近20个帖子）
        recent_threads = self.state["processed_threads"][-20:]
        for thread_id in recent_threads:
            if self.reply_posts_count >= self.max_reply_posts:
                break
            new_posts = self.get_new_posts(thread_id)
            if not new_posts:
                continue
            print(f"💬 帖子 {thread_id} 下发现 {len(new_posts)} 条新评论")
            for post in new_posts[:self.max_reply_posts - self.reply_posts_count]:
                print(f"  分析评论: {post.get('content', '')[:50]}... (ID: {post['id']})")
                context = f"""
这是一个评论：
内容：{post['content']}
发布者：{post.get('user', {}).get('nickname', '未知')}
所属帖子ID：{post.get('thread_id')}
评论ID：{post['id']}
如果这是对其他评论的回复，原回复ID可能是：{post.get('reply_to_post_id', '无')}
"""
                decision = self.decide_action(context, is_thread=False)
                if decision.get("action") != "ignore":
                    self.execute_action(decision)
                delay = random.uniform(20, 60)
                print(f"⏳ 等待 {delay:.1f} 秒后继续...")
                time.sleep(delay)

        # 3. 如果还有发帖配额，让AI决定是否发新帖
        if self.create_threads_count < self.max_create_threads and self.today_posts_count < self.daily_post_limit:
            context = "现在你可以决定是否发布一个新帖子。如果没有合适的主题，可以选择 ignore。"
            decision = self.decide_action(context, is_thread=False)
            if decision.get("action") == "create_thread":
                self.execute_action(decision)

        elapsed = time.time() - start_time
        print(f"✅ 本轮运行完成，耗时 {elapsed/60:.1f} 分钟")
        print(f"📊 统计: 回复帖子 {self.reply_threads_count}/{self.max_reply_threads}, "
              f"回复评论 {self.reply_posts_count}/{self.max_reply_posts}, "
              f"发布新帖 {self.create_threads_count}/{self.max_create_threads}")
        self._save_state()

if __name__ == "__main__":
    bot = AutonomousBot()
    bot.run_once()
