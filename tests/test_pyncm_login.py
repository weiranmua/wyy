import time
import json
import os
import qrcode
from pyncm import GetCurrentSession
from pyncm.apis.login import LoginQRSessionKey, LoginQRCheck

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "setting.json")

def update_local_config(music_u, csrf):
    """自动将新的 Cookie 写回 setting.json"""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["Cookie_MUSIC_U"] = music_u
            data["Cookie___csrf"] = csrf
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            print(">>> [成功] 已自动将新 Cookie 写入 config/setting.json")
        except Exception as e:
            print(f">>> [警告] 写入配置文件失败: {e}")

def qr_login():
    """扫码登录主逻辑"""
    print("正在请求网易云登录二维码...")
    key_res = LoginQRSessionKey()
    unikey = key_res.get("unikey")
    if not unikey:
        print("获取 unikey 失败:", key_res)
        return None, None

    login_url = f"https://music.163.com/login?codekey={unikey}"
    
    # 终端直接打印二维码（无需图形界面）
    qr = qrcode.QRCode(border=1)
    qr.add_data(login_url)
    print("\n请使用网易云音乐 APP 扫描下方二维码登录：")
    qr.print_ascii(invert=True)
    print(f"二维码链接备用（可复制到浏览器生成）: {login_url}\n")

    # 轮询状态
    while True:
        res = LoginQRCheck(unikey)
        code = res.get("code")

        if code == 800:
            print("二维码已过期，请重新运行！")
            return None, None
        elif code == 801:
            pass  # 等待扫码
        elif code == 802:
            print("已扫码，请在手机端点击【确认登录】...")
        elif code == 803:
            print(">>> 登录成功，正在提取 Cookie...")
            session = GetCurrentSession()
            cookies = session.cookies.get_dict()
            
            music_u = cookies.get("MUSIC_U", "")
            csrf = cookies.get("__csrf", "")

            # 如果 Session 里没取到，从返回文本中提取
            if not music_u and "cookie" in res:
                for c in res["cookie"].split(";"):
                    if "MUSIC_U=" in c:
                        music_u = c.split("=")[1].strip()
                    if "__csrf=" in c:
                        csrf = c.split("=")[1].strip()

            update_local_config(music_u, csrf)
            return music_u, csrf

        time.sleep(2)

if __name__ == "__main__":
    qr_login()
