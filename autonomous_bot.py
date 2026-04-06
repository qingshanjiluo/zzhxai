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

        # 监听板块
        target_categories_str = os.getenv("TARGET_CATEGORIES", "2,5")
        self.target_categories = [int(x) for x in target_categories_str.split(",") if x.strip()]
        self.skip_latest = int(os.getenv("SKIP_LATEST", "1"))
        self.login_retries = int(os.getenv("LOGIN_RETRIES", "50"))

        # 黑名单帖子ID（不评论）
        blacklist_str = os.getenv("BLACKLIST_THREAD_IDS", "")
        self.blacklist_threads = [int(x) for x in blacklist_str.split(",") if x.strip()]

        # 操作配额（仅保留回复帖子）
        self.max_reply_threads = int(os.getenv("MAX_REPLY_THREADS", "15"))  # 每次运行最多评论15个帖子
        self.daily_post_limit = int(os.getenv("DAILY_POST_LIMIT", "10"))    # 保留但实际无用（发帖已移除）
        self.max_comments_to_skip = int(os.getenv("MAX_COMMENTS_TO_SKIP", "10"))

        # 总运行时长目标（秒）
        self.target_duration = int(os.getenv("TARGET_DURATION_SECONDS", "1800"))

        # DeepSeek API 客户端
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("请设置 DEEPSEEK_API_KEY")
        self.client = DeepSeekClient(api_key=api_key)

        # 加载风格（从文件读取）
        self.style = self._load_file("style.txt", "你是论坛老坛友，幽默简洁。")

        # 状态持久化
        self.state_file = "state.json"
        self.state = self._load_state()

        # 运行计数
        self.reply_threads_count = 0

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
        if len(self.state["action_logs"]) > 200:
            self.state["action_logs"] = self.state["action_logs"][-200:]
        self._save_state()

    def _update_daily_stats(self):
        # 保留但不再用于发帖限制（可忽略）
        today = date.today().isoformat()
        if today not in self.state["daily_stats"]:
            self.state["daily_stats"][today] = {"posts": 0}
        # 不再使用发帖计数

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

    def get_threads_with_comments(self, category_id, limit=30, retries=2):
        """获取板块内的帖子，仅保留评论数 ≤ max_comments_to_skip 的帖子"""
        for attempt in range(retries):
            try:
                threads = self.poster.get_threads(self.token, category_id=category_id, page_limit=limit)
                if not isinstance(threads, list):
                    return []
                break
            except Exception as e:
                print(f"⚠️ 获取板块 {category_id} 失败 (尝试 {attempt+1}/{retries}): {e}")
                if attempt == retries - 1:
                    return []
                time.sleep(5)

        result = []
        for t in threads:
            tid = int(t.get('id'))
            if tid in self.state['processed_threads']:
                continue
            if tid in self.blacklist_threads:
                continue

            # 获取帖子详情，确保有内容字段
            detail = self.poster.get_thread_detail(self.token, tid)
            if detail:
                t['content'] = detail.get('content', '')
            else:
                t['content'] = t.get('content', '')

            # 获取评论（仅用于判断是否跳过）
            all_comments = []
            for _ in range(retries):
                try:
                    all_comments = self._get_all_comments(tid)
                    break
                except Exception as e:
                    print(f"⚠️ 获取帖子 {tid} 评论失败: {e}")
                    if _ == retries - 1:
                        all_comments = []
                    time.sleep(3)
            comment_count = len(all_comments)
            if comment_count > self.max_comments_to_skip:
                print(f"⏭️ 帖子 {tid} 评论数 {comment_count} > {self.max_comments_to_skip}，跳过")
                continue
            result.append({
                "thread": t,
                "comments": all_comments  # 仍可提供评论上下文给AI，但不用于回复
            })
        return result

    def _get_all_comments(self, thread_id):
        """获取帖子下所有评论（扁平列表），用于判断是否跳过"""
        comments = []
        first_level = self.poster.get_post_comments(self.token, thread_id)
        for c in first_level:
            comments.append({
                "id": c['id'],
                "content": c.get('content', ''),
                "user_nickname": c.get('user', {}).get('nickname', '未知'),
                "reply_to_post_id": None,
                "created_at": c.get('created_at', '')
            })
            replies = self._get_replies(c['id'])
            comments.extend(replies)
        return comments

    def _get_replies(self, post_id):
        replies = []
        resp = self.poster.get_comment_replies(self.token, post_id)
        for r in resp:
            replies.append({
                "id": r['id'],
                "content": r.get('content', ''),
                "user_nickname": r.get('user', {}).get('nickname', '未知'),
                "reply_to_post_id": r.get('reply_to_post_id'),
                "created_at": r.get('created_at', '')
            })
            deeper = self._get_replies(r['id'])
            replies.extend(deeper)
        return replies

    def decide_action(self, thread, comments):
        """AI 决策：仅决定是否回复帖子（回复内容）"""
        thread_title = thread.get('title', '无标题')
        thread_content = (thread.get('content', '') or '')[:200]
        thread_author = thread.get('user', {}).get('nickname', '未知用户')
        thread_time = thread.get('created_at', '未知时间')

        context = f"帖子标题：{thread_title}\n"
        context += f"发帖人：{thread_author}\n"
        context += f"发帖时间：{thread_time}\n"
        context += f"帖子内容：{thread_content}\n"

        if comments:
            context += "\n现有评论（部分）：\n"
            for idx, c in enumerate(comments[:10]):
                context += f"评论{idx+1}: {c['content'][:100]}\n"
        else:
            context += "暂无评论。\n"

        prompt = f"""
{self.style}

**当前帖子内容：**
{context}

**要求：**
1. 你的回复**必须直接针对帖子标题和内容**，不能使用“新人报到”、“欢迎”等无关模板。
2. 如果帖子是在抱怨、求助、讨论某个话题，你就围绕那个话题回复。
3. 回复要简短（不超过60字），幽默自然，不要套话。
4. **你只能回复帖子本身**（作为新评论），不能回复其他评论，也不能点赞或发新帖。

**输出格式（只输出JSON）：**
{{"action": "reply_to_thread", "content": "回复内容"}}
"""
        response = self.client.generate(prompt, max_tokens=150, temperature=0.9)
        try:
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                decision = json.loads(json_match.group())
                if decision.get("action") != "reply_to_thread":
                    # 如果AI返回其他操作，强制改为回复帖子
                    decision = {"action": "reply_to_thread", "content": "有意思，支持一下！"}
                return decision
            else:
                return {"action": "reply_to_thread", "content": "有意思，支持一下！"}
        except:
            return {"action": "reply_to_thread", "content": "有意思，支持一下！"}

    def execute_action(self, thread_id, decision):
        """执行回复帖子操作"""
        if decision.get("action") != "reply_to_thread":
            return False

        if self.reply_threads_count >= self.max_reply_threads:
            print("⚠️ 回复帖子配额已满")
            return False

        content = decision.get("content", "")
        if not content:
            content = "支持一下！"

        success, _ = self.poster.create_comment(self.token, thread_id, content)
        if success:
            self.reply_threads_count += 1
            self._log_action("reply_to_thread", thread_id, content, True)
            self.state["processed_threads"].append(thread_id)
            self._save_state()
        else:
            self._log_action("reply_to_thread", thread_id, content, False)
        return success

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
                if self.reply_threads_count >= self.max_reply_threads:
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
                if self.reply_threads_count >= self.max_reply_threads:
                    print("✅ 回复配额已用完，提前结束")
                    break

                thread = item['thread']
                comments = item['comments']
                tid = thread['id']
                print(f"📄 帖子: {thread['title']} (ID:{tid}) 评论数:{len(comments)}")
                decision = self.decide_action(thread, comments)
                self.execute_action(tid, decision)

                if idx < total_items - 1 and self.reply_threads_count < self.max_reply_threads:
                    wait_time = interval_per_item + random.uniform(-5, 5)
                    wait_time = max(5, wait_time)
                    print(f"⏳ 等待 {wait_time:.1f} 秒后处理下一个帖子...")
                    time.sleep(wait_time)

            elapsed = time.time() - start_time
            print(f"✅ 运行完成，耗时 {elapsed/60:.1f} 分钟")
            print(f"📊 统计: 回复帖子 {self.reply_threads_count}/{self.max_reply_threads}")
        except Exception as e:
            print(f"❌ 运行错误: {e}")
            traceback.print_exc()
        finally:
            self._save_state()

if __name__ == "__main__":
    bot = AutonomousBot()
    bot.run_once()
