from typing import Tuple
import requests
from ..utils.logger import Logger


class CookieValidator:
    def __init__(self, session: requests.Session, logger: Logger):
        self.session = session
        self.logger = logger
        self.check_urls = {
            "user_info": "https://music.163.com/api/nuser/account/get",
            "task_data": "https://interface.music.163.com/api/music/partner/daily/task/get"
        }
        # 补全伪装 Headers，防止被网易云反爬策略拦截报错
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://music.163.com/",
            "Accept": "application/json, text/plain, */*"
        }

    def validate(self) -> Tuple[bool, str]:
        """验证Cookie是否有效"""
        try:
            if not self._check_cookie_exists():
                return False, "Cookie未正确设置（缺少 MUSIC_U 或 __csrf）"

            if not self._check_user_info():
                return False, "Cookie已失效或账号登录态过期"

            if not self._check_task_access():
                return False, "当前账号可能没有音乐合伙人权限"

            return True, "Cookie有效"

        except Exception as e:
            return False, f"Cookie验证失败: {str(e)}"

    def _check_cookie_exists(self) -> bool:
        """检查必要的Cookie是否存在"""
        music_u = self.session.cookies.get("MUSIC_U")
        csrf = self.session.cookies.get("__csrf")
        return bool(music_u and csrf)

    def _check_user_info(self) -> bool:
        """检查用户信息是否有效"""
        try:
            response = self.session.get(
                self.check_urls["user_info"], 
                headers=self.headers, 
                timeout=10
            )
            data = response.json()
            return bool(data.get("code") == 200 and data.get("profile"))
        except Exception as e:
            self.logger.warning(f"获取用户信息校验异常: {e}")
            return False

    def _check_task_access(self) -> bool:
        """检查是否有任务访问权限"""
        try:
            response = self.session.get(
                self.check_urls["task_data"], 
                headers=self.headers, 
                timeout=10
            )
            data = response.json()
            return data.get("code") == 200
        except Exception as e:
            self.logger.warning(f"校验合伙人任务权限异常: {e}")
            return False
