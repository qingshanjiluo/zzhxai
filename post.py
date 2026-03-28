# post.py
import requests
import json

class BBSPoster:
    def __init__(self, session, base_url):
        self.session = session
        self.base_url = base_url
        self.api_base = f"{base_url}/bbs"
        
        # API 端点（已根据接口文档修正）
        self.create_thread_url = f"{self.api_base}/threads"
        self.list_threads_url = f"{self.api_base}/threads/list"
        self.list_posts_url = f"{self.api_base}/posts/list"
        self.create_post_url = f"{self.api_base}/posts/create"
        self.set_essence_url = f"{self.api_base}/threads/setEssence"
        self.set_sticky_url = f"{self.api_base}/threads/setSticky"
        self.set_approved_url = f"{self.api_base}/threads/setApproved"
        self.set_thread_like_url = f"{self.api_base}/threads/setLike"
        self.set_post_like_url = f"{self.api_base}/posts/setLike"
        self.batch_delete_threads_url = f"{self.api_base}/threads/batchDelete"
        self.batch_delete_posts_url = f"{self.api_base}/posts/batchDeletePosts"
        self.create_comment_reply_url = f"{self.api_base}/posts/createComment"
        self.list_comments_replies_url = f"{self.api_base}/posts/listComments"
        self.user_list_url = f"{self.api_base}/users/list"
        self.get_thread_url = f"{self.api_base}/threads"          # 获取单个帖子，需拼接ID

    def create_thread(self, token, category_id, title, content):
        """创建新帖子，返回 (success, thread_data)"""
        try:
            headers = {'Authorization': token, 'Content-Type': 'application/json'}
            thread_data = {
                "category_id": category_id,
                "title": title,
                "content": content
            }
            print(f"[发帖] 创建帖子: {title}")
            response = self.session.post(self.create_thread_url, json=thread_data, headers=headers, timeout=15)
            if response.status_code == 200:
                result = response.json()
                if result.get('success') is True:
                    data = result.get('data', {})
                    print(f"[成功] 发帖成功！帖子ID: {data.get('id')}")
                    return True, data
                print(f"[失败] 发帖失败: {result.get('message', '未知错误')}")
                return False, None
            else:
                print(f"[错误] 发帖HTTP错误: {response.status_code}")
                return False, None
        except Exception as e:
            print(f"[异常] 发帖异常: {e}")
            return False, None

    def get_threads(self, token, category_id=None, page_limit=20, page_offset=0, user_id=None):
        """获取帖子列表"""
        try:
            headers = {'Authorization': token}
            params = {
                "page_limit": page_limit,
                "page_offset": page_offset,
                "sort": "-created_at"
            }
            if category_id is not None:
                params["category_id"] = category_id
            if user_id is not None:
                params["user_id"] = user_id
            response = self.session.get(self.list_threads_url, headers=headers, params=params, timeout=15)
            if response.status_code == 200:
                result = response.json()
                if result.get('success') is True:
                    threads = result.get('data', [])
                    print(f"[信息] 获取到 {len(threads)} 个帖子 (页偏移 {page_offset})")
                    return threads
                else:
                    print(f"[失败] 获取帖子列表失败: {result.get('message')}")
                    return []
            else:
                print(f"[错误] 获取帖子列表HTTP错误: {response.status_code}")
                return []
        except Exception as e:
            print(f"[异常] 获取帖子列表异常: {e}")
            return []

    def get_thread_detail(self, token, thread_id):
        """获取单个帖子详情"""
        try:
            headers = {'Authorization': token}
            url = f"{self.get_thread_url}/{thread_id}"
            response = self.session.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                result = response.json()
                if result.get('success') is True:
                    return result.get('data')
            return None
        except Exception as e:
            print(f"[异常] 获取帖子详情失败: {e}")
            return None

    def get_post_comments(self, token, thread_id):
        """获取帖子下的所有评论（一级评论）"""
        try:
            headers = {'Authorization': token}
            params = {
                "thread_id": thread_id,
                "page_limit": 200,
                "page_offset": 0
            }
            response = self.session.get(self.list_posts_url, headers=headers, params=params, timeout=15)
            if response.status_code == 200:
                result = response.json()
                if result.get('success') is True:
                    posts = result.get('data', [])
                    # 过滤掉帖子本身（通常 is_first 为 True 的是帖子）
                    comments = [post for post in posts if not post.get('is_first', True)]
                    return comments
                else:
                    print(f"[失败] 获取评论失败: {result.get('message')}")
                    return []
            else:
                print(f"[错误] 获取评论HTTP错误: {response.status_code}")
                return []
        except Exception as e:
            print(f"[异常] 获取评论异常: {e}")
            return []

    def get_comment_replies(self, token, post_id, page_limit=100, page_offset=0):
        """获取某条评论的回复（嵌套评论）"""
        try:
            headers = {'Authorization': token}
            params = {
                "post_id": post_id,
                "page_limit": page_limit,
                "page_offset": page_offset
            }
            response = self.session.get(self.list_comments_replies_url, headers=headers, params=params, timeout=15)
            if response.status_code == 200:
                result = response.json()
                if result.get('success') is True:
                    return result.get('data', {}).get('list', [])
                else:
                    return []
            else:
                return []
        except Exception as e:
            print(f"[异常] 获取评论回复异常: {e}")
            return []

    def create_comment(self, token, thread_id, content):
        """发表评论（回复帖子）"""
        try:
            headers = {'Authorization': token, 'Content-Type': 'application/json'}
            post_data = {
                "thread_id": thread_id,
                "content": content
            }
            response = self.session.post(self.create_post_url, json=post_data, headers=headers, timeout=15)
            if response.status_code == 200:
                result = response.json()
                if result.get('success') is True:
                    print(f"[成功] 评论发布成功！帖子ID: {thread_id}")
                    return True
                else:
                    print(f"[失败] 评论发布失败: {result.get('message')}")
                    return False
            else:
                print(f"[错误] 评论发布HTTP错误: {response.status_code}")
                return False
        except Exception as e:
            print(f"[异常] 评论发布异常: {e}")
            return False

    def reply_to_comment(self, token, post_id, content, comment_post_id=None):
        """回复某条评论（支持嵌套回复）"""
        try:
            headers = {'Authorization': token, 'Content-Type': 'application/json'}
            data = {"post_id": post_id, "content": content}
            if comment_post_id:
                data["comment_post_id"] = comment_post_id
            response = self.session.post(self.create_comment_reply_url, json=data, headers=headers, timeout=15)
            if response.status_code == 200:
                result = response.json()
                if result.get('success') is True:
                    print(f"[成功] 回复评论成功！评论ID: {post_id}")
                    return True
                else:
                    print(f"[失败] 回复评论失败: {result.get('message')}")
                    return False
            else:
                print(f"[错误] 回复评论HTTP错误: {response.status_code}")
                return False
        except Exception as e:
            print(f"[异常] 回复评论异常: {e}")
            return False

    def delete_thread(self, token, thread_id):
        """删除帖子"""
        try:
            headers = {'Authorization': token}
            url = f"{self.api_base}/threads/{thread_id}"
            response = self.session.delete(url, headers=headers, timeout=15)
            return response.status_code == 200
        except Exception as e:
            print(f"[异常] 删除帖子失败: {e}")
            return False

    def batch_delete_threads(self, token, thread_ids):
        """批量删除帖子"""
        try:
            headers = {'Authorization': token, 'Content-Type': 'application/json'}
            data = {"thread_ids": thread_ids}
            response = self.session.post(self.batch_delete_threads_url, json=data, headers=headers, timeout=15)
            return response.status_code == 200
        except Exception as e:
            print(f"[异常] 批量删除帖子失败: {e}")
            return False

    def set_essence(self, token, thread_id, is_essence=True):
        """设置/取消精华"""
        try:
            headers = {'Authorization': token, 'Content-Type': 'application/json'}
            data = {"thread_id": thread_id, "is_essence": is_essence}
            response = self.session.post(self.set_essence_url, json=data, headers=headers, timeout=15)
            return response.status_code == 200
        except Exception as e:
            print(f"[异常] 设置精华失败: {e}")
            return False

    def set_sticky(self, token, thread_id, is_sticky=True):
        """设置/取消置顶"""
        try:
            headers = {'Authorization': token, 'Content-Type': 'application/json'}
            data = {"thread_id": thread_id, "is_sticky": is_sticky}
            response = self.session.post(self.set_sticky_url, json=data, headers=headers, timeout=15)
            return response.status_code == 200
        except Exception as e:
            print(f"[异常] 设置置顶失败: {e}")
            return False

    def set_approved(self, token, thread_id, is_approved=True):
        """审核帖子"""
        try:
            headers = {'Authorization': token, 'Content-Type': 'application/json'}
            data = {"thread_id": thread_id, "is_approved": is_approved}
            response = self.session.post(self.set_approved_url, json=data, headers=headers, timeout=15)
            return response.status_code == 200
        except Exception as e:
            print(f"[异常] 审核帖子失败: {e}")
            return False

    def set_thread_like(self, token, thread_id, like=True):
        """点赞/取消点赞帖子"""
        try:
            headers = {'Authorization': token, 'Content-Type': 'application/json'}
            data = {"thread_id": thread_id, "is_like": like}
            response = self.session.post(self.set_thread_like_url, json=data, headers=headers, timeout=15)
            return response.status_code == 200
        except Exception as e:
            print(f"[异常] 点赞帖子失败: {e}")
            return False

    def set_post_like(self, token, post_id, like=True):
        """点赞/取消点赞评论"""
        try:
            headers = {'Authorization': token, 'Content-Type': 'application/json'}
            data = {"post_id": post_id, "is_like": like}
            response = self.session.post(self.set_post_like_url, json=data, headers=headers, timeout=15)
            return response.status_code == 200
        except Exception as e:
            print(f"[异常] 点赞评论失败: {e}")
            return False

    def delete_comment(self, token, comment_id):
        """删除评论"""
        try:
            headers = {'Authorization': token}
            url = f"{self.api_base}/posts/{comment_id}"
            response = self.session.delete(url, headers=headers, timeout=15)
            return response.status_code == 200
        except Exception as e:
            print(f"[异常] 删除评论失败: {e}")
            return False

    def batch_delete_comments(self, token, comment_ids):
        """批量删除评论"""
        try:
            headers = {'Authorization': token, 'Content-Type': 'application/json'}
            data = {"post_ids": comment_ids}
            response = self.session.post(self.batch_delete_posts_url, json=data, headers=headers, timeout=15)
            return response.status_code == 200
        except Exception as e:
            print(f"[异常] 批量删除评论失败: {e}")
            return False

    def get_user_list(self, token, page=1, page_size=20, search=""):
        """获取用户列表（管理员）"""
        try:
            headers = {'Authorization': token}
            params = {"page": page, "page_size": page_size}
            if search:
                params["search"] = search
            response = self.session.get(self.user_list_url, headers=headers, params=params, timeout=15)
            if response.status_code == 200:
                result = response.json()
                if result.get('success') is True:
                    return result.get('data', [])
                else:
                    print(f"[失败] 获取用户列表失败: {result.get('message')}")
                    return []
            else:
                print(f"[错误] 获取用户列表HTTP错误: {response.status_code}")
                return []
        except Exception as e:
            print(f"[异常] 获取用户列表异常: {e}")
            return []

    def get_notifications(self, token):
        """获取通知（预留，暂未实现）"""
        print("[消息] get_notifications 功能暂未实现，返回空列表")
        return []
