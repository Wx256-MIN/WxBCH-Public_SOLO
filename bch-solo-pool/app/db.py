import sqlite3
import threading
import time

class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._init()

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
              best_diff REAL NOT NULL DEFAULT 0
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
            """)

    def touch_worker(self, worker, difficulty):
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute("""
              INSERT INTO workers(worker,first_seen,last_seen,difficulty)
              VALUES(?,?,?,?)
              ON CONFLICT(worker) DO UPDATE SET last_seen=excluded.last_seen,difficulty=excluded.difficulty
            """, (worker, now, now, difficulty))

    def share(self, worker, accepted, best_diff=0.0, hashrate=0.0):
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute("""
              INSERT INTO workers(worker,first_seen,last_seen,shares,rejected,hashrate,difficulty,best_diff)
              VALUES(?,?,?, ?, ?, ?, 1, ?)
              ON CONFLICT(worker) DO UPDATE SET
                last_seen=excluded.last_seen,
                shares=workers.shares+excluded.shares,
                rejected=workers.rejected+excluded.rejected,
                hashrate=CASE WHEN excluded.hashrate>0 THEN excluded.hashrate ELSE workers.hashrate END,
                best_diff=MAX(workers.best_diff, excluded.best_diff)
            """, (worker, now, now, 1 if accepted else 0, 0 if accepted else 1, hashrate, best_diff))

    def event(self, kind, worker="", detail=""):
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO events(time,kind,worker,detail) VALUES(?,?,?,?)",
                              (time.time(),kind,worker,detail))

    def block(self, height, job_id, worker, h, result):
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO blocks(time,height,job_id,worker,hash,result) VALUES(?,?,?,?,?,?)",
                              (time.time(),height,job_id,worker,h,result))

    def snapshot(self):
        with self.lock:
            workers = [dict(x) for x in self.conn.execute("SELECT * FROM workers ORDER BY last_seen DESC")]
            blocks = [dict(x) for x in self.conn.execute("SELECT * FROM blocks ORDER BY id DESC LIMIT 20")]
            events = [dict(x) for x in self.conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 30")]
        return workers, blocks, events
