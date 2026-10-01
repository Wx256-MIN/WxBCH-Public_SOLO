import sqlite3
import threading
import time


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.conn:
            self.conn.execute("PRAGMA journal_mode=WAL")
            # FULL is slightly more conservative than NORMAL and is preferable
            # for a solo pool because the share/block history is operational data.
            self.conn.execute("PRAGMA synchronous=FULL")
            self.conn.execute("PRAGMA busy_timeout=30000")
            self.conn.execute("PRAGMA foreign_keys=ON")
        self._init()

    def close(self):
        with self.lock:
            self.conn.close()

    def _migrate(self):
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(workers)")}
        if "connected" not in cols:
            self.conn.execute("ALTER TABLE workers ADD COLUMN connected INTEGER NOT NULL DEFAULT 0")
        if "rejected_diff" not in cols:
            self.conn.execute("ALTER TABLE workers ADD COLUMN rejected_diff REAL NOT NULL DEFAULT 0")

    def _init(self):
        with self.lock, self.conn:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS workers(
              worker TEXT PRIMARY KEY,
              first_seen REAL NOT NULL,
              last_seen REAL NOT NULL,
              shares INTEGER NOT NULL DEFAULT 0,
              rejected INTEGER NOT NULL DEFAULT 0,
              hashrate REAL NOT NULL DEFAULT 0,
              difficulty REAL NOT NULL DEFAULT 1,
              best_diff REAL NOT NULL DEFAULT 0,
              rejected_diff REAL NOT NULL DEFAULT 0,
              connected INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS blocks(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              time REAL NOT NULL,
              height INTEGER,
              job_id TEXT,
              worker TEXT,
              hash TEXT,
              result TEXT
            );
            CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              time REAL NOT NULL,
              kind TEXT NOT NULL,
              worker TEXT,
              detail TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_workers_last_seen ON workers(last_seen);
            CREATE INDEX IF NOT EXISTS idx_blocks_time ON blocks(time DESC);
            CREATE INDEX IF NOT EXISTS idx_events_time ON events(time DESC);
            """)
            self._migrate()

    def touch_worker(self, worker, difficulty, connected=None):
        now = time.time()
        with self.lock, self.conn:
            if connected is None:
                self.conn.execute("""
                  INSERT INTO workers(worker,first_seen,last_seen,difficulty)
                  VALUES(?,?,?,?)
                  ON CONFLICT(worker) DO UPDATE SET
                    last_seen=excluded.last_seen,difficulty=excluded.difficulty
                """, (worker, now, now, difficulty))
            else:
                self.conn.execute("""
                  INSERT INTO workers(worker,first_seen,last_seen,difficulty,connected)
                  VALUES(?,?,?,?,?)
                  ON CONFLICT(worker) DO UPDATE SET
                    last_seen=excluded.last_seen,difficulty=excluded.difficulty,
                    connected=excluded.connected
                """, (worker, now, now, difficulty, int(bool(connected))))

    def set_worker_connected(self, worker, connected):
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE workers SET connected=?, last_seen=? WHERE worker=?",
                (int(bool(connected)), now, worker)
            )

    def share(self, worker, accepted, best_diff=0.0, hashrate=0.0, difficulty=1.0,
              rejected_diff=0.0):
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute("""
              INSERT INTO workers(worker,first_seen,last_seen,shares,rejected,hashrate,difficulty,best_diff,rejected_diff)
              VALUES(?,?,?,?,?,?,?,?,?)
              ON CONFLICT(worker) DO UPDATE SET
                last_seen=excluded.last_seen,
                shares=workers.shares+excluded.shares,
                rejected=workers.rejected+excluded.rejected,
                hashrate=CASE WHEN excluded.hashrate>0 THEN excluded.hashrate ELSE workers.hashrate END,
                difficulty=excluded.difficulty,
                best_diff=MAX(workers.best_diff, excluded.best_diff),
                rejected_diff=CASE
                  WHEN excluded.rejected>0 THEN excluded.rejected_diff
                  ELSE workers.rejected_diff
                END
            """, (
                worker, now, now, int(accepted), int(not accepted),
                hashrate, difficulty, best_diff, rejected_diff
            ))

    def event(self, kind, worker="", detail=""):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO events(time,kind,worker,detail) VALUES(?,?,?,?)",
                (time.time(), kind, worker, detail)
            )

    def block(self, height, job_id, worker, h, result):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO blocks(time,height,job_id,worker,hash,result) VALUES(?,?,?,?,?,?)",
                (time.time(), height, job_id, worker, h, result)
            )

    def snapshot(self):
        with self.lock:
            workers = [dict(x) for x in self.conn.execute(
                "SELECT * FROM workers WHERE connected=1 ORDER BY last_seen DESC"
            )]
            blocks = [dict(x) for x in self.conn.execute(
                "SELECT * FROM blocks ORDER BY id DESC LIMIT 20"
            )]
            events = [dict(x) for x in self.conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT 30"
            )]
        return workers, blocks, events
