"""SQLite snapshots: formal revisions are immutable; drafts remain recoverable."""

import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager

from .models import Meeting, now


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meetings (
                    meeting_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    status TEXT NOT NULL, company TEXT NOT NULL, title TEXT NOT NULL,
                    meeting_date TEXT NOT NULL, search_text TEXT NOT NULL,
                    updated_at TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (meeting_id, revision)
                );
                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deleted_meetings (
                    meeting_id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, meeting: Meeting):
        if meeting.status == "confirmed":
            from .review import require_exportable
            require_exportable(meeting)
        payload = meeting.model_dump_json()
        # Validate persisted contract even for caller-created models.
        Meeting.model_validate_json(payload)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM deleted_meetings WHERE meeting_id=?", (meeting.meeting_id,)).fetchone():
                raise ValueError("会议已移入回收站，请先恢复后再编辑。")
            row = db.execute("SELECT status, payload FROM meetings WHERE meeting_id=? AND revision=?",
                             (meeting.meeting_id, meeting.revision)).fetchone()
            if row and row["status"] == "confirmed":
                if Meeting.model_validate_json(row["payload"]).model_dump_json() != payload:
                    raise ValueError("已确认版本不可覆盖，请先创建新的编辑修订。")
                return
            db.execute("INSERT OR REPLACE INTO meetings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                meeting.meeting_id, meeting.revision, meeting.status,
                meeting.metadata.company.value or "", meeting.metadata.title.value or "",
                meeting.metadata.meeting_date.value or "",
                "\n".join([s.text for s in meeting.segments] + [f.statement for f in meeting.facts]),
                meeting.updated_at, payload))
            if meeting.status == "confirmed" and meeting.audio:
                # Audio asset lifecycle is independent of immutable meeting revisions.
                from .audio.assets import AudioAssets
                AudioAssets(self.path.parent).mark_confirmed(meeting.audio.asset_id)

    def load(self, meeting_id: str, revision: int | None = None) -> Meeting:
        with self.connect() as db:
            if db.execute("SELECT 1 FROM deleted_meetings WHERE meeting_id=?", (meeting_id,)).fetchone():
                raise ValueError("会议已移入回收站，请先恢复。")
            if revision is None:
                row = db.execute("SELECT payload FROM meetings WHERE meeting_id=? ORDER BY revision DESC LIMIT 1",
                                 (meeting_id,)).fetchone()
            else:
                row = db.execute("SELECT payload FROM meetings WHERE meeting_id=? AND revision=?",
                                 (meeting_id, revision)).fetchone()
        if row is None:
            raise ValueError("未找到该会议版本。")
        return Meeting.model_validate_json(row["payload"])

    def editable_copy(self, meeting: Meeting) -> Meeting:
        from .review import edit_copy
        result = edit_copy(meeting)
        with self.connect() as db:
            latest = db.execute("SELECT MAX(revision) FROM meetings WHERE meeting_id=?",
                                (meeting.meeting_id,)).fetchone()[0]
        result.revision = max(result.revision, (latest or 0) + 1)
        return result

    def search(self, keyword: str = "", include_drafts: bool = True) -> list[dict]:
        # instr uses literal substrings, so %/_ in user search aren't SQL wildcards.
        status = "" if include_drafts else " AND status='confirmed'"
        with self.connect() as db:
            rows = db.execute(f"""SELECT meeting_id, revision, status, company, title, meeting_date, updated_at
                FROM meetings m WHERE revision=(SELECT MAX(revision) FROM meetings n
                    WHERE n.meeting_id=m.meeting_id{status})
                AND (?='' OR instr(lower(company || title || search_text), lower(?))>0)
                AND NOT EXISTS (SELECT 1 FROM deleted_meetings d WHERE d.meeting_id=m.meeting_id)
                ORDER BY updated_at DESC LIMIT 100""", (keyword, keyword)).fetchall()
        return [dict(row) for row in rows]

    def delete_meeting(self, meeting_id: str):
        """Move all revisions to trash; retain audio under its existing policy."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM meetings WHERE meeting_id=?", (meeting_id,)).fetchone():
                raise ValueError("未找到该会议。")
            db.execute("INSERT OR IGNORE INTO deleted_meetings VALUES (?, ?)", (meeting_id, now()))

    def restore_meeting(self, meeting_id: str):
        with self.connect() as db:
            db.execute("DELETE FROM deleted_meetings WHERE meeting_id=?", (meeting_id,))

    def trash(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT m.meeting_id, m.title, d.deleted_at
                FROM meetings m JOIN deleted_meetings d ON d.meeting_id=m.meeting_id
                WHERE m.revision=(SELECT MAX(revision) FROM meetings n WHERE n.meeting_id=m.meeting_id)
                ORDER BY d.deleted_at DESC""")]

    def versions(self, meeting_id: str) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT revision, status, updated_at FROM meetings WHERE meeting_id=? ORDER BY revision DESC",
                (meeting_id,))]

    def audit(self, event: dict):
        allowed = {"meeting_id", "model", "correction_attempt", "network_attempt", "input_tokens",
                   "output_tokens", "estimated_cny", "price_updated_at", "status", "elapsed_ms"}
        safe = {k: v for k, v in event.items() if k in allowed}
        with self.connect() as db:
            db.execute("INSERT INTO calls (created_at, payload) VALUES (?, ?)",
                       (now(), json.dumps(safe, ensure_ascii=False)))

    def usage(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM calls WHERE substr(created_at,1,7)=?", (now()[:7],)).fetchall()
        return [json.loads(row["payload"]) for row in rows]
