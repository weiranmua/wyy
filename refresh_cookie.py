import os
import json
import requests
import subprocess
import random
import base64
import hashlib
from Crypto.Cipher import AES

# 读取环境变量
MUSIC_U = os.getenv("MUSIC_U", "")
CSRF = os.getenv("CSRF", "")
PHONE = os.getenv("NETEASE_PHONE", "")
PASSWORD = os.getenv("NETEASE_PASSWORD", "")
GH_TOKEN = os.getenv("GH_TOKEN", "")

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Referer": "https://music.163.com/"
}
session = requests.Session()

# AES加密工具（网易接口强制参数）
def get_params(data):
    text = json.dumps(data, separators=(',', ':'))
    key = b"0CoJUm6Qyw8W8jud"
    iv = b"0102030405060708"
    random_key = os.urandom(16)
    def aes_encrypt(text_str, k, ivv):
        pad_len = 16 - len(text_str.encode()) % 16
        pad_str = text_str + chr(pad_len)*pad_len
        aes = AES.new(k, AES.MODE_CBC, ivv)
        return base64.b64encode(aes.encrypt(pad_str.encode())).decode()
    enc1 = aes_encrypt(text, key, iv)
    enc2 = aes_encrypt(enc1, random_key, iv)
    enc_sec = base64.b64encode(random_key[::-1]).decode()
    return {"params": enc2, "encSecKey": enc_sec}

# 校验原有Cookie
def refresh_cookie():
    cookies = {"MUSIC_U": MUSIC_U, "__csrf": CSRF}
    session.cookies.update(cookies)
    # 换可用的用户信息接口
    res = session.get("https://music.163.com/api/nuser/account/get", headers=headers, timeout=15)
    print("用户信息接口返回文本：", res.text[:300])
    try:
        data = res.json()
        if data.get("profile"):
            print("✅ Cookie有效，无需重新登录")
            return True, MUSIC_U, CSRF
    except:
        pass
    print("❌ Cookie失效，开始手机号登录")
    return False, "", ""

# 手机号登录
def login_by_phone(phone, pwd):
    url = "https://music.163.com/weapi/login/cellphone"
    # 密码md5
    pwd_md5 = hashlib.md5(pwd.encode()).hexdigest()
    raw_data = {"phone": phone, "password": pwd_md5, "rememberLogin": "true"}
    post_data = get_params(raw_data)
    resp = session.post(url, data=post_data, headers=headers, timeout=15)
    print("登录接口原始返回：", resp.text[:500])
    try:
        login_res = resp.json()
    except Exception as e:
        print("接口非JSON数据，登录失败：", e)
        return None, None
    if login_res.get("code") == 200:
        new_mu = session.cookies.get("MUSIC_U")
        new_csrf = session.cookies.get("__csrf")
        print(f"✅ 登录成功\nMUSIC_U:{new_mu[:30]}...")
        return new_mu, new_csrf
    else:
        print(f"❌登录失败：{login_res}")
        return None, None

# gh更新secrets
def update_github_secret(new_mu, new_csrf):
    if not GH_TOKEN:
        print("无GH_TOKEN，跳过更新")
        return
    subprocess.run(["gh", "secret", "set", "MUSIC_U", "-b", new_mu], env=dict(os.environ, GH_TOKEN=GH_TOKEN))
    subprocess.run(["gh", "secret", "set", "CSRF", "-b", new_csrf], env=dict(os.environ, GH_TOKEN=GH_TOKEN))
    print("✅ Secrets更新完成")

if __name__ == "__main__":
    valid, mu, csrf = refresh_cookie()
    if not valid:
        if not PHONE or not PASSWORD:
            print("⚠️ 缺失手机号/密码")
        else:
            new_mu, new_csrf = login_by_phone(PHONE, PASSWORD)
            if new_mu and new_csrf:
                update_github_secret(new_mu, new_csrf)
