import os
import json
import time
import argparse
from datetime import datetime
from login import ForumLogin
from forum_api import ForumAPI
from deepseek_client import DeepSeekClient

class AutonomousBot:
    def __init__(self):
        self.base_url = os.getenv("BASE_URL", "https://mbbs.zdjl.site/mk48by049.mbbs.cc")
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("请设置环境变量 DEEPSEEK_API_KEY")
        self.client = DeepSeekClient(api_key=self.api_key)

        self.username = os.getenv("BOT_USERNAME")
        self.password = os.getenv("BOT_PASSWORD")
        if not self.username or not self.password:
            raise ValueError("请设置环境变量 BOT_USERNAME 和 BOT_PASSWORD")

        # 状态文件
        self.state_file = "state.json"
        self.state = self._load_state()

        # 风格文档
        self.style_file = "style.txt"
        self.style_content = self._load_style()

        self.api = None  # ForumAPI 实例，登录后设置

    def _load_state(self):
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"processed_threads": [], "processed_posts": [], "log": []}

    def _save_state(self):
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def _load_style(self):
        if os.path.exists(self.style_file):
            with open(self.style_file, 'r', encoding='utf-8') as f:
                return f.read()
        return "你是一个友好的论坛用户，喜欢帮助他人，语气幽默。"

    def _add_log(self, message):
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "message": message
        }
        self.state["log"].append(log_entry)
        # 保留最近1000条日志
        if len(self.state["log"]) > 1000:
            self.state["log"] = self.state["log"][-1000:]

    def login(self):
        login_obj = ForumLogin()
        success, session, token, user_id = login_obj.login(self.username, self.password)
        if not success:
            return False
        self.api = ForumAPI(token=token, user_id=user_id, base_url=self.base_url)
        return True

    def get_recent_context(self):
        """获取最近的帖子作为上下文，供AI决策"""
        categories = [2, 5]  # 可配置
        context = []
        for cat in categories:
            threads = self.api.get_threads(cat, limit=5)
            for t in threads[:5]:
                context.append({
                    "id": t["id"],
                    "title": t["title"],
                    "content": t.get("content", "")[:200],
                    "category": cat
                })
        return context

    def generate_plan(self, context):
        """使用AI生成行动计划"""
        system_prompt = f"""你是论坛智能代理，需要根据当前论坛状态和你的风格，决定要执行的操作。
你的风格文档：
{self.style_content}

当前状态：你已登录论坛，以下是最近的帖子：
{json.dumps(context, ensure_ascii=False, indent=2)}

你已经处理过的帖子ID列表（不再重复处理）：{self.state["processed_threads"]}
你已经处理过的评论ID列表：{self.state["processed_posts"]}

请决定你要执行的操作。你可以执行以下操作：
- 发新帖: {{"action": "create_thread", "category_id": 2, "title": "...", "content": "..."}}
- 回复帖子: {{"action": "reply", "thread_id": 123, "content": "..."}}
- 点赞帖子: {{"action": "like_thread", "thread_id": 123}}
- 点赞评论: {{"action": "like_post", "post_id": 456}}
- 设置精华: {{"action": "essence", "thread_id": 123, "is_essence": true}}
- 不做任何事: {{"action": "noop"}}

输出格式：一个JSON数组，包含一个或多个操作。只输出JSON，不要其他文字。
示例：[{{"action": "reply", "thread_id": 30209, "content": "我觉得这个帖子很有趣！"}}, {{"action": "like_thread", "thread_id": 30209}}]
"""
        response = self.client.generate(system_prompt, max_tokens=500, temperature=0.8)
        # 尝试解析JSON
        try:
            # 提取JSON部分（可能包含前后文字）
            import re
            json_match = re.search(r'\[.*\]', response, re.S)
            if json_match:
                plan = json.loads(json_match.group())
            else:
                plan = json.loads(response)
        except Exception as e:
            print(f"解析AI计划失败: {e}\n原始响应: {response}")
            plan = []
        return plan

    def execute_action(self, action):
        """执行单个操作，返回是否成功"""
        action_type = action.get("action")
        if action_type == "create_thread":
            category = action.get("category_id", 2)
            title = action.get("title", "")
            content = action.get("content", "")
            if not title or not content:
                return False
            success = self.api.create_thread(title, content, category)
            self._add_log(f"创建帖子: {title} -> {'成功' if success else '失败'}")
            return success

        elif action_type == "reply":
            thread_id = action.get("thread_id")
            content = action.get("content", "")
            if not thread_id or not content:
                return False
            # 检查是否已回复过该帖子
            if str(thread_id) in self.state["processed_threads"]:
                self._add_log(f"跳过回复帖子 {thread_id}，已处理过")
                return False
            success = self.api.create_post(thread_id, content)
            if success:
                self.state["processed_threads"].append(str(thread_id))
                self._add_log(f"回复帖子 {thread_id}: {content[:50]}...")
            else:
                self._add_log(f"回复帖子 {thread_id} 失败")
            return success

        elif action_type == "like_thread":
            thread_id = action.get("thread_id")
            if not thread_id:
                return False
            success = self.api.set_like(thread_id=thread_id, is_like=True)
            self._add_log(f"点赞帖子 {thread_id}: {'成功' if success else '失败'}")
            return success

        elif action_type == "like_post":
            post_id = action.get("post_id")
            if not post_id:
                return False
            success = self.api.set_like(post_id=post_id, is_like=True)
            self._add_log(f"点赞评论 {post_id}: {'成功' if success else '失败'}")
            return success

        elif action_type == "essence":
            thread_id = action.get("thread_id")
            is_essence = action.get("is_essence", True)
            if not thread_id:
                return False
            success = self.api.set_essence(thread_id, is_essence)
            self._add_log(f"设置精华 {thread_id}: {'成功' if success else '失败'}")
            return success

        elif action_type == "noop":
            return True

        else:
            self._add_log(f"未知操作类型: {action_type}")
            return False

    def run_once(self):
        """单次运行：登录 -> 获取上下文 -> 生成计划 -> 执行 -> 保存状态"""
        if not self.login():
            self._add_log("❌ 登录失败，退出")
            return

        context = self.get_recent_context()
        plan = self.generate_plan(context)
        self._add_log(f"生成计划: {json.dumps(plan, ensure_ascii=False)}")

        for action in plan:
            self.execute_action(action)
            time.sleep(2)  # 避免请求过快

        self._save_state()
        self._add_log("本轮运行结束")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="单次运行模式")
    args = parser.parse_args()

    bot = AutonomousBot()
    bot.run_once()
