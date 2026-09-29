import asyncio
import logging
import threading
import time

from .config import Config
from .db import DB
from .node import BCHRPC
from .stratum import Pool, Miner
from .web import Web

log = logging.getLogger("bchpool")

async def stratum_server(pool, cfg):
    async def handler(reader, writer):
        peer = writer.get_extra_info("peername")
        log.info("miner connected: %s", peer)
        miner = Miner(reader, writer, pool)
        try:
            await miner.run()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            log.info("miner disconnected: %s", peer)
    return await asyncio.start_server(handler, cfg.stratum_host, cfg.stratum_port)

async def poller(pool):
    while True:
        await asyncio.sleep(2)
        try:
            # Polling is also a recovery path if a ZMQ notification is missed.
            await pool.refresh_job("poll")
        except Exception:
            pass

def zmq_thread(cfg, loop, pool):
    if not cfg.zmq_url:
        return
    try:
        import zmq
        ctx = zmq.Context()
        s = ctx.socket(zmq.SUB)
        s.setsockopt(zmq.SUBSCRIBE, b"hashblock")
        s.connect(cfg.zmq_url)
        log.info("ZMQ connected: %s", cfg.zmq_url)
        while True:
            parts = s.recv_multipart()
            if parts and parts[0] == b"hashblock":
                asyncio.run_coroutine_threadsafe(pool.refresh_job("zmq-hashblock"), loop)
    except Exception:
        log.exception("ZMQ listener stopped")

async def main_async():
    cfg = Config()
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not cfg.payout_address:
        raise SystemExit("BCH_PAYOUT_ADDRESS is required")
    rpc = BCHRPC(cfg.rpc_url, cfg.rpc_user, cfg.rpc_password)
    db = DB(cfg.db_path)
    pool = Pool(cfg, rpc, db)

    await pool.refresh_job("startup")

    web = Web(cfg, pool, rpc, db)
    web.start()

    loop = asyncio.get_running_loop()
    if cfg.zmq_url:
        threading.Thread(target=zmq_thread, args=(cfg,loop,pool), daemon=True).start()

    server = await stratum_server(pool, cfg)
    log.info("Stratum listening on %s:%s", cfg.stratum_host, cfg.stratum_port)
    log.info("Web listening on %s:%s", cfg.web_host, cfg.web_port)
    asyncio.create_task(poller(pool))

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
