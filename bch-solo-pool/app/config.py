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

        def setting(name, default):
            # Dashboard settings persist in config.json and override static
            # container environment defaults once the user changes them.
            if name in saved and saved.get(name) not in (None, ""):
                return saved.get(name)
            env = os.getenv(name)
            return env if env not in (None, "") else default

        self.rpc_url = value("BCH_RPC_URL", "http://bitcoind:8332/")
        self.rpc_user = value("BCH_RPC_USER", "bchn")
        self.rpc_password = value("BCH_RPC_PASSWORD", "")
        self.zmq_url = value("BCH_ZMQ_URL", "tcp://bitcoind:28332")
        self.payout_address = value("BCH_PAYOUT_ADDRESS", "")
        self.pool_id = value("POOL_ID", "BCH Solo Pool")
        self.stratum_host = value("STRATUM_HOST", "0.0.0.0")
        self.stratum_port = int(value("STRATUM_PORT", "3334"))
        self.web_host = value("WEB_HOST", "0.0.0.0")
        self.web_port = int(value("WEB_PORT", "8080"))
        self.start_difficulty = float(setting("START_DIFFICULTY", "1000"))
        self.vardiff_enabled = bool(saved["VARDIFF_ENABLED"]) if "VARDIFF_ENABLED" in saved else _bool_env("VARDIFF_ENABLED", True)
        self.vardiff_target_seconds = float(setting("VARDIFF_TARGET_SECONDS", "30"))
        self.vardiff_min = float(setting("VARDIFF_MIN", "0.001"))
        self.vardiff_max = float(setting("VARDIFF_MAX", "65536"))
        if self.vardiff_min <= 0:
            self.vardiff_min = 0.001
        if self.vardiff_max < self.vardiff_min:
            self.vardiff_max = self.vardiff_min
        self.start_difficulty = max(self.vardiff_min, min(self.vardiff_max, self.start_difficulty))
        self.coinbase_message = value("COINBASE_MESSAGE", "BCH Solo Pool")[:60]
        self.db_path = value("DB_PATH", "/data/pool.sqlite3")
        self.log_level = value("LOG_LEVEL", "INFO")

    def _load_saved(self):
        try:
            return json.loads(self.config_path.read_text())
        except (FileNotFoundError, OSError, ValueError):
            return {}



    def save_vardiff_settings(self, enabled, target_seconds, start_difficulty, min_difficulty, max_difficulty):
        enabled = bool(enabled)
        target = float(target_seconds)
        start = float(start_difficulty)
        minimum = float(min_difficulty)
        maximum = float(max_difficulty)
        if target < 5 or target > 600:
            raise ValueError("Vardiff target time must be between 5 and 600 seconds")
        if minimum <= 0 or start <= 0 or maximum <= 0:
            raise ValueError("Difficulty values must be greater than 0")
        if minimum > maximum:
            raise ValueError("Minimum difficulty cannot exceed maximum difficulty")
        if start < minimum or start > maximum:
            raise ValueError("Start difficulty must be between minimum and maximum difficulty")
        saved = self._load_saved()
        saved["VARDIFF_ENABLED"] = enabled
        saved["VARDIFF_TARGET_SECONDS"] = target
        saved["START_DIFFICULTY"] = start
        saved["VARDIFF_MIN"] = minimum
        saved["VARDIFF_MAX"] = maximum
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved, indent=2) + "\n")
        os.replace(tmp, self.config_path)
        self.vardiff_enabled = enabled
        self.vardiff_target_seconds = target
        self.start_difficulty = start
        self.vardiff_min = minimum
        self.vardiff_max = maximum
        return enabled, target, start, minimum, maximum

    def save_pool_settings(self, start_difficulty, min_difficulty):
        return self.save_vardiff_settings(
            self.vardiff_enabled,
            self.vardiff_target_seconds,
            start_difficulty,
            min_difficulty,
            self.vardiff_max,
        )[2:4]


        start = float(start_difficulty)
        minimum = float(min_difficulty)
        if minimum <= 0:
            raise ValueError("Minimum difficulty must be greater than 0")
        if start <= 0:
            raise ValueError("Start difficulty must be greater than 0")
        if minimum > self.vardiff_max:
            raise ValueError("Minimum difficulty cannot exceed maximum difficulty")
        if start < minimum:
            raise ValueError("Start difficulty must be greater than or equal to minimum difficulty")
        if start > self.vardiff_max:
            raise ValueError("Start difficulty cannot exceed maximum difficulty")
        saved = self._load_saved()
        saved["START_DIFFICULTY"] = start
        saved["VARDIFF_MIN"] = minimum
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved, indent=2) + "\n")
        os.replace(tmp, self.config_path)
        self.start_difficulty = start
        self.vardiff_min = minimum
        return start, minimum

    @property
    def configured(self):
        return bool(self.payout_address and self.rpc_url and self.rpc_user)

    def save_setup(self, data):
        saved = self._load_saved()
        clean = dict(saved)
        clean.update({
            "BCH_RPC_URL": str(data["BCH_RPC_URL"]).strip(),
            "BCH_RPC_USER": str(data["BCH_RPC_USER"]).strip(),
            "BCH_RPC_PASSWORD": str(data.get("BCH_RPC_PASSWORD", "")).strip() or self.rpc_password,
            "BCH_ZMQ_URL": str(data.get("BCH_ZMQ_URL", "")).strip(),
            "BCH_PAYOUT_ADDRESS": str(data["BCH_PAYOUT_ADDRESS"]).strip(),
        })
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(clean, indent=2) + "\n")
        os.replace(tmp, self.config_path)
        return clean
