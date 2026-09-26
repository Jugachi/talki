import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY,
    created_at REAL NOT NULL,
    mode TEXT NOT NULL DEFAULT 'dictation',
    status TEXT NOT NULL DEFAULT 'ok',
    raw TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    app TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'other',
    language TEXT NOT NULL DEFAULT '',
    duration REAL NOT NULL DEFAULT 0,
    speech_seconds REAL NOT NULL DEFAULT 0,
    words INTEGER NOT NULL DEFAULT 0,
    audio_path TEXT,
    flagged INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS transcripts_created ON transcripts(created_at);
CREATE TABLE IF NOT EXISTS dictionary (
    id INTEGER PRIMARY KEY,
    word TEXT NOT NULL UNIQUE COLLATE NOCASE,
    wrong TEXT,
    starred INTEGER NOT NULL DEFAULT 0,
    uses INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS snippets (
    id INTEGER PRIMARY KEY,
    trigger TEXT NOT NULL UNIQUE COLLATE NOCASE,
    expansion TEXT NOT NULL,
    uses INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS note_versions (
    id INTEGER PRIMARY KEY,
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

NOTE_VERSIONS_KEPT = 50


@dataclass
class DictEntry:
    id: int
    word: str
    wrong: str | None
    starred: bool
    uses: int
    source: str


@dataclass
class Snippet:
    id: int
    trigger: str
    expansion: str
    uses: int


class Store:
    def __init__(self, path: Path = DB_PATH):
        ensure_dirs()
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)

    def _q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def _x(self, sql: str, args=()) -> int:
        with self._lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # transcripts
    def add_transcript(self, **fields) -> int:
        fields.setdefault("created_at", time.time())
        cols = ",".join(fields)
        marks = ",".join("?" * len(fields))
        return self._x(f"INSERT INTO transcripts ({cols}) VALUES ({marks})", tuple(fields.values()))

    def update_transcript(self, tid: int, **fields) -> None:
        sets = ",".join(f"{k}=?" for k in fields)
        self._x(f"UPDATE transcripts SET {sets} WHERE id=?", (*fields.values(), tid))

    def transcript(self, tid: int) -> sqlite3.Row | None:
        rows = self._q("SELECT * FROM transcripts WHERE id=?", (tid,))
        return rows[0] if rows else None

    def transcripts(self, search: str = "", limit: int = 500) -> list[sqlite3.Row]:
        if search:
            like = f"%{search}%"
            return self._q(
                "SELECT * FROM transcripts WHERE text LIKE ? OR raw LIKE ? OR app LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (like, like, like, limit),
            )
        return self._q("SELECT * FROM transcripts ORDER BY created_at DESC LIMIT ?", (limit,))

    def last_transcript(self) -> sqlite3.Row | None:
        rows = self._q(
            "SELECT * FROM transcripts WHERE status='ok' AND text != '' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        return rows[0] if rows else None

    def delete_transcript(self, tid: int) -> None:
        row = self.transcript(tid)
        if row and row["audio_path"]:
            Path(row["audio_path"]).unlink(missing_ok=True)
        self._x("DELETE FROM transcripts WHERE id=?", (tid,))

    def clear_history(self) -> None:
        for row in self._q("SELECT audio_path FROM transcripts WHERE audio_path IS NOT NULL"):
            Path(row["audio_path"]).unlink(missing_ok=True)
        self._x("DELETE FROM transcripts")

    def prune(self, audio_days: int, history_days: int) -> None:
        now = time.time()
        if audio_days > 0:
            cutoff = now - audio_days * 86400
            for row in self._q(
                "SELECT id, audio_path FROM transcripts WHERE audio_path IS NOT NULL AND created_at < ?",
                (cutoff,),
            ):
                Path(row["audio_path"]).unlink(missing_ok=True)
                self._x("UPDATE transcripts SET audio_path=NULL WHERE id=?", (row["id"],))
        if history_days > 0:
            cutoff = now - history_days * 86400
            for row in self._q("SELECT id FROM transcripts WHERE created_at < ?", (cutoff,)):
                self.delete_transcript(row["id"])

    def stats_rows(self) -> list[sqlite3.Row]:
        return self._q(
            "SELECT created_at, words, duration, speech_seconds, category, app, mode FROM transcripts "
            "WHERE status='ok' ORDER BY created_at"
        )

    # dictionary
    def dictionary(self) -> list[DictEntry]:
        return [
            DictEntry(r["id"], r["word"], r["wrong"], bool(r["starred"]), r["uses"], r["source"])
            for r in self._q("SELECT * FROM dictionary ORDER BY starred DESC, uses DESC, word")
        ]

    def add_word(self, word: str, wrong: str | None = None, source: str = "manual") -> None:
        word = word.strip()[:60]
        wrong = (wrong or "").strip()[:60] or None
        if not word:
            return
        self._x(
            "INSERT INTO dictionary (word, wrong, source, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(word) DO UPDATE SET wrong=COALESCE(excluded.wrong, wrong)",
            (word, wrong, source, time.time()),
        )

    def update_word(self, wid: int, word: str, wrong: str | None, starred: bool) -> None:
        self._x(
            "UPDATE dictionary SET word=?, wrong=?, starred=? WHERE id=?",
            (word.strip()[:60], (wrong or "").strip()[:60] or None, int(starred), wid),
        )

    def delete_word(self, wid: int) -> None:
        self._x("DELETE FROM dictionary WHERE id=?", (wid,))

    def bump_words(self, words: list[str]) -> None:
        for w in words:
            self._x("UPDATE dictionary SET uses=uses+1 WHERE word=?", (w,))

    # snippets
    def snippets(self) -> list[Snippet]:
        return [
            Snippet(r["id"], r["trigger"], r["expansion"], r["uses"])
            for r in self._q("SELECT * FROM snippets ORDER BY uses DESC, trigger")
        ]

    def add_snippet(self, trigger: str, expansion: str) -> None:
        self._x(
            "INSERT INTO snippets (trigger, expansion, created_at) VALUES (?,?,?) "
            "ON CONFLICT(trigger) DO UPDATE SET expansion=excluded.expansion",
            (trigger.strip()[:60], expansion[:4000], time.time()),
        )

    def update_snippet(self, sid: int, trigger: str, expansion: str) -> None:
        self._x(
            "UPDATE snippets SET trigger=?, expansion=? WHERE id=?",
            (trigger.strip()[:60], expansion[:4000], sid),
        )

    def delete_snippet(self, sid: int) -> None:
        self._x("DELETE FROM snippets WHERE id=?", (sid,))

    def bump_snippet(self, sid: int) -> None:
        self._x("UPDATE snippets SET uses=uses+1 WHERE id=?", (sid,))

    # notes
    def notes(self, search: str = "") -> list[sqlite3.Row]:
        if search:
            like = f"%{search}%"
            return self._q(
                "SELECT * FROM notes WHERE title LIKE ? OR body LIKE ? "
                "ORDER BY pinned DESC, updated_at DESC",
                (like, like),
            )
        return self._q("SELECT * FROM notes ORDER BY pinned DESC, updated_at DESC")

    def note(self, nid: int) -> sqlite3.Row | None:
        rows = self._q("SELECT * FROM notes WHERE id=?", (nid,))
        return rows[0] if rows else None

    def add_note(self, title: str = "", body: str = "") -> int:
        now = time.time()
        return self._x(
            "INSERT INTO notes (title, body, created_at, updated_at) VALUES (?,?,?,?)",
            (title, body, now, now),
        )

    def save_note(self, nid: int, title: str, body: str, snapshot: bool = False) -> None:
        now = time.time()
        with self._lock:
            if snapshot:
                prev = self.note(nid)
                if prev and prev["body"] != body:
                    self.conn.execute(
                        "INSERT INTO note_versions (note_id, body, created_at) VALUES (?,?,?)",
                        (nid, prev["body"], now),
                    )
                    self.conn.execute(
                        "DELETE FROM note_versions WHERE note_id=? AND id NOT IN "
                        "(SELECT id FROM note_versions WHERE note_id=? ORDER BY id DESC LIMIT ?)",
                        (nid, nid, NOTE_VERSIONS_KEPT),
                    )
            self.conn.execute(
                "UPDATE notes SET title=?, body=?, updated_at=? WHERE id=?", (title, body, now, nid)
            )
            self.conn.commit()

    def set_note_pinned(self, nid: int, pinned: bool) -> None:
        self._x("UPDATE notes SET pinned=? WHERE id=?", (int(pinned), nid))

    def delete_note(self, nid: int) -> None:
        self._x("DELETE FROM notes WHERE id=?", (nid,))

    def note_versions(self, nid: int) -> list[sqlite3.Row]:
        return self._q(
            "SELECT * FROM note_versions WHERE note_id=? ORDER BY id DESC", (nid,)
        )
