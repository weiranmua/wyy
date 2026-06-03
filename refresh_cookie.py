import os
import sys
import json
import base64
import hashlib
import logging
import requests
import smtplib
from email.mime.text import MIMEText
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad

# ----------------------------------------------------------------------
# 日志配置
# ----------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("cookie-refresh")

# ----------------------------------------------------------------------
# 网易云加密常量
# ----------------------------------------------------------------------
AES_FIXED_KEY = b"0CoJUm6Qyw8W8jud"
AES_IV = b"0102030405060708"

RSA_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDgzBYYoEzRtoHqOZ3Gn6QFnOFh
KuTqJLqBpA/U9LmBzHwUZ+qLJzT+RzKzTcmxsw/xr+wYcll9NHDfHkB9KZqD4QH6
JLZv1EIZSPA9r3r5nCZQ/LF+bJ9Q2OZ1Kt1Kw/3lLPFmFZP2HpR6PZNA5I5KtF6T
0mE3YvwE+5Z2jR8zvqQIDAQAB
-----END PUBLIC KEY-----"""

# ----------------------------------------------------------------------
# 加密工具函数
# ----------------------------------------------------------------------
def aes_encrypt(text: str, key: bytes, iv: bytes = AES_IV) -> str:
    """AES-128-CBC 加密，返回 base64 字符串"""
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded = pad(text.encode(), AES.block_size)
    return base64.b64encode(cipher.encrypt(padded)).decode()


def rsa_encrypt(data: bytes) -> str:
    """RSA 加密，返回 base64 字符串"""
    key = RSA.import_key(RSA_PUBLIC_KEY)
    cipher = PKCS1_v1_5.new(key)
    return base64.b64encode(cipher.encrypt(data)).decode()


def weapi_encrypt(data: dict) -> dict:
    """
    网易云 weapi 参数加密
    返回 {'params': str, 'encSecKey': str}
    """
    text = json.dumps(data)
    first_enc = aes_encrypt(text, AES_FIXED_KEY)
    second_key = os.urandom(16)
    params = aes_encrypt(first_enc, second_key)
    enc_sec_key = rsa_encrypt(second_key[::-1])
    return {"params": params, "encSecKey": enc_sec_key}


# ----------------------------------------------------------------------
# 网易云 API 请求辅助
# ----------------------------------------------------------------------
class NeteaseSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://music.163.com/",
        })

    def weapi_post(self, url: str, data: dict) -> dict:
        enc = weapi_encrypt(data)
        resp = self.post(
            url,
            data=enc,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json()


# ----------------------------------------------------------------------
# 登录 & 刷新 Token
# ----------------------------------------------------------------------
def login_cellphone(session: NeteaseSession, phone: str,
                    password: str = None, md5_password: str = None) -> dict:
    if md5_password:
        passwd = md5_password
    else:
        passwd = hashlib.md5(password.encode()).hexdigest()

    data = {
        "phone": phone,
        "password": passwd,
        "countrycode": "86",
        "rememberLogin": "true",
    }
    log.info("正在使用手机号登录...")
    session.get("https://music.163.com/", timeout=15)
    resp_json = session.weapi_post("https://music.163.com/weapi/login/cellphone", data)
    log.info("登录响应: %s", resp_json)

    if resp_json.get("code") != 200:
        raise RuntimeError(f"手机登录失败: {resp_json}")

    return session.cookies.get_dict()


def refresh_token(session: NeteaseSession, music_u: str, csrf: str) -> dict:
    session.cookies.set("MUSIC_U", music_u, domain="music.163.com")
    session.cookies.set("__csrf", csrf, domain="music.163.com")
    session.get("https://music.163.com/", timeout=15)

    data = {
        "token": music_u,
        "csrf_token": csrf,
    }
    log.info("正在尝试刷新 Token...")
    resp_json = session.weapi_post(
        "https://music.163.com/weapi/login/token/refresh", data
    )
    log.info("刷新响应: %s", resp_json)

    if resp_json.get("code") != 200:
        raise RuntimeError(f"Token 刷新失败: {resp_json}")

    return session.cookies.get_dict()


# ----------------------------------------------------------------------
# 使用gh命令更新Secret（删除nacl加密，改用gh cli）
# ----------------------------------------------------------------------
def update_secret_gh(secret_name: str, secret_value: str):
    """调用gh命令行更新secret，自动处理加密，无需代码加密"""
    import subprocess
    cmd = ["gh", "secret", "set", secret_name, "--body", secret_value]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        log.info(f"Secret {secret_name} 更新成功")
    else:
        log.error(f"更新{secret_name}失败:{res.stderr}")
        raise Exception(res.stderr)


# ----------------------------------------------------------------------
# 邮件通知（可选）
# ----------------------------------------------------------------------
def send_email(subject: str, content: str):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = os.getenv("SMTP_PORT")
    sender = os.getenv("NOTIFY_EMAIL")
    password = os.getenv("EMAIL_PASSWORD")
    if not all([smtp_server, smtp_port, sender, password]):
        log.info("邮件配置不全，跳过通知")
        return

    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = sender

    try:
        with smtplib.SMTP_SSL(smtp_server, int(smtp_port)) as server:
            server.login(sender, password)
            server.sendmail(sender, [sender], msg.as_string())
        log.info("通知邮件已发送")
    except Exception as exc:
        log.error("邮件发送失败: %s", exc)


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main():
    MUSIC_U = os.getenv("MUSIC_U")
    CSRF = os.getenv("CSRF")
    PHONE = os.getenv("NETEASE_PHONE")
    PASSWORD = os.getenv("NETEASE_PASSWORD")
    MD5_PASSWORD = os.getenv("NETEASE_MD5_PASSWORD")
    GH_TOKEN = os.getenv("GH_TOKEN")

    if not GH_TOKEN:
        log.error("必须提供 GH_TOKEN 环境变量")
        sys.exit(1)

    session = NeteaseSession()
    new_cookies = {}

    # 1. 尝试用旧 Token 刷新
    if MUSIC_U and CSRF:
        try:
            log.info("检测到已有 MUSIC_U / CSRF，优先尝试刷新 Token")
            new_cookies = refresh_token(session, MUSIC_U, CSRF)
        except Exception as exc:
            log.warning("Token 刷新失败: %s，准备回退到密码登录", exc)
            session.cookies.clear()

    # 2. 如果刷新未获得有效 cookie，使用手机号密码登录
    if not new_cookies.get("MUSIC_U"):
        if not PHONE or not (PASSWORD or MD5_PASSWORD):
            log.error("缺少手机号或密码，无法登录")
            sys.exit(1)
        try:
            new_cookies = login_cellphone(
                session, PHONE, PASSWORD, MD5_PASSWORD
            )
        except Exception as exc:
            log.exception("手机登录异常")
            send_email(
                "Cookie 刷新失败",
                f"手机登录或 Token 刷新均失败：{exc}",
            )
            sys.exit(1)

    # 3. 提取最终 MUSIC_U 和 __csrf
    new_music_u = new_cookies.get("MUSIC_U")
    new_csrf = new_cookies.get("__csrf")

    if not new_music_u or not new_csrf:
        log.error("未能获取到有效的 MUSIC_U 或 __csrf，获取到的 cookie: %s", new_cookies)
        sys.exit(1)

    log.info("成功获取新 Cookie")

    # 4. 更新 Secrets
    os.environ["GH_TOKEN"] = GH_TOKEN
    try:
        update_secret_gh("MUSIC_U", new_music_u)
        update_secret_gh("CSRF", new_csrf)
    except Exception as exc:
        log.exception("Secret 更新失败")
        send_email("Cookie 更新失败", f"Secret 更新错误：{exc}")
        sys.exit(1)

    # 5. 成功通知
    send_email(
        "Cookie 刷新成功",
        f"MUSIC_U 和 CSRF 已更新\n{new_music_u[:20]}...",
    )
    log.info("全部流程完成")


if __name__ == "__main__":
    main()
