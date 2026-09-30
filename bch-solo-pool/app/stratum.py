
    def live_hashrate(self):
        """Return a short-window effective hashrate for dashboard display."""
        now = time.time()
        cutoff = now - 300.0
        self.share_samples = [(t, d) for t, d in self.share_samples if t >= cutoff]
        if not self.share_samples:
            return 0.0

        # Show an immediate expected hashrate after the first accepted share.
        # With multiple shares, use the rolling observed share rate.
        if len(self.share_samples) < 2:
            if now - self.share_samples[-1][0] > 120:
                return 0.0
            return self.share_samples[-1][1] * (2 ** 32) / max(
                self.pool.cfg.vardiff_target_seconds, 1.0
            )

        elapsed = self.share_samples[-1][0] - self.share_samples[0][0]
        if elapsed < 1.0:
            return self.share_samples[-1][1] * (2 ** 32) / max(
                self.pool.cfg.vardiff_target_seconds, 1.0
            )
        work = sum(d * (2 ** 32) for _, d in self.share_samples[1:])
        rate = work / elapsed
        # A worker that has stopped producing shares is not considered live.
        if now - self.share_samples[-1][0] > 120:
            return 0.0
        return rate

class Pool:
    def __init__(self, cfg, rpc, db):
        self.cfg = cfg
        self.rpc = rpc
        self.db = db
        self.miners = set()
        self.job = None
        self.job_lock = asyncio.Lock()
        self.start_difficulty = cfg.start_difficulty
        self.payout_script = address_to_script(cfg.payout_address)
        self.running = True
        self._seen_shares = set()
        self._seen_order = []
        self._seen_limit = 100000

    async def cleanup_inactive_workers(self):
        """Disconnect authorized miners that have produced no share for 30 minutes."""
        now = time.time()