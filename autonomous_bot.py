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
        # 论坛配置
        self.base_url = os.getenv("BASE_URL", "https://mbbs.zdjl.site/mk48by049.mbbs.cc")
        self.username = os.getenv("BOT_USERNAME")
        self.password = os.getenv("BOT_PASSWORD")
        if not self.username or not self.password:
            raise ValueError("请设置 BOT_USERNAME 和 BOT_PASSWORD")

        # 监控板块
        target_categories_str = os.getenv("TARGET_CATEGORIES", "2,5")
        self.target_categories = [int(x) for x in target_categories_str.split(",") if x.strip()]
        self.skip_latest = int(os.getenv("SKIP_LATEST", "5"))
        self.login_retries = int(os.getenv("LOGIN_RETRIES", "50"))

        # 黑名单帖子ID（不评论）
        blacklist_str = os.getenv("BLACKLIST_THREAD_IDS", "")
        self.blacklist_threads = [int(x) for x in blacklist_str.split(",") if x.strip()]

        # 操作配额
        self.max_reply_threads = int(os.getenv("MAX_REPLY_THREADS", "10"))
        self.max_reply_comments = int(os.getenv("MAX_REPLY_COMMENTS", "5"))
        self.max_create_threads = int(os.getenv("MAX_CREATE_THREADS", "1"))
        self.daily_post_limit = int(os.getenv("DAILY_POST_LIMIT", "5"))

        # 点赞概率（AI 决策，但可设置偏好）
        self.like_preference = float(os.getenv("LIKE_PREFERENCE", "0.3"))  # AI 在决策时会更倾向于点赞的概率

        # DeepSeek API 客户端
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("请设置 DEEPSEEK_API_KEY")
        self.client = DeepSeekClient(api_key=api_key)

        # 加载风格（可选，用于提示词）
        self.style = self._load_file("style.txt", "你是论坛管理员，幽默风趣。")

        # 状态持久化
        self.state_file = "state.json"
        self.state = self._load_state()

        # 运行计数
        self.reply_threads_count = 0
        self.reply_comments_count = 0
        self.create_threads_count = 0
        self.today_posts_count = 0

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
            "processed_posts": [],      # 已处理的评论ID
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
        print(f"🔐 登录论坛账号: {self.username}")
        login_bot = BBSTurkeyBotLogin(
            base_url=self.base_url,
            username=self.username,
            password=self.password,
            max_retries=self.login_retries
        )
        success, result, session = login_bot.login_with_retry()
        if not success:
            print("❌ 论坛登录失败")
            return False
        user_data = result.get('data', {})
        self.token = user_data.get('token')
        self.user_id = user_data.get('id')
        if not self.token or not self.user_id:
            print("❌ 登录响应缺少 token 或 user_id")
            return False
        self.session = session
        self.poster = BBSPoster(session, self.base_url)
        print("⏳ 等待5秒并刷新页面以获取管理员权限...")
        time.sleep(5)
        try:
            self.session.get(self.base_url, timeout=10)
            print("✅ 页面刷新成功")
        except:
            print("⚠️ 页面刷新失败，但继续运行")
        print(f"✅ 论坛登录成功，用户ID: {self.user_id}")
        return True

    def get_threads_with_comments(self, category_id, limit=30):
        """获取板块内的帖子，并附加评论列表（仅限评论数 ≤ 10 的帖子）"""
        threads = self.poster.get_threads(self.token, category_id=category_id, page_limit=limit)
        if not isinstance(threads, list):
            return []

        result = []
        for t in threads:
            tid = int(t.get('id'))
            # 跳过已处理、黑名单、置顶帖（可选）
            if tid in self.state['processed_threads']:
                continue
            if tid in self.blacklist_threads:
                continue
            # 获取所有评论（包括嵌套）
            all_comments = self._get_all_comments(tid)
            if len(all_comments) > 10:
                print(f"⏭️ 帖子 {tid} 评论数 {len(all_comments)} > 10，跳过")
                continue
            result.append({
                "thread": t,
                "comments": all_comments
            })
        return result

    def _get_all_comments(self, thread_id):
        """递归获取帖子下所有评论（扁平列表，每条包含 id, content, user_nickname, reply_to_post_id）"""
        comments = []
        # 一级评论
        first_level = self.poster.get_post_comments(self.token, thread_id)
        for c in first_level:
            comments.append({
                "id": c['id'],
                "content": c.get('content', ''),
                "user_nickname": c.get('user', {}).get('nickname', '未知'),
                "reply_to_post_id": None
            })
            # 递归获取回复
            replies = self._get_replies(c['id'])
            comments.extend(replies)
        return comments

    def _get_replies(self, post_id):
        """获取某条评论的所有回复（嵌套）"""
        replies = []
        resp = self.poster.get_comment_replies(self.token, post_id)
        for r in resp:
            replies.append({
                "id": r['id'],
                "content": r.get('content', ''),
                "user_nickname": r.get('user', {}).get('nickname', '未知'),
                "reply_to_post_id": r.get('reply_to_post_id')
            })
            # 如果还有更深层的回复，继续递归（限制深度，这里假设最多2层）
            deeper = self._get_replies(r['id'])
            replies.extend(deeper)
        return replies

    def decide_action(self, thread, comments):
        """AI 决策：是否回复帖子、回复某条评论、点赞、发新帖"""
        # 构建简洁的上下文
        context = f"帖子标题：{thread['title']}\n帖子内容：{thread.get('content', '')}\n"
        if comments:
            context += "\n现有评论（按时间顺序）：\n"
            for idx, c in enumerate(comments[:10]):  # 最多展示10条评论
                context += f"评论{idx+1} (ID:{c['id']}, 作者:{c['user_nickname']}): {c['content'][:100]}\n"
                if c['reply_to_post_id']:
                    context += f"  ↳ 回复评论ID: {c['reply_to_post_id']}\n"
        else:
            context += "暂无评论。\n"

        prompt = f"""
{self.style}

请根据以上帖子内容和评论，决定你要做什么。你可以：
- 回复帖子（作为新评论）
- 回复某条评论（需要提供评论ID和回复内容）
- 给帖子点赞
- 发布一个新帖子（需要标题和内容）
- 什么都不做

注意：你的回复应简短幽默（不超过50字）。如果选择回复评论，请指明评论ID。

输出格式（只输出JSON，不要其他文字）：
{{"action": "reply_to_thread", "content": "回复内容"}}
{{"action": "reply_to_comment", "post_id": 12345, "content": "回复内容"}}
{{"action": "like_thread"}}
{{"action": "create_thread", "title": "标题", "content": "内容"}}
{{"action": "ignore"}}
"""
        response = self.client.generate(prompt, max_tokens=150, temperature=0.8)
        try:
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            else:
                return {"action": "ignore"}
        except:
            return {"action": "ignore"}

    def execute_action(self, thread_id, decision, comments):
        action = decision.get("action")
        if action == "ignore":
            return False

        # 配额检查
        if action == "reply_to_thread" and self.reply_threads_count >= self.max_reply_threads:
            print("⚠️ 回复帖子配额已满")
            return False
        if action == "reply_to_comment" and self.reply_comments_count >= self.max_reply_comments:
            print("⚠️ 回复评论配额已满")
            return False
        if action == "create_thread" and self.create_threads_count >= self.max_create_threads:
            print("⚠️ 发帖配额已满")
            return False
        if action == "create_thread" and self.today_posts_count >= self.daily_post_limit:
            print("⚠️ 今日发帖上限已到")
            return False

        if action == "reply_to_thread":
            content = decision.get("content", "")
            if not content:
                return False
            success = self.poster.create_comment(self.token, thread_id, content)
            if success:
                self.reply_threads_count += 1
                self._log_action("reply_to_thread", thread_id, content, True)
                self.state["processed_threads"].append(thread_id)
                self._save_state()
            else:
                self._log_action("reply_to_thread", thread_id, content, False)
            return success

        elif action == "reply_to_comment":
            post_id = decision.get("post_id")
            content = decision.get("content")
            if not post_id or not content:
                return False
            # 查找该评论是否存在，以及是否需要传入 reply_to_post_id（如果是嵌套回复）
            reply_to = None
            for c in comments:
                if c['id'] == post_id and c['reply_to_post_id']:
                    reply_to = c['reply_to_post_id']
            success = self.poster.reply_to_comment(self.token, post_id, content, reply_to)
            if success:
                self.reply_comments_count += 1
                self._log_action("reply_to_comment", post_id, content, True)
                # 标记该评论已处理（避免重复回复）
                self.state["processed_posts"].append(post_id)
                self._save_state()
            else:
                self._log_action("reply_to_comment", post_id, content, False)
            return success

        elif action == "like_thread":
            success = self.poster.set_thread_like(self.token, thread_id, like=True)
            self._log_action("like_thread", thread_id, "", success)
            return success

        elif action == "create_thread":
            title = decision.get("title", "")
            content = decision.get("content", "")
            if not title or not content:
                return False
            success, _ = self.poster.create_thread(self.token, 2, title, content)  # 默认板块2
            if success:
                self.create_threads_count += 1
                self._increment_daily_count()
                self._log_action("create_thread", "cat2", title, True)
                self._save_state()
            else:
                self._log_action("create_thread", "cat2", title, False)
            return success

        return False

    def run_once(self):
        start_time = time.time()
        try:
            if not self.login():
                return
            self._update_daily_stats()

            for cat_id in self.target_categories:
                if (self.reply_threads_count >= self.max_reply_threads and
                    self.reply_comments_count >= self.max_reply_comments and
                    self.create_threads_count >= self.max_create_threads):
                    break
                print(f"📂 扫描板块 {cat_id}")
                items = self.get_threads_with_comments(cat_id, limit=20)
                for item in items:
                    thread = item['thread']
                    comments = item['comments']
                    tid = thread['id']
                    print(f"📄 帖子: {thread['title']} (ID:{tid}) 评论数:{len(comments)}")
                    decision = self.decide_action(thread, comments)
                    if decision.get("action") != "ignore":
                        self.execute_action(tid, decision, comments)
                    time.sleep(random.uniform(2, 5))

            elapsed = time.time() - start_time
            print(f"✅ 运行完成，耗时 {elapsed/60:.1f} 分钟")
            print(f"📊 统计: 回复帖子 {self.reply_threads_count}/{self.max_reply_threads}, "
                  f"回复评论 {self.reply_comments_count}/{self.max_reply_comments}, "
                  f"发新帖 {self.create_threads_count}/{self.max_create_threads}")
        except Exception as e:
            print(f"❌ 运行错误: {e}")
            traceback.print_exc()
        finally:
            self._save_state()

if __name__ == "__main__":
    bot = AutonomousBot()
    bot.run_once()
