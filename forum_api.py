import requests

class ForumAPI:
    def __init__(self, token=None, user_id=None, base_url="https://mbbs.zdjl.site/mk48by049.mbbs.cc"):
        self.base_url = base_url
        self.session = requests.Session()
        self.token = token
        self.user_id = user_id
        self._update_headers()

    def _update_headers(self):
        if self.token and self.user_id:
            self.session.headers.update({
                'authorization': self.token,
                'mbbs-domain': 'mk48by049.mbbs.cc',
                'mbbs-userid': str(self.user_id),
                'Content-Type': 'application/json'
            })

    def set_auth(self, token, user_id):
        self.token = token
        self.user_id = user_id
        self._update_headers()

    def get_captcha(self):
        """获取验证码，返回 (captcha_id, svg_data)"""
        resp = self.session.get(f"{self.base_url}/bbs/login/captcha")
        data = resp.json()
        if data.get('success'):
            return data['data']['id'], data['data']['svg']
        return None, None

    def login(self, username, password, captcha_text, captcha_id):
        payload = {
            "username": username,
            "password": password,
            "captcha_text": captcha_text,
            "captcha_id": captcha_id
        }
        resp = self.session.post(f"{self.base_url}/bbs/login", json=payload)
        data = resp.json()
        if data.get('success'):
            user_data = data['data']
            self.set_auth(user_data['token'], user_data['id'])
            return True, user_data
        return False, data.get('message')

    def get_login_user(self):
        resp = self.session.get(f"{self.base_url}/bbs/users/getLoginUser")
        return resp.json().get('data')

    def get_threads(self, category_id, page=0, limit=20, sort="-posted_at"):
        params = {
            'category_id': category_id,
            'is_approved': 1,
            'page_limit': limit,
            'page_offset': page * limit,
            'sort': sort
        }
        resp = self.session.get(f"{self.base_url}/bbs/threads/list", params=params)
        return resp.json().get('data', {}).get('list', [])

    def get_thread_detail(self, thread_id):
        resp = self.session.get(f"{self.base_url}/bbs/threads/{thread_id}")
        return resp.json().get('data')

    def create_thread(self, title, content, category_id):
        payload = {"title": title, "content": content, "category_id": category_id}
        resp = self.session.post(f"{self.base_url}/bbs/threads", json=payload)
        return resp.json().get('success', False)

    def create_post(self, thread_id, content):
        payload = {"thread_id": str(thread_id), "content": content}
        resp = self.session.post(f"{self.base_url}/bbs/posts/create", json=payload)
        return resp.json().get('success', False)

    def set_like(self, thread_id=None, post_id=None, is_like=True):
        if thread_id:
            payload = {"thread_id": str(thread_id), "is_like": is_like}
            resp = self.session.post(f"{self.base_url}/bbs/threads/setLike", json=payload)
        elif post_id:
            payload = {"post_id": str(post_id), "is_like": is_like}
            resp = self.session.post(f"{self.base_url}/bbs/posts/setLike", json=payload)
        else:
            return False
        return resp.json().get('success', False)

    def set_essence(self, thread_id, is_essence=True):
        payload = {"thread_id": str(thread_id), "is_essence": is_essence}
        resp = self.session.post(f"{self.base_url}/bbs/threads/setEssence", json=payload)
        return resp.json().get('success', False)

    def get_posts(self, thread_id, sort="created_at", page=0, limit=20):
        params = {
            'thread_id': thread_id,
            'sort': sort,
            'page_limit': limit,
            'page_offset': page * limit
        }
        resp = self.session.get(f"{self.base_url}/bbs/posts/list", params=params)
        return resp.json().get('data', {}).get('list', [])
