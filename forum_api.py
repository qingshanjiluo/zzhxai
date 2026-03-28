# forum_api.py
import requests
import os
import json

class ForumAPI:
    def __init__(self, token=None, user_id=None, base_url="https://mbbs.zdjl.site/mk48by049.mbbs.cc"):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.token = token
        self.user_id = user_id
        self._update_headers()

    def _update_headers(self):
        """更新请求头，包含认证信息"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'mbbs-domain': 'mk48by049.mbbs.cc'
        }
        if self.token and self.user_id:
            headers['authorization'] = self.token
            headers['mbbs-userid'] = str(self.user_id)
        self.session.headers.update(headers)

    def set_auth(self, token, user_id):
        self.token = token
        self.user_id = user_id
        self._update_headers()

    def _request(self, method, endpoint, **kwargs):
        """统一请求方法，处理错误"""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            resp = self.session.request(method, url, **kwargs)
            print(f"[API] {method} {url} -> {resp.status_code}")
            if resp.status_code != 200:
                print(f"[错误] 响应内容: {resp.text[:200]}")
                return None
            # 尝试解析 JSON
            try:
                return resp.json()
            except Exception:
                print(f"[错误] 响应不是 JSON: {resp.text[:200]}")
                return None
        except Exception as e:
            print(f"[API异常] {e}")
            return None

    def get_threads(self, category_id, page=0, limit=20, sort="-posted_at"):
        """获取帖子列表"""
        params = {
            'category_id': category_id,
            'is_approved': 1,
            'page_limit': limit,
            'page_offset': page * limit,
            'sort': sort
        }
        data = self._request('GET', 'bbs/threads/list', params=params)
        if data and data.get('success'):
            return data.get('data', {}).get('list', [])
        return []

    def get_thread_detail(self, thread_id):
        data = self._request('GET', f'bbs/threads/{thread_id}')
        if data and data.get('success'):
            return data.get('data')
        return None

    def get_posts(self, thread_id, sort="created_at", page=0, limit=20):
        params = {
            'thread_id': thread_id,
            'sort': sort,
            'page_limit': limit,
            'page_offset': page * limit
        }
        data = self._request('GET', 'bbs/posts/list', params=params)
        if data and data.get('success'):
            return data.get('data', {}).get('list', [])
        return []

    def get_post_replies(self, post_id, page=0, limit=20):
        params = {
            'post_id': post_id,
            'page_limit': limit,
            'page_offset': page * limit
        }
        data = self._request('GET', 'bbs/posts/listComments', params=params)
        if data and data.get('success'):
            return data.get('data', {}).get('list', [])
        return []

    def create_thread(self, title, content, category_id):
        payload = {"title": title, "content": content, "category_id": category_id}
        data = self._request('POST', 'bbs/threads', json=payload)
        return data.get('success', False) if data else False

    def create_post(self, thread_id, content):
        payload = {"thread_id": str(thread_id), "content": content}
        data = self._request('POST', 'bbs/posts/create', json=payload)
        return data.get('success', False) if data else False

    def create_comment_reply(self, post_id, content, reply_to_post_id=None):
        payload = {"post_id": post_id, "content": content}
        if reply_to_post_id:
            payload["comment_post_id"] = reply_to_post_id
        data = self._request('POST', 'bbs/posts/createComment', json=payload)
        return data.get('success', False) if data else False

    def set_thread_like(self, thread_id, is_like=True):
        payload = {"thread_id": str(thread_id), "is_like": is_like}
        data = self._request('POST', 'bbs/threads/setLike', json=payload)
        return data.get('success', False) if data else False

    def set_post_like(self, post_id, is_like=True):
        payload = {"post_id": str(post_id), "is_like": is_like}
        data = self._request('POST', 'bbs/posts/setLike', json=payload)
        return data.get('success', False) if data else False

    def set_thread_essence(self, thread_id, is_essence=True):
        payload = {"thread_id": str(thread_id), "is_essence": is_essence}
        data = self._request('POST', 'bbs/threads/setEssence', json=payload)
        return data.get('success', False) if data else False
