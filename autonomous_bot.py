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
        self.skip_latest = int(os.getenv("SKIP_LATEST", "1"))
        self.login_retries = int(os.getenv("LOGIN_RETRIES", "50"))

        # 黑名单帖子ID
        blacklist_str = os.getenv("BLACKLIST_THREAD_IDS", "")
        self.blacklist_threads = [int(x) for x in blacklist_str.split(",") if x.strip()]

        # 操作配额
        self.max_reply_threads = int(os.getenv("MAX_REPLY_THREADS", "15"))
        self.max_reply_comments = int(os.getenv("MAX_REPLY_COMMENTS", "10"))
        self.max_create_threads = int(os.getenv("MAX_CREATE_THREADS", "2"))
        self.daily_post_limit = int(os.getenv("DAILY_POST_LIMIT", "10"))

        # 评论数阈值（超过10跳过）
        self.max_comments_to_skip = int(os.getenv("MAX_COMMENTS_TO_SKIP", "10"))

        # 总运行时长目标（秒）
        self.target_duration = int(os.getenv("TARGET_DURATION_SECONDS", "1800"))  # 默认30分钟

        # DeepSeek API 客户端
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("请设置 DEEPSEEK_API_KEY")
        self.client = DeepSeekClient(api_key=api_key)

        # 加载风格
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
            "processed_threads": [],   # 已回复过的帖子ID
            "processed_posts": [],     # 已回复过的评论ID
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
        if len(self.state["action_logs"]) > 200:
            self.state["action_logs"] = self.state["action_logs"][-200:]
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
        """获取板块内的帖子，仅保留评论数 ≤ max_comments_to_skip 且未处理过的帖子"""
        threads = self.poster.get_threads(self.token, category_id=category_id, page_limit=limit)
        if not isinstance(threads, list):
            return []

        result = []
        for t in threads:
            tid = int(t.get('id'))
            # 跳过已处理的帖子
            if tid in self.state['processed_threads']:
                continue
            if tid in self.blacklist_threads:
                continue
            all_comments = self._get_all_comments(tid)
            comment_count = len(all_comments)
            if comment_count > self.max_comments_to_skip:
                print(f"⏭️ 帖子 {tid} 评论数 {comment_count} > {self.max_comments_to_skip}，跳过")
                continue
            result.append({
                "thread": t,
                "comments": all_comments
            })
        return result

    def _get_all_comments(self, thread_id):
        """递归获取帖子下所有评论（扁平列表），包含作者、时间等信息"""
        comments = []
        first_level = self.poster.get_post_comments(self.token, thread_id)
        for c in first_level:
            # 跳过已经回复过的评论
            if c.get('id') in self.state['processed_posts']:
                continue
            comments.append({
                "id": c['id'],
                "content": c.get('content', '')[:100],  # 限制长度
                "user_nickname": c.get('user', {}).get('nickname', '未知'),
                "reply_to_post_id": None,
                "created_at": c.get('created_at', '')
            })
            replies = self._get_replies(c['id'])
            comments.extend(replies)
        return comments

    def _get_replies(self, post_id):
        """获取某条评论的所有回复，包含作者、时间，并过滤已处理的"""
        replies = []
        resp = self.poster.get_comment_replies(self.token, post_id)
        for r in resp:
            if r.get('id') in self.state['processed_posts']:
                continue
            replies.append({
                "id": r['id'],
                "content": r.get('content', '')[:100],
                "user_nickname": r.get('user', {}).get('nickname', '未知'),
                "reply_to_post_id": r.get('reply_to_post_id'),
                "created_at": r.get('created_at', '')
            })
            deeper = self._get_replies(r['id'])
            replies.extend(deeper)
        return replies

    def decide_action(self, thread, comments):
        """AI 决策，包含帖子完整信息和评论元数据"""
        # 截取帖子内容前200字
        thread_title = thread.get('title', '无标题')
        thread_content = (thread.get('content', '') or '')[:200]
        thread_author = thread.get('user', {}).get('nickname', '未知用户')
        thread_time = thread.get('created_at', '未知时间')

        context = f"帖子标题：{thread_title}\n"
        context += f"发帖人：{thread_author}\n"
        context += f"发帖时间：{thread_time}\n"
        context += f"帖子内容：{thread_content}\n"

        if comments:
            context += "\n现有评论（按时间顺序）：\n"
            for idx, c in enumerate(comments[:15]):  # 最多展示15条评论
                comment_time = c.get('created_at', '未知时间')[:19] if c.get('created_at') else '未知时间'
                context += f"评论{idx+1} (ID:{c['id']}, 作者:{c['user_nickname']}, 时间:{comment_time}): {c['content'][:100]}\n"
                if c.get('reply_to_post_id'):
                    context += f"  ↳ 回复评论ID: {c['reply_to_post_id']}\n"
        else:
            context += "暂无评论。\n"

        prompt = f"""
{self.style}

**重要：这个帖子的评论数少于10条，你必须做出回应，不要忽略！**

请根据以上帖子内容和评论，选择以下操作之一：
- 回复帖子（作为新评论）
- 回复某条评论（提供评论ID）
- 给帖子点赞
- 发布一个新帖子（标题和内容）

你的回复应简短幽默（不超过60字）。**强烈建议回复帖子或评论**。如果可以，请自然地提及发帖人、评论者或帖子内容，使回复更贴切。

输出格式（只输出JSON）：
{{"action": "reply_to_thread", "content": "回复内容"}}
{{"action": "reply_to_comment", "post_id": 12345, "content": "回复内容"}}
{{"action": "like_thread"}}
{{"action": "create_thread", "title": "标题", "content": "内容"}}
"""
        response = self.client.generate(prompt, max_tokens=150, temperature=0.9)
        try:
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            else:
                return {"action": "reply_to_thread", "content": "有意思，支持一下！"}
        except:
            return {"action": "reply_to_thread", "content": "有意思，支持一下！"}

    def execute_action(self, thread_id, decision, comments):
        action = decision.get("action")
        if action == "ignore":
            action = "reply_to_thread"
            decision["content"] = decision.get("content", "有点意思，支持一下！")

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
                content = "支持一下！"
            success, comment_id = self.poster.create_comment(self.token, thread_id, content)
            if success and comment_id:
                self.reply_threads_count += 1
                self._log_action("reply_to_thread", thread_id, content, True)
                self.state["processed_threads"].append(thread_id)
                self._save_state()
            else:
                self._log_action("reply_to_thread", thread_id, content, False)
            return success

        elif action == "reply_to_comment":
            post_id = decision.get("post_id")
            content = decision.get("content", "")
            if not post_id or not content:
                return False
            reply_to = None
            for c in comments:
                if c['id'] == post_id and c['reply_to_post_id']:
                    reply_to = c['reply_to_post_id']
            success, comment_id = self.poster.reply_to_comment(self.token, post_id, content, reply_to)
            if success and comment_id:
                self.reply_comments_count += 1
                self._log_action("reply_to_comment", post_id, content, True)
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
            success, _ = self.poster.create_thread(self.token, 2, title, content)
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

            # 收集所有待处理的帖子（跨板块）
            all_items = []
            for cat_id in self.target_categories:
                print(f"📂 扫描板块 {cat_id}")
                items = self.get_threads_with_comments(cat_id, limit=30)
                all_items.extend(items)
                # 如果已经达到配额上限，停止扫描
                if (self.reply_threads_count >= self.max_reply_threads and
                    self.reply_comments_count >= self.max_reply_comments and
                    self.create_threads_count >= self.max_create_threads):
                    break

            total_items = len(all_items)
            if total_items == 0:
                print("⚠️ 没有找到符合条件的帖子，运行结束。")
                return

            # 动态计算每个帖子之间的间隔，使总运行时间接近 target_duration
            elapsed_before = time.time() - start_time
            remaining_seconds = max(10, self.target_duration - elapsed_before)
            interval_per_item = remaining_seconds / total_items
            interval_per_item = min(180, max(10, interval_per_item))
            print(f"📊 共 {total_items} 个待处理帖子，计划每个间隔约 {interval_per_item:.1f} 秒")

            for idx, item in enumerate(all_items):
                # 检查是否已超过配额
                if (self.reply_threads_count >= self.max_reply_threads and
                    self.reply_comments_count >= self.max_reply_comments and
                    self.create_threads_count >= self.max_create_threads):
                    print("✅ 所有配额已用完，提前结束")
                    break

                thread = item['thread']
                comments = item['comments']
                tid = thread['id']
                print(f"📄 帖子: {thread['title']} (ID:{tid}) 评论数:{len(comments)}")
                decision = self.decide_action(thread, comments)
                self.execute_action(tid, decision, comments)

                if idx < total_items - 1:
                    wait_time = interval_per_item + random.uniform(-5, 5)
                    wait_time = max(5, wait_time)
                    print(f"⏳ 等待 {wait_time:.1f} 秒后处理下一个帖子...")
                    time.sleep(wait_time)

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
