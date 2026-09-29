import os

def env(name, default=None):
    return os.getenv(name, default)

def boolean(name, default=False):
    v = os.getenv(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")

class Config:
    rpc_url = env("BCH_RPC_URL", "http://127.0.0.1:8332/")
    rpc_user = env("BCH_RPC_USER", "")
    rpc_password = env("BCH_RPC_PASSWORD", "")
    zmq_url = env("BCH_ZMQ_URL", "")
    payout_address = env("BCH_PAYOUT_ADDRESS", "")
    pool_id = env("POOL_ID", "BCH Solo Pool")
    stratum_host = env("STRATUM_HOST", "0.0.0.0")
    stratum_port = int(env("STRATUM_PORT", "3334"))
    web_host = env("WEB_HOST", "0.0.0.0")
    web_port = int(env("WEB_PORT", "8080"))
    start_difficulty = float(env("START_DIFFICULTY", "1"))
    vardiff_enabled = boolean("VARDIFF_ENABLED", True)
    vardiff_target_seconds = float(env("VARDIFF_TARGET_SECONDS", "30"))
    vardiff_min = float(env("VARDIFF_MIN", "0.001"))
    vardiff_max = float(env("VARDIFF_MAX", "1000000000"))
    coinbase_message = env("COINBASE_MESSAGE", "BCH Solo Pool")[:60]
    db_path = env("DB_PATH", "/data/pool.sqlite3")
    log_level = env("LOG_LEVEL", "INFO")
