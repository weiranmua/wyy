import os
import time
import uuid
import requests
import qrcode
from pyncm import GetCurrentSession
from pyncm.apis.login import LoginQRSessionKey, LoginQRCheck

from src.core.bot import MusicPartnerBot
from src.utils.config import Config
from src.utils.logger import Logger
from src.utils.notification import NotificationService
from src.validators.cookie import CookieValidator


def set_cookie_cross_domain(session: requests.Session, name: str, value: str):
    """确保 Cookie 能够跨网易云各子域名共享"""
    session.cookies.set(name, value, domain=".music.163.com", path="/")
    session.cookies.set(name, value)  # 兼顾根域名请求


def wait_and_refresh_by_qr(config: Config, logger: Logger, notifier: NotificationService, session: requests.Session) -> bool:
    """
    通过二维码扫码重新获取 Cookie 并更新配置
    """
    try:
        logger.info("正在获取网易云登录二维码...")
        qr_key_res = LoginQRSessionKey()
        unikey = qr_key_res.get("unikey")
        if not unikey:
            logger.error(f"获取 unikey 失败: {qr_key_res}")
            return False

        login_url = f"https://music.163.com/login?codekey={unikey}"
        # 生成可供直接扫码识别的二维码图片直链
        qr_img_url = f"https://api.qrserver.com/v1/create-qr-code/?data={login_url}&size=300x300"

        # 1. 终端打印二维码（本地运行可直接扫）
        print("\n" + "=" * 40)
        print("请使用网易云音乐 APP 扫描下方二维码登录：")
        qr = qrcode.QRCode(border=1)
        qr.add_data(login_url)
        qr.print_ascii(invert=True)
        print(f"二维码链接备用: {login_url}")
        print("=" * 40 + "\n")

        # 2. 推送通知（在 Actions/服务器等无界面环境运行时接收）
        try:
            notifier.send_notification(
                "网易云音乐合伙人 - 请扫码更新Cookie",
                f"检测到 Cookie 已失效，请扫码登录刷新：\n"
                f"二维码图片链接: {qr_img_url}\n"
                f"扫码授权地址: {login_url}\n"
                f"请在 3 分钟内完成确认。"
            )
        except Exception as e:
            logger.warning(f"发送扫码通知失败: {e}")

        # 3. 轮询等待扫码状态（最长等待 180 秒）
        start_time = time.time()
        while time.time() - start_time < 180:
            check_res = LoginQRCheck(unikey)
            code = check_res.get("code")

            if code == 800:
                logger.error("二维码已过期，本次更新终止。")
                return False
            elif code == 801:
                pass  # 等待扫码中
            elif code == 802:
                logger.info("已扫码，请在手机端点击【确认登录】...")
            elif code == 803:
                logger.info("🎉 授权成功！正在提取并保存最新 Cookie...")
                
                pyncm_session = GetCurrentSession()
                cookies_dict = pyncm_session.cookies.get_dict()
                
                music_u = cookies_dict.get("MUSIC_U", "")
                csrf = cookies_dict.get("__csrf", "")

                # 备用提取机制：解析返回中的 Set-Cookie 字符串
                if not music_u and "cookie" in check_res:
                    for item in check_res["cookie"].split(";"):
                        item = item.strip()
                        if item.startswith("MUSIC_U="):
                            music_u = item.split("=", 1)[1]
                        elif item.startswith("__csrf="):
                            csrf = item.split("=", 1)[1]

                if not music_u:
                    logger.error("未能成功提取到 MUSIC_U，登录失败！")
                    return False

                # 若未捕获到 __csrf，自动生成 32 位十六进制作为合法 csrf 兜底
                if not csrf:
                    csrf = uuid.uuid4().hex

                # 跨子域写入当前执行 session
                set_cookie_cross_domain(session, "MUSIC_U", music_u)
                set_cookie_cross_domain(session, "__csrf", csrf)

                # 将新的 Cookie 写回 config 实例并持久化
                config.set("Cookie_MUSIC_U", music_u)
                config.set("Cookie___csrf", csrf)

                if hasattr(config, "save"):
                    config.save()
                elif hasattr(config, "save_config"):
                    config.save_config()

                logger.info("✅ Cookie 自动刷新并持久化成功！")
                return True

            time.sleep(2)

        logger.error("扫码等待超时（3分钟未确认）。")
        return False

    except Exception as e:
        logger.error(f"扫码登录过程中发生异常: {str(e)}")
        return False


def main():
    try:
        # 初始化基础组件
        config = Config()
        logger = Logger()
        notifier = NotificationService(config, logger)
        
        # 创建会话并写入 Cookie
        session = requests.Session()
        initial_music_u = config.get("Cookie_MUSIC_U") or ""
        initial_csrf = config.get("Cookie___csrf") or ""
        
        if initial_music_u:
            set_cookie_cross_domain(session, "MUSIC_U", initial_music_u)
        if initial_csrf:
            set_cookie_cross_domain(session, "__csrf", initial_csrf)
        
        # 验证 Cookie
        validator = CookieValidator(session, logger)
        is_valid, message = validator.validate()
        
        # 如果 Cookie 失效，自动启动扫码登录更新流程
        if not is_valid:
            logger.warning(f"Cookie 校验未通过 ({message})，正在拉起自动扫码刷新...")
            success_login = wait_and_refresh_by_qr(config, logger, notifier, session)
            
            if not success_login:
                logger.error("重新扫码登录失败，退出任务。")
                notifier.send_notification(
                    "网易云音乐合伙人 - 扫码登录失败", 
                    f"未能成功更新 Cookie，任务已终止。\n原因: {message}"
                )
                return
            
            # 重新验证刷新后的 Cookie
            is_
