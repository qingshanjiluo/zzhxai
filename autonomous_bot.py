import os
import json
import time
import re
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

        # 管理员删帖 token（可选）
        self.admin_mk49_token = os.getenv("ADMIN_MK49_TOKEN")

        # 加载风格文档和背景知识
        self.style = self._load_file("style.txt", "你是一个论坛用户，回复风格幽默风趣。")
        self.background = self._load_file("mk48.txt", "")

        # 状态持久化
        self.state_file = "state.json"
        self.state = self._load_state()

        # 每次运行最多执行的操作数
        max_actions_str = os.getenv("MAX_ACTIONS_PER_RUN", "5")
        self.max_actions_per_run = int(max_actions_str) if max_actions_str else 5

        # 每日发帖上限（仅针对新帖子）
        daily_limit_str = os.getenv("DAILY_POST_LIMIT", "10")
        self.daily_post_limit = int(daily_limit_str) if daily_limit_str else 10

        # 登录后的凭证
        self.token = None
        self.user_id = None
        self.session = None
        self.poster = None

        # 频率记录：当天已发新帖数
        self.today_posts_count = 0

    def _load_file(self, filename, default):
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return f.read()
        return default

    def _load_state(self):
        default = {
            "processed_threads": [],      # 已回复的帖子 ID（作为一级评论）
            "processed_posts": [],        # 保留，但不使用
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
        else:
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
            max_retries=3
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
        print(f"✅ 登录成功，用户ID: {self.user_id}")
        if self.admin_mk49_token:
            print("🔑 管理员删帖功能已启用")
        return True

    def get_new_threads(self):
        """获取未处理的新帖子（未被回复过的）"""
        new_threads = []
        for cat_id in self.target_categories:
            threads = self.poster.get_threads(self.token, category_id=cat_id, page_limit=20)
            if not isinstance(threads, list):
                continue
            for t in threads:
                tid = int(t.get('id'))
                if tid not in self.state['processed_threads']:
                    new_threads.append(t)
        return new_threads

    def decide_action(self, context):
        """AI 决策：只允许回复帖子、发新帖、点赞、加精、删帖（禁止回复评论）"""
        daily_limit_warning = ""
        if self.today_posts_count >= self.daily_post_limit:
            daily_limit_warning = "\n⚠️ 你今天已经达到每日发帖上限，请不要再创建新帖子（create_thread）。"

        prompt = f"""
你是一个论坛用户，你需要根据当前的情况决定做什么。

【你的角色风格】
{self.style}

【背景知识】
{self.background}

【当前情况】
{context}

【频率限制】
- 每天最多发 {self.daily_post_limit} 个新帖子（create_thread）。
- 回复帖子（reply_to_thread）不设严格上限，但请避免刷屏。
{daily_limit_warning}

【你可以执行的操作】
- reply_to_thread: 回复帖子（需要 thread_id 和回复内容）
- like_thread: 给帖子点赞（thread_id）
- create_thread: 发布新帖子（需要 title, content, category_id）
- set_essence: 给帖子加精（thread_id，需管理员权限）
- delete_thread: 删除帖子（thread_id，需管理员权限，仅在必要时使用）
- ignore: 不采取任何行动

【注意】你只能回复帖子本身，不能回复评论。

【输出格式】
请只输出一个 JSON 对象，不要有其他内容。
例如：{{"action": "reply_to_thread", "thread_id": 12345, "content": "生成的回复内容"}}
或者：{{"action": "ignore", "reason": "暂时不需要回复"}}
"""
        response = self.client.generate(prompt, max_tokens=300, temperature=0.8)
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

        # 发帖频率检查
        if action == "create_thread" and self.today_posts_count >= self.daily_post_limit:
            print(f"⚠️ 今日已达发帖上限 {self.daily_post_limit}，跳过创建新帖子")
            return False

        elif action == "reply_to_thread":
            thread_id = decision.get("thread_id")
            content = decision.get("content")
            if not thread_id or not content:
                return False
            success = self.poster.create_comment(self.token, thread_id, content)
            if success:
                self.state["processed_threads"].append(thread_id)
                self._log_action("reply_to_thread", thread_id, content, True)
                self._save_state()  # 立即保存
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
            success, _ = self.poster.create_thread(self.token, category_id, title, content)
            if success:
                self._log_action("create_thread", f"cat{category_id}", title, True)
                self._increment_daily_count()
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
        """单次运行：扫描新帖子，AI 决策并执行"""
        try:
            if not self.login():
                return

            self._update_daily_stats()

            actions_done = 0

            # 扫描新帖子
            new_threads = self.get_new_threads()
            for thread in new_threads:
                if actions_done >= self.max_actions_per_run:
                    break
                print(f"📄 发现新帖子: {thread['title']} (ID: {thread['id']})")
                context = f"""
这是一个新帖子：
标题：{thread['title']}
内容：{thread.get('content', '')}
发布者：{thread.get('user', {}).get('nickname', '未知')}
帖子ID：{thread['id']}
"""
                decision = self.decide_action(context)
                if decision.get("action") != "ignore":
                    self.execute_action(decision)
                    actions_done += 1
                time.sleep(2)

            print(f"✅ 本轮执行了 {actions_done} 个操作")
        except Exception as e:
            print(f"❌ 运行过程中发生错误: {e}")
            traceback.print_exc()
        finally:
            self._save_state()

if __name__ == "__main__":
    bot = AutonomousBot()
    bot.run_once()
