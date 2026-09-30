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


def zmq_thread(cfg, loop, pool):
    """Keep the BCHN ZMQ subscriber alive and reconnect after outages."""
    if not cfg.zmq_url:
        return

    import zmq

    ctx = zmq.Context()
    while True:
        socket = None
        try:
            socket = ctx.socket(zmq.SUB)
            socket.setsockopt(zmq.SUBSCRIBE, b"hashblock")
            socket.setsockopt(zmq.RCVTIMEO, 5000)
            socket.connect(cfg.zmq_url)
            log.info("ZMQ connected: %s", cfg.zmq_url)

            while True:
                try:
                    parts = socket.recv_multipart()
                except zmq.Again:
                    # Keep the receive loop alive even when BCHN is quiet.
                    continue

                if parts and parts[0] == b"hashblock":
                    future = asyncio.run_coroutine_threadsafe(
                        pool.refresh_job("zmq-hashblock"),
                        loop
                    )
                    try:
                        future.result(timeout=30)
                    except Exception:
                        log.exception("ZMQ-triggered job refresh failed")
        except Exception:
            log.exception("ZMQ listener disconnected; retrying in 5 seconds")
        finally:
            if socket is not None:
                try:
                    socket.close(0)
                except Exception:
                    pass
        time.sleep(5)


async def template_recovery_loop(pool):
    """Recover from startup/RPC/ZMQ outages without restarting the container."""
    while True:
        try:
            # If there is no job, retry quickly so mining can start when BCHN
            # finishes booting or the RPC becomes reachable.
            if pool.job is None:
                await pool.refresh_job("rpc-recovery")
                delay = 10
            else:
                # Safety net for missed ZMQ notifications. Only create a new
                # job when the chain tip actually changed, avoiding needless
                # job churn from normal template polling.
                await pool.refresh_job("fallback", only_if_new_block=True)
                delay = 60
        except Exception:
            log.exception("template recovery loop failed")
            delay = 10
        await asyncio.sleep(delay)


async def inactive_worker_loop(pool):
    while True:
        try:
            await pool.cleanup_inactive_workers()
        except Exception:
            log.exception("inactive worker cleanup failed")
        await asyncio.sleep(60)


async def main_async():
    cfg = Config()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    db = DB(cfg.db_path)
    web = Web(cfg, None, None, db)
    web.start()

    # Keep the RPC client available to the dashboard even while BCHN is
    # starting. The pool itself can now start and retry template creation.
    rpc = BCHRPC(cfg.rpc_url, cfg.rpc_user, cfg.rpc_password)
    web.rpc = rpc

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

    pool = Pool(cfg, rpc, db)
    web.pool = pool
    web.rpc = rpc
    loop = asyncio.get_running_loop()
    web.loop = loop

    # Start recovery immediately. RPC may be temporarily unavailable while
    # the BCHN app is starting or syncing.
    await pool.refresh_job("startup")
    if pool.job is None:
        log.warning("BCHN template unavailable at startup; recovery loop will retry automatically.")

    asyncio.create_task(template_recovery_loop(pool))
    asyncio.create_task(inactive_worker_loop(pool))

    if cfg.zmq_url:
        threading.Thread(
            target=zmq_thread, args=(cfg, loop, pool), daemon=True
        ).start()

    server = await stratum_server(pool, cfg)
    log.info("Stratum listening on %s:%s", cfg.stratum_host, cfg.stratum_port)
    log.info("Web listening on %s:%s", cfg.web_host, cfg.web_port)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
