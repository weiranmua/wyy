import os
import time
import requests
import qrcode
from pyncm import GetCurrentSession
from pyncm.apis.login import LoginQRSessionKey, LoginQRCheck

from src.core.bot import MusicPartnerBot
from src.utils.config import Config
from src.utils.logger import Logger
from src.utils.notification import NotificationService
from src.validators.cookie import CookieValidator


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
        # 生成可供直接扫码的图片直链（适合推送通知）
        qr_img_url = f"https://api.qrserver.com/v1/create-qr-code/?data={login_url}&size=300x300"

        # 1. 终端打印二维码（本地运行可直接扫）
        print("\n" + "=" * 40)
        print("请使用网易云音乐 APP 扫描下方二维码登录：")
        qr = qrcode.QRCode(border=1)
        qr.add_data(login_url)
        qr.print_ascii(invert=True)
        print(f"二维码链接备用: {login_url}")
        print("=" * 40 + "\n")

        # 2. 推送通知（如果是在服务器/后台运行，可直接点击链接或扫码）
        try:
            notifier.send_notification(
                "网易云音乐合伙人 - 请扫码更新Cookie",
                f"检测到Cookie已失效，请扫码登录刷新：\n"
                f"二维码图片: {qr_img_url}\n"
                f"或直接打开链接: {login_url}"
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
                # 等待扫码
                pass
            elif code == 802:
                logger.info("已扫码，请在手机端点击【确认登录】...")
            elif code == 803:
                logger.info("🎉 授权成功！正在提取并保存最新 Cookie...")
                
                # 提取最新 Cookie
                pyncm_session = GetCurrentSession()
                cookies_dict = pyncm_session.cookies.get_dict()
                
                music_u = cookies_dict.get("MUSIC_U", "")
                csrf = cookies_dict.get("__csrf", "")

                # 备用方案：从接口返回的 cookie 字符串中提取
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

                # 更新当前会话
                session.cookies.set("MUSIC_U", music_u)
                if csrf:
                    session.cookies.set("__csrf", csrf)

                # 将新的 Cookie 写回 config
                config.set("Cookie_MUSIC_U", music_u)
                if csrf:
                    config.set("Cookie___csrf", csrf)

                # 如果 Config 类支持 save/write，则调用持久化
                if hasattr(config, "save"):
                    config.save()
                elif hasattr(config, "save_config"):
                    config.save_config()

                logger.info("✅ Cookie 自动刷新并保存成功！")
                return True

            time.sleep(2)

        logger.error("扫码等待超时（3分钟未扫码）。")
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
        
        # 创建会话并设置Cookie
        session = requests.Session()
        session.cookies.set("MUSIC_U", config.get("Cookie_MUSIC_U"))
        session.cookies.set("__csrf", config.get("Cookie___csrf"))
        
        # 验证Cookie
        validator = CookieValidator(session, logger)
        is_valid, message = validator.validate()
        
        # 如果 Cookie 失效，自动拉起扫码登录
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
            is_valid, message = validator.validate()
            if not is_valid:
                logger.error(f"刷新后 Cookie 依然无效: {message}")
                return
        
        # 运行主程序
        bot = MusicPartnerBot(config, logger, session)
        success = bot.run()
        
        # 处理执行结果
        end_message = "✅ 执行成功" if success else "❌ 执行失败"
        logger.end(end_message, not success)
        
        if not success:
            notifier.send_notification(
                "网易云音乐合伙人 - 执行失败提醒",
                "程序执行失败，请检查日志"
            )
            
    except Exception as e:
        error_message = f"程序异常: {str(e)}"
        logger.error(error_message)
        logger.end("❌ 执行失败", True)
        
        try:
            notifier.send_notification(
                "网易云音乐合伙人 - 异常提醒",
                error_message
            )
        except Exception as notify_error:
            logger.error(f"发送异常通知时出错: {str(notify_error)}")


if __name__ == "__main__":
    main()
