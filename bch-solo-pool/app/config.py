import json
import os
from pathlib import Path


def _env(name, default=None):
    value = os.getenv(name)
    return default if value is None else value


def _bool_env(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    def __init__(self):
        self.config_path = Path(_env("CONFIG_PATH", "/data/config.json"))
        saved = self._load_saved()

        def value(name, default):
            env = os.getenv(name)
            if env not in (None, ""):
                return env
            return saved.get(name, default)

        self.rpc_url = value("BCH_RPC_URL", "http://bchn:28332/")
        self.rpc_user = value("BCH_RPC_USER", "bch")
        self.rpc_password = value("BCH_RPC_PASSWORD", "")
        self.zmq_url = value("BCH_ZMQ_URL", "tcp://bchn:28334")
        self.payout_address = value("BCH_PAYOUT_ADDRESS", "")
        self.pool_id = value("POOL_ID", "BCH Solo Pool")
        self.stratum_host = value("STRATUM_HOST", "0.0.0.0")
        self.stratum_port = int(value("STRATUM_PORT", "3334"))
        self.web_host = value("WEB_HOST", "0.0.0.0")
        self.web_port = int(value("WEB_PORT", "8080"))
        self.start_difficulty = float(value("START_DIFFICULTY", "1"))
        self.vardiff_enabled = _bool_env("VARDIFF_ENABLED", bool(saved.get("VARDIFF_ENABLED", True)))
        self.vardiff_target_seconds = float(value("VARDIFF_TARGET_SECONDS", "30"))
        self.vardiff_min = float(value("VARDIFF_MIN", "0.001"))
        self.vardiff_max = float(value("VARDIFF_MAX", "1000000000"))
        self.coinbase_message = value("COINBASE_MESSAGE", "BCH Solo Pool")[:60]
        self.db_path = value("DB_PATH", "/data/pool.sqlite3")
        self.log_level = value("LOG_LEVEL", "INFO")

    def _load_saved(self):
        try:
            return json.loads(self.config_path.read_text())
        except (FileNotFoundError, OSError, ValueError):
            return {}

    @property
    def configured(self):
        return bool(self.payout_address and self.rpc_url and self.rpc_user)

    def save_setup(self, data):
        clean = {
            "BCH_RPC_URL": str(data["BCH_RPC_URL"]).strip(),
            "BCH_RPC_USER": str(data["BCH_RPC_USER"]).strip(),
            "BCH_RPC_PASSWORD": str(data.get("BCH_RPC_PASSWORD", "")),
            "BCH_ZMQ_URL": str(data.get("BCH_ZMQ_URL", "")).strip(),
            "BCH_PAYOUT_ADDRESS": str(data["BCH_PAYOUT_ADDRESS"]).strip(),
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(clean, indent=2) + "\n")
        os.replace(tmp, self.config_path)
        return clean
