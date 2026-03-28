import requests
import time
import os
import ddddocr
import cairosvg

class ForumLogin:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'mbbs-domain': 'mk48by049.mbbs.cc',
        })
        self.base_url = os.getenv("BASE_URL", "https://mbbs.zdjl.site/mk48by049.mbbs.cc")
        self.api = None  # 将在登录后返回

    def _get_captcha(self):
        try:
            resp = self.session.get(f"{self.base_url}/bbs/login/captcha", timeout=10)
            if resp.status_code != 200:
                return None, None
            data = resp.json()
            if data.get('success'):
                captcha_id = data['data']['id']
                svg_data = data['data']['svg']
                # 识别 SVG 验证码
                png_data = cairosvg.svg2png(bytestring=svg_data.encode('utf-8'))
                ocr = ddddocr.DdddOcr()
                captcha_text = ocr.classification(png_data)
                return captcha_id, captcha_text
        except Exception as e:
            print(f"获取验证码失败: {e}")
        return None, None

    def login(self, username, password, retries=3):
        for attempt in range(retries):
            captcha_id, captcha_text = self._get_captcha()
            if not captcha_id or not captcha_text:
                print(f"获取验证码失败 (尝试 {attempt+1}/{retries})")
                time.sleep(2)
                continue

            payload = {
                "username": username,
                "password": password,
                "captcha_text": captcha_text,
                "captcha_id": captcha_id
            }
            try:
                resp = self.session.post(f"{self.base_url}/bbs/login", json=payload, timeout=15)
                data = resp.json()
                if data.get('success'):
                    user_data = data['data']
                    token = user_data['token']
                    user_id = user_data['id']
                    print(f"✅ 登录成功，用户ID: {user_id}")
                    return True, self.session, token, user_id
                else:
                    print(f"登录失败: {data.get('message')}")
            except Exception as e:
                print(f"登录异常: {e}")
            time.sleep(2)
        return False, None, None, None
