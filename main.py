import os
import time
import uuid
import requests
import qrcode

from src.core.bot import MusicPartnerBot
from src.utils.config import Config
from src.utils.logger import Logger
from src.utils.notification import NotificationService
from src.validators.cookie import CookieValidator


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
    "Accept": "application/json, text/plain, */*"
}


def set_cookie_cross_domain(session: requests.Session, name: str, value: str):
    """确保 Cookie 能够跨网易云各子域名共享生效"""
    session.cookies.set(name, value, domain=".music.163.com", path="/")
    session.cookies.set(name, value)


def wait_and_refresh_by_qr(config: Config, logger: Logger, notifier: NotificationService, session: requests.Session) -> bool:
    """
    通过官方原生接口生成二维码、使用固定 Session 保持会话并提取持久化 Cookie
    """
    try:
        logger.info("正在初始化网络环境并请求网易云登录二维码...")
        
        # 1. 建立具有设备标识的会话
        qr_session = requests.Session()
        qr_session.headers.update(HEADERS)
        # 写入基础指纹，防止异地 Actions 机房直接被风控阻断
        qr_session.cookies.set("os", "pc", domain=".music.163.com")
        qr_session.cookies.set("appver", "8.9.70", domain=".music.163.com")

        # 访问主页预热获取基础 token
        try:
            qr_session.get("https://music.163.com/", timeout=5)
        except Exception:
            pass

        t = int(time.time() * 1000)
        key_url = "https://music.163.com/api/login/qrcode/unikey"
        
        # 必须携带 POST 表单参数获取 unikey
        key_res = qr_session.post(
            key_url,
            data={"type": "1", "_nmclfl": "1", "t": t},
            params={"type": "1", "_nmclfl": "1", "t": t},
            timeout=10
        ).json()
        unikey = key_res.get("unikey")
        
        if not unikey:
            logger.error(f"获取二维码 key 失败: {key_res}")
            return False

        login_url = f"https://music.163.com/login?codekey={unikey}"
        qr_img_url = f"https://api.qrserver.com/v1/create-qr-code/?data={login_url}&size=300x300"

        # 2. 终端打印 ASCII 码
        print("\n" + "=" * 40)
        print("请使用网易云音乐 APP 扫描下方二维码登录：")
        qr = qrcode.QRCode(border=1)
        qr.add_data(login_url)
        qr.print_ascii(invert=True)
        print(f"二维码链接备用: {login_url}")
        print("=" * 40 + "\n")

        # 3. 邮件/推送通知
        try:
            notifier.send_notification(
                "网易云音乐合伙人 - 请扫码更新Cookie",
                f"检测到 Cookie 已失效，请在手机上打开链接或长按扫码登录：\n"
                f"二维码图片: {qr_img_url}\n"
                f"授权链接: {login_url}\n"
                f"请在 3 分钟内完成确认。"
            )
        except Exception as e:
            logger.warning(f"发送扫码通知失败: {e}")

        # 4. 轮询二维码状态（最长等待 180 秒）
        start_time = time.time()
        last_logged_code = None
        check_url = "https://music.163.com/api/login/qrcode/client/login"

        while time.time() - start_time < 180:
            now_t = int(time.time() * 1000)
            
            # 关键修复：URL 与 Form Data 双重传递 key，保证后台状态正常流转
            post_data = {
                "key": unikey,
                "type": "1",
                "_nmclfl": "1"
            }
            query_params = {
                "key": unikey,
                "type": "1",
                "_nmclfl": "1",
                "t": now_t
            }
            
            check_res = qr_session.post(check_url, params=query_params, data=post_data, timeout=10)
            
            try:
                data = check_res.json()
            except Exception:
                time.sleep(2)
                continue

            code = data.get("code")

            # 避免日志刷屏
            if code != last_logged_code:
                last_logged_code = code
                if code == 801:
                    logger.info("⏳ 等待手机端扫码...")
                elif code == 802:
                    logger.info("📱 已扫码成功！请在手机端点击【确认登录】...")

            if code == 800:
                logger.error("❌ 二维码已失效过期。")
                return False
            elif code == 803:
                logger.info("🎉 手机端已确认授权！正在提取并保存最新 Cookie...")
                
                # 优先从 qr_session 和响应头 cookies 中拿
                music_u = qr_session.cookies.get("MUSIC_U") or check_res.cookies.get("MUSIC_U") or ""
                csrf = qr_session.cookies.get("__csrf") or check_res.cookies.get("__csrf") or ""

                # 备用方案：解析返回体里的 raw cookie 字符串
                raw_cookie_str = data.get("cookie", "")
                if not music_u and raw_cookie_str:
                    for item in raw_cookie_str.split(";"):
                        item = item.strip()
                        if item.startswith("MUSIC_U="):
                            music_u = item.split("=", 1)[1]
                        elif item.startswith("__csrf="):
                            csrf = item.split("=", 1)[1]

                if not music_u:
                    logger.error(f"授权成功但未能捕获 MUSIC_U，返回数据: {data}")
                    return False

                if not csrf:
                    csrf = uuid.uuid4().hex

                # 将新 Cookie 注入主程序执行 session
                set_cookie_cross_domain(session, "MUSIC_U", music_u)
                set_cookie_cross_domain(session, "__csrf", csrf)

                # 写回配置并触发 GitHub Secrets 持久化更新
                config.set("Cookie_MUSIC_U", music_u)
                config.set("Cookie___csrf", csrf)

                if hasattr(config, "save"):
                    config.save()
                elif hasattr(config, "save_config"):
                    config.save_config()

                logger.info("✅ Cookie 自动刷新并持久化保存成功！")
                return True

            time.sleep(2)

        logger.error("扫码等待超时（3分钟未扫码确认）。")
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
        
        # 创建会话并注入配置中的 Cookie
        session = requests.Session()
        initial_music_u = config.get("Cookie_MUSIC_U") or ""
        initial_csrf = config.get("Cookie___csrf") or ""
        
        if initial_music_u:
            set_cookie_cross_domain(session, "MUSIC_U", initial_music_u)
        if initial_csrf:
            set_cookie_cross_domain(session, "__csrf", initial_csrf)
        
        # 验证 Cookie 是否有效
        validator = CookieValidator(session, logger)
        is_valid, message = validator.validate()
        
        # 失效自动拉起扫码登录
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
            
            # 二次校验
            is_valid, message = validator.validate()
            if not is_valid:
                logger.error(f"刷新后 Cookie 校验依然未通过: {message}")
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
