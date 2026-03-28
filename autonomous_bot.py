# autonomous_bot.py
import os
import json
import time
import re
import traceback
from datetime import datetime
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

        # 论坛配置 - 必须使用 API 根地址
        self.base_url = os.getenv("BASE_URL", "https://mbbs.zdjl.site/mk48by049.mbbs.cc")
        self.username = os.getenv("BOT_USERNAME")
        self.password = os.getenv("BOT_PASSWORD")
        if not self.username or not self.password:
            raise ValueError("请设置 BOT_USERNAME 和 BOT_PASSWORD 环境变量")

        # 监听板块
        self.target_categories = [
            int(x) for x in os.getenv("TARGET_CATEGORIES", "2,5").split(",") if x
        ]

        # 加载风格文档和背景知识
        self.style = self._load_file("style.txt", "你是一个论坛用户，回复风格幽默风趣。")
        self.background = self._load_file("mk48.txt", "")

        # 状态持久化
        self.state_file = "state.json"
        self.state = self._load_state()

        # 每次运行最多执行的操作数
        self.max_actions_per_run = int(os.getenv("MAX_ACTIONS_PER_RUN", "5"))

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
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {
            "processed_threads": [],
            "processed_posts": [],
            "action_logs": []
        }

    def _save_state(self):
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

    def login(self):
        """使用 BBSTurkeyBotLogin 登录"""
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
        return True

    def get_new_threads(self):
        """获取未处理的新帖子"""
        new_threads = []
        for cat_id in self.target_categories:
            threads = self.poster.get_threads(self.token, category_id=cat_id, page_limit=20)
            if not isinstance(threads, list):
                print(f"⚠️ 获取板块 {cat_id} 帖子返回非列表")
                continue
            for t in threads:
                if t.get('id') not in self.state['processed_threads']:
                    new_threads.append(t)
        return new_threads

    def get_new_posts(self, thread_id):
        """获取某个帖子下未处理的新评论（包括嵌套）"""
        new_posts = []
        # 一级评论
        posts = self.poster.get_post_comments(self.token, thread_id)
        if not isinstance(posts, list):
            return []
        for p in posts:
            if p.get('id') not in self.state['processed_posts']:
                new_posts.append(p)
            # 评论的回复
            replies = self.poster.get_comment_replies(self.token, p['id'])
            for r in replies:
                if r.get('id') not in self.state['processed_posts']:
                    new_posts.append(r)
        return new_posts

    def decide_action(self, context):
        """AI 决策下一步行动"""
        prompt = f"""
你是一个论坛用户，你需要根据当前的情况决定做什么。

【你的角色风格】
{self.style}

【背景知识（可能相关的游戏/社区背景）】
{self.background}

【当前情况】
{context}

【你可以执行的操作】
- reply_to_thread: 回复帖子（需要提供 thread_id 和回复内容）
- reply_to_post: 回复评论（需要提供 post_id 和回复内容，如果是嵌套回复可指定 reply_to_post_id）
- like_thread: 给帖子点赞（thread_id）
- like_post: 给评论点赞（post_id）
- create_thread: 发布新帖子（需要 title, content, category_id）
- set_essence: 给帖子加精（thread_id，仅当你有权限时）
- ignore: 不采取任何行动

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

        elif action == "reply_to_thread":
            thread_id = decision.get("thread_id")
            content = decision.get("content")
            if not thread_id or not content:
                return False
            success = self.poster.create_comment(self.token, thread_id, content)
            if success:
                self.state["processed_threads"].append(thread_id)
                self._log_action("reply_to_thread", thread_id, content, True)
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
                self._log_action("reply_to_post", post_id, content, True)
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
            success, _ = self.poster.create_thread(self.token, category_id, title, content)
            self._log_action("create_thread", f"cat{category_id}", title, success)
            return success

        elif action == "set_essence":
            thread_id = decision.get("thread_id")
            if not thread_id:
                return False
            success = self.poster.set_essence(self.token, thread_id, is_essence=True)
            self._log_action("set_essence", thread_id, "", success)
            return success

        else:
            print(f"未知操作: {action}")
            return False

    def run_once(self):
        """单次运行：扫描新内容，AI 决策并执行"""
        try:
            if not self.login():
                return

            actions_done = 0

            # 1. 扫描新帖子
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

            # 2. 扫描已处理帖子的新评论
            recent_threads = self.state["processed_threads"][-20:]
            for thread_id in recent_threads:
                if actions_done >= self.max_actions_per_run:
                    break
                new_posts = self.get_new_posts(thread_id)
                for post in new_posts:
                    if actions_done >= self.max_actions_per_run:
                        break
                    print(f"💬 发现新评论: {post.get('content', '')[:50]}... (ID: {post['id']})")
                    context = f"""
这是一个评论：
内容：{post['content']}
发布者：{post.get('user', {}).get('nickname', '未知')}
所属帖子ID：{post.get('thread_id')}
评论ID：{post['id']}
如果这是对其他评论的回复，原回复ID可能是：{post.get('reply_to_post_id', '无')}
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
