import json
import os
import random
import base64
from typing import Any, Dict, Optional
import requests

try:
    from nacl import encoding, public
    HAS_PYNACL = True
except ImportError:
    HAS_PYNACL = False


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

        # GitHub 相关配置 (用于自动更新 Secrets)
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
        config.setdefault("smtp_server", "smtp.gmail.com")
        config.setdefault("smtp_port", 465)
        config.setdefault("score", 3)
        config.setdefault("full_extra_tasks", False)

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项"""
        return self.config_data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """修改或新增配置项"""
        self.config_data[key] = value

    def save(self) -> bool:
        """
        持久化保存配置：
        1. 本地模式：写回 config/setting.json
        2. GitHub Actions 模式：调用 GitHub API 更新 Secrets
        """
        if self.is_env_mode:
            return self._save_to_github_secrets()
        return self._save_to_file()

    def _save_to_file(self) -> bool:
        """本地文件回写保存"""
        try:
            config_path = "config/setting.json"
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, ensure_ascii=False, indent=4)
            print("[Config] 本地 setting.json 自动更新成功")
            return True
        except Exception as e:
            print(f"[Config] 写入本地配置文件失败: {e}")
            return False

    def _save_to_github_secrets(self) -> bool:
        """更新 GitHub 仓库 Secrets (MUSIC_U, CSRF)"""
        gh_token = self.get("gh_token")
        gh_repo = self.get("gh_repo")

        if not gh_token or not gh_repo:
            print("[Config] 未配置 GH_TOKEN 或 GH_REPO，无法自动同步更新 GitHub Secrets")
            return False

        if not HAS_PYNACL:
            print("[Config] 缺少 PyNaCl 依赖，无法加密更新 GitHub Secrets (请 pip install pynacl)")
            return False

        try:
            headers = {
                "Authorization": f"Bearer {gh_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            # 1. 获取公钥用于加密 Secret
            key_url = f"https://api.github.com/repos/{gh_repo}/actions/secrets/public-key"
            res = requests.get(key_url, headers=headers, timeout=10)
            if res.status_code != 200:
                print(f"[Config] 获取 GitHub Public Key 失败: {res.text}")
                return False

            key_data = res.json()
            public_key = public.PublicKey(key_data["key"].encode("utf-8"), encoding.Base64Encoder())
            sealed_box = public.SealedBox(public_key)

            # 2. 定义待更新的 Secrets
            updates = {
                "MUSIC_U": self.get("Cookie_MUSIC_U", ""),
                "CSRF": self.get("Cookie___csrf", "")
            }

            for secret_name, secret_val in updates.items():
                if not secret_val:
                    continue
                encrypted = sealed_box.encrypt(secret_val.encode("utf-8"))
                encrypted_str = base64.b64encode(encrypted).decode("utf-8")

                put_url = f"https://api.github.com/repos/{gh_repo}/actions/secrets/{secret_name}"
                payload = {
                    "encrypted_value": encrypted_str,
                    "key_id": key_data["key_id"]
                }
                r = requests.put(put_url, headers=headers, json=payload, timeout=10)
                if r.status_code in (201, 204):
                    print(f"[Config] GitHub Secret [{secret_name}] 同步更新成功！")
                else:
                    print(f"[Config] 更新 Secret [{secret_name}] 失败: {r.text}")

            return True
        except Exception as e:
            print(f"[Config] 同步更新 GitHub Secrets 出现异常: {e}")
            return False

    def get_wait_time(self) -> float:
        """获取随机等待时间"""
        min_time = float(self.get("wait_time_min", 15))
        max_time = float(self.get("wait_time_max", 20))
        return random.uniform(min_time, max_time)
