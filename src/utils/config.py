import base64
import json
import os
import random
from typing import Any, Dict, Optional
import requests


class Config:
    def __init__(self):
        self.is_env_mode: bool = self._check_env_variables()
        self.config_data: Dict = self._load_config()

    def _load_config(self) -> Dict:
        if self.is_env_mode:
            return self._load_from_env()
        return self._load_from_file()

    def _check_env_variables(self) -> bool:
        required_vars = ["MUSIC_U", "CSRF"]
        return all(os.getenv(var) for var in required_vars)

    def _load_from_env(self) -> Dict:
        config = {}

        # 必需的环境变量
        config["Cookie_MUSIC_U"] = os.getenv("MUSIC_U")
        config["Cookie___csrf"] = os.getenv("CSRF")

        # 可选的环境变量
        if notify_email := os.getenv("NOTIFY_EMAIL"):
            config["notify_email"] = notify_email
        if email_password := os.getenv("EMAIL_PASSWORD"):
            config["email_password"] = email_password
        if smtp_server := os.getenv("SMTP_SERVER"):
            config["smtp_server"] = smtp_server
        if smtp_port := os.getenv("SMTP_PORT"):
            config["smtp_port"] = int(smtp_port)
        if wait_min := os.getenv("WAIT_TIME_MIN"):
            config["wait_time_min"] = float(wait_min)
        if wait_max := os.getenv("WAIT_TIME_MAX"):
            config["wait_time_max"] = float(wait_max)
        if score := os.getenv("SCORE"):
            config["score"] = int(score)
        if full_extra_tasks := os.getenv("FULL_EXTRA_TASKS"):
            config["full_extra_tasks"] = full_extra_tasks.lower() in ("1", "true", "yes")

        # 自动登录相关配置
        if phone := os.getenv("NETEASE_PHONE"):
            config["netease_phone"] = phone
        if password := os.getenv("NETEASE_PASSWORD"):
            config["netease_password"] = password
        if md5_password := os.getenv("NETEASE_MD5_PASSWORD"):
            config["netease_md5_password"] = md5_password

        # GitHub Secrets 自动更新配置
        if gh_token := os.getenv("GH_TOKEN"):
            config["gh_token"] = gh_token
        if gh_repo := os.getenv("GH_REPO"):
            config["gh_repo"] = gh_repo

        config.setdefault("wait_time_min", 15)
        config.setdefault("wait_time_max", 20)
        config.setdefault("score", 3)

        return config

    def _load_from_file(self) -> Dict:
        try:
            config_path = "config/setting.json"
            if not os.path.exists(config_path):
                raise FileNotFoundError(f"配置文件 {config_path} 不存在")

            with open(config_path, "r", encoding="utf-8") as file:
                config = json.loads(file.read())

            self._validate_config(config)
            return config

        except Exception as e:
            raise RuntimeError(f"配置加载失败: {str(e)}")

    def _validate_config(self, config: Dict) -> None:
        required_keys = ["Cookie_MUSIC_U", "Cookie___csrf"]
        for key in required_keys:
            if not config.get(key):
                raise ValueError(f"配置文件中缺少必要的配置项: {key}")

        config.setdefault("wait_time_min", 15)
        config.setdefault("wait_time_max", 20)
        config.setdefault("smtp_server", "smtp.qq.com")
        config.setdefault("smtp_port", 465)
        config.setdefault("score", 3)
        config.setdefault("full_extra_tasks", False)

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项"""
        return self.config_data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """修改或设置配置项"""
        self.config_data[key] = value

    def save(self) -> bool:
        """持久化保存 Cookie"""
        if self.is_env_mode:
            return self._save_to_github_secrets()
        return self._save_to_file()

    def _save_to_file(self) -> bool:
        """本地开发模式：写回 setting.json"""
        try:
            config_path = "config/setting.json"
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, ensure_ascii=False, indent=4)
            print("[Config] 本地 setting.json 已自动更新")
            return True
        except Exception as e:
            print(f"[Config] 写入本地配置文件失败: {e}")
            return False

    def _save_to_github_secrets(self) -> bool:
        """
        Actions 云端模式：独立加密更新 Secrets
        使用局部作用域导入，完全杜绝模块和命名冲突
        """
        gh_token = self.get("gh_token")
        gh_repo = self.get("gh_repo")

        if not gh_token or not gh_repo:
            print("[Config] 未获取到 GH_TOKEN 或 GH_REPO，跳过更新 GitHub Secrets")
            return False

        try:
            # 局部导入并设置别名，不影响任何其他模块
            from nacl import encoding as _nacl_enc
            from nacl import public as _nacl_pub
        except ImportError:
            print("[Config] 运行环境中缺少 pynacl 库，无法加密更新 Secrets")
            return False

        try:
            req_headers = {
                "Authorization": f"token {gh_token}",
                "Accept": "application/vnd.github.v3+json"
            }

            # 1. 独立获取当前仓库用于加密 Secret 的公钥
            pub_key_api = f"https://api.github.com/repos/{gh_repo}/actions/secrets/public-key"
            res = requests.get(pub_key_api, headers=req_headers, timeout=10)
            if res.status_code != 200:
                print(f"[Config] 获取仓库公钥失败: {res.status_code} - {res.text}")
                return False

            key_info = res.json()
            box_key = _nacl_pub.PublicKey(key_info["key"].encode("utf-8"), _nacl_enc.Base64Encoder())
            sealed_box = _nacl_pub.SealedBox(box_key)

            # 2. 需要回写的 Secrets 字典
            secret_items = {
                "MUSIC_U": self.get("Cookie_MUSIC_U", ""),
                "CSRF": self.get("Cookie___csrf", "")
            }

            all_ok = True
            for sec_name, sec_val in secret_items.items():
                if not sec_val:
                    continue

                # 使用 libsodium 规范对 Secret 加密
                encrypted_bytes = sealed_box.encrypt(sec_val.encode("utf-8"))
                encrypted_str = base64.b64encode(encrypted_bytes).decode("utf-8")

                put_api = f"https://api.github.com/repos/{gh_repo}/actions/secrets/{sec_name}"
                payload = {
                    "encrypted_value": encrypted_str,
                    "key_id": key_info["key_id"]
                }

                put_res = requests.put(put_api, headers=req_headers, json=payload, timeout=10)
                if put_res.status_code in (201, 204):
                    print(f"[Config] ✅ 成功独立更新 Secret: {sec_name}")
                else:
                    print(f"[Config] ❌ 更新 Secret {sec_name} 失败: {put_res.text}")
                    all_ok = False

            return all_ok

        except Exception as e:
            print(f"[Config] ❌ 独立更新 GitHub Secrets 出现异常: {e}")
            return False

    def get_wait_time(self) -> float:
        """获取随机等待时间"""
        min_time = float(self.get("wait_time_min", 15))
        max_time = float(self.get("wait_time_max", 20))
        return random.uniform(min_time, max_time)
