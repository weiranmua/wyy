import os
import json
import requests
import subprocess

# ========== 配置读取环境变量（不再需要MD5、RSA公钥、AES密钥）==========
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

# ========== 1、优先用原有Cookie刷新 ==========
def refresh_cookie():
    cookies = {"MUSIC_U": MUSIC_U, "__csrf": CSRF}
    session.cookies.update(cookies)
    # 检测cookie有效性
    res = session.get("https://music.163.com/api/v1/user/account", headers=headers)
    data = res.json()
    if data.get("profile"):
        print("✅ Cookie有效，无需重新登录")
        return True, MUSIC_U, CSRF
    print("❌ Cookie失效，开始手机号明文登录")
    return False, "", ""

# ========== 2、明文密码登录（去掉所有加密逻辑）==========
def login_by_phone(phone, pwd):
    url = "https://music.163.com/weapi/login/cellphone"
    # 网易云接口必须传加密参数，但【密码明文写入json，不再RSA加密】
    raw_data = {"phone": phone, "password": pwd, "remember": True}
    # 保留接口外层固定加密（接口强制要求params/encSecKey，仅密码不加密）
    import base64
    from Crypto.Cipher import AES
    import random

    def get_params(data):
        text = json.dumps(data, separators=(',', ':'))
        key = b"0CoJUm6Qyw8W8jud"
        iv = b"0102030405060708"
        random_key = bytes(random.sample(range(48, 123), 16))
        def aes_encrypt(text_str, k, ivv):
            pad_len = 16 - len(text_str.encode()) % 16
            pad_str = text_str + chr(pad_len)*pad_len
            aes = AES.new(k, AES.MODE_CBC, ivv)
            return base64.b64encode(aes.encrypt(pad_str.encode())).decode()
        enc1 = aes_encrypt(text, key, iv)
        enc2 = aes_encrypt(enc1, random_key, iv)
        enc_sec = base64.b64encode(random_key).decode()
        return {"params": enc2, "encSecKey": enc_sec}

    post_data = get_params(raw_data)
    resp = session.post(url, data=post_data, headers=headers)
    login_res = resp.json()
    if login_res.get("code") == 200:
        new_mu = session.cookies.get("MUSIC_U")
        new_csrf = session.cookies.get("__csrf")
        print(f"✅ 登录成功\n新MUSIC_U:{new_mu[:30]}...")
        return new_mu, new_csrf
    else:
        print(f"❌登录失败：{login_res}")
        return None, None

# ========== 3、自动更新Github Secrets ==========
def update_github_secret(new_mu, new_csrf):
    if not GH_TOKEN:
        print("无GH_TOKEN，跳过更新密钥")
        return
    # 更新MUSIC_U
    subprocess.run(["gh", "secret", "set", "MUSIC_U", "-b", new_mu], env=dict(os.environ, GH_TOKEN=GH_TOKEN))
    # 更新CSRF
    subprocess.run(["gh", "secret", "set", "CSRF", "-b", new_csrf], env=dict(os.environ, GH_TOKEN=GH_TOKEN))
    print("✅ Github密钥已自动替换")

# ========== 主运行逻辑 ==========
if __name__ == "__main__":
    valid, mu, csrf = refresh_cookie()
    if not valid:
        if not PHONE or not PASSWORD:
            print("缺少手机号或密码环境变量！")
        else:
            new_mu, new_csrf = login_by_phone(PHONE, PASSWORD)
            if new_mu and new_csrf:
                update_github_secret(new_mu, new_csrf)
