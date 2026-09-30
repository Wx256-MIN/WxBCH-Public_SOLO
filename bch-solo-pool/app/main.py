import asyncio
import logging
import signal
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

    return await asyncio.start_server(handler, cfg.stratum_host, cfg.stratum_port, limit=65536, backlog=128)


def zmq_thread(cfg, loop, pool, stop_event):
    """Keep the BCHN ZMQ subscriber alive and reconnect after outages.

    ZMQ is treated as a fast notification channel, not as the source of truth.
    Every notification still causes the pool to fetch a fresh GBT from BCHN.
    """
    if not cfg.zmq_url:
        return

    import zmq

    ctx = zmq.Context()
    try:
        while not stop_event.is_set():
            socket = None
            try:
                socket = ctx.socket(zmq.SUB)
                socket.setsockopt(zmq.SUBSCRIBE, b"hashblock")
                socket.setsockopt(zmq.RCVTIMEO, 1000)
                socket.setsockopt(zmq.LINGER, 0)
                socket.connect(cfg.zmq_url)
                log.info("ZMQ connected: %s", cfg.zmq_url)

                while not stop_event.is_set():
                    try:
                        parts = socket.recv_multipart()
                    except zmq.Again:
                        continue

                    if parts and parts[0] == b"hashblock":
                        future = asyncio.run_coroutine_threadsafe(
                            pool.refresh_job("zmq-hashblock", only_if_new_block=True),
                            loop
                        )
                        try:
                            future.result(timeout=30)
                        except Exception:
                            log.exception("ZMQ-triggered job refresh failed")
            except Exception:
                if not stop_event.is_set():
                    log.exception("ZMQ listener disconnected; retrying")
            finally:
                if socket is not None:
                    try:
                        socket.close(0)
                    except Exception:
                        pass
            stop_event.wait(5)
    finally:
        try:
            ctx.term()
        except Exception:
            pass


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
                # ZMQ is the fast path, but a periodic GBT refresh protects
                # against missed notifications and keeps transaction/ntime
                # templates fresh. Same-tip refreshes use clean_jobs=false,
                # so miners do not throw away valid work in flight.
                age = time.time() - pool.job.created
                if age >= 30:
                    await pool.refresh_job("template-refresh")
                else:
                    await pool.refresh_job("fallback", only_if_new_block=True)
                delay = 15
        except Exception:
            log.exception("template recovery loop failed")
            delay = 10
        await asyncio.sleep(delay)


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

    stop_event = threading.Event()
    recovery_task = asyncio.create_task(template_recovery_loop(pool))

    zmq_worker = None
    if cfg.zmq_url:
        zmq_worker = threading.Thread(
            target=zmq_thread, args=(cfg, loop, pool, stop_event),
            name="bchn-zmq", daemon=True
        )
        zmq_worker.start()

    server = await stratum_server(pool, cfg)
    log.info("Stratum listening on %s:%s", cfg.stratum_host, cfg.stratum_port)
    log.info("Web listening on %s:%s", cfg.web_host, cfg.web_port)

    shutdown = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown.set)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        await shutdown.wait()
    finally:
        log.info("Shutting down BCH Solo Pool")
        stop_event.set()
        recovery_task.cancel()
        try:
            await recovery_task
        except asyncio.CancelledError:
            pass
        server.close()
        await server.wait_closed()
        if zmq_worker is not None:
            zmq_worker.join(timeout=3)
        for miner in list(pool.miners):
            await miner.close()
        pool.miners.clear()
        try:
            web.stop()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
