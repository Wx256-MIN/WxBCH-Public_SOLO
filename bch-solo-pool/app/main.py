import asyncio
import logging
import threading

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


def zmq_thread(cfg, loop, pool):
    if not cfg.zmq_url:
        return
    try:
        import zmq
        ctx = zmq.Context()
        socket = ctx.socket(zmq.SUB)
        socket.setsockopt(zmq.SUBSCRIBE, b"hashblock")
        socket.connect(cfg.zmq_url)
        log.info("ZMQ connected: %s", cfg.zmq_url)
        while True:
            parts = socket.recv_multipart()
            if parts and parts[0] == b"hashblock":
                asyncio.run_coroutine_threadsafe(pool.refresh_job("zmq-hashblock"), loop)
    except Exception:
        log.exception("ZMQ listener stopped")


async def main_async():
    cfg = Config()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    db = DB(cfg.db_path)
    web = Web(cfg, None, None, db)
    web.start()

    # Connect to the Umbrel BCHN node immediately, even before a payout
    # address has been entered. This lets the dashboard show live node
    # information automatically while the pool remains in setup mode.
    rpc = BCHRPC(cfg.rpc_url, cfg.rpc_user, cfg.rpc_password)
    web.rpc = rpc
    try:
        await asyncio.to_thread(rpc.get_blockchain_info)
        log.info("BCHN RPC connected automatically: %s", cfg.rpc_url)
    except Exception as exc:
        log.error("BCH RPC is not reachable: %s", exc)

    if not cfg.configured:
        log.warning("BCH Solo Pool is in setup mode; enter a payout address to start mining.")
        await asyncio.Event().wait()
        return

    from .address import address_to_script
    try:
        address_to_script(cfg.payout_address)
    except Exception as exc:
        log.error("Invalid BCH_PAYOUT_ADDRESS: %s", exc)
        await asyncio.Event().wait()
        return

    try:
        await asyncio.to_thread(rpc.get_blockchain_info)
    except Exception as exc:
        log.error("BCH RPC is not reachable: %s", exc)
        # Keep the setup page available so the user can correct credentials.
        await asyncio.Event().wait()
        return

    pool = Pool(cfg, rpc, db)
    web.pool = pool
    web.rpc = rpc

    await pool.refresh_job("startup")
    if pool.job is None:
        log.error("Unable to obtain a BCH block template; leaving dashboard available.")
        await asyncio.Event().wait()
        return

    loop = asyncio.get_running_loop()
    web.loop = loop
    if cfg.zmq_url:
        threading.Thread(
            target=zmq_thread, args=(cfg, loop, pool), daemon=True
        ).start()

    server = await stratum_server(pool, cfg)
    log.info("Stratum listening on %s:%s", cfg.stratum_host, cfg.stratum_port)
    log.info("Web listening on %s:%s", cfg.web_host, cfg.web_port)
    # Block templates are refreshed by BCHN ZMQ hashblock notifications.
    # Do not poll every few seconds: polling creates unnecessary new jobs
    # and can make miners restart work continuously.

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
