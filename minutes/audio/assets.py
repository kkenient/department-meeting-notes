"""Managed, local-only audio storage with bounded uploads and safe deletion."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import BinaryIO
from uuid import uuid4

from ..models import AudioSource

MAX_BYTES = 500 * 1024 * 1024
EXTENSIONS = {".wav", ".m4a", ".mp3"}
RETENTION = {"7days": "首次正式确认后保留 7 天", "immediate": "首次正式确认后删除", "keep": "持续保留"}


class AudioError(ValueError):
    pass


class AudioAssets:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.directory = self.root / "audio"
        self.directory.mkdir(exist_ok=True)
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS assets (
                asset_id TEXT PRIMARY KEY, relative_path TEXT NOT NULL,
                original_name TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                retention TEXT NOT NULL, confirmed_at TEXT, deleted_at TEXT
            )""")

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.root / "audio-assets.sqlite3", timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def safe_path(self, relative: str) -> Path:
        if not re.fullmatch(r"audio/a_[0-9a-f]{32}\.(wav|m4a|mp3)", relative):
            raise AudioError("音频文件引用无效。")
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or path.parent != self.directory.resolve():
            raise AudioError("音频路径超出本项目数据目录。")
        return path

    def row(self, asset_id: str):
        with self.db() as db:
            row = db.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        if not row:
            raise AudioError("未找到音频记录。")
        return row

    def save_upload(self, stream: BinaryIO, name: str, retention="7days") -> AudioSource:
        original = name.replace("\\", "/").rsplit("/", 1)[-1]
        extension = Path(original).suffix.lower()
        if extension not in EXTENSIONS:
            raise AudioError("仅支持 m4a、mp3、wav 音频。")
        if retention not in RETENTION:
            raise AudioError("音频保留策略无效。")
        asset_id = "a_" + uuid4().hex
        relative = f"audio/{asset_id}{extension}"
        path = self.safe_path(relative)
        digest, size = hashlib.sha256(), 0
        try:
            with path.open("xb") as out:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise AudioError("音频超过 500 MB，请拆分后上传。")
                    digest.update(chunk)
                    out.write(chunk)
            if not size:
                raise AudioError("音频文件为空。")
            source = AudioSource(asset_id=asset_id, original_name=original[:200],
                                 sha256=digest.hexdigest(), size_bytes=size)
            with self.db() as db:
                db.execute("INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
                           (asset_id, relative, source.original_name, source.sha256, size, retention))
            return source
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def playable_path(self, source: AudioSource) -> Path | None:
        row = self.row(source.asset_id)
        if row["sha256"] != source.sha256 or row["size_bytes"] != source.size_bytes:
            raise AudioError("音频记录与会议引用不一致。")
        path = self.safe_path(row["relative_path"])
        return path if not row["deleted_at"] and path.is_file() else None

    def verified_path(self, source: AudioSource) -> Path:
        path = self.playable_path(source)
        if path is None:
            raise AudioError("原音频已删除或丢失，无法转写或回听。")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != source.size_bytes or digest != source.sha256:
            raise AudioError("音频文件发生变化，不能用它替换原始证据。")
        return path

    def mark_confirmed(self, asset_id: str, at: datetime | None = None):
        stamp = (at or datetime.now(timezone.utc)).isoformat()
        with self.db() as db:
            db.execute("UPDATE assets SET confirmed_at=COALESCE(confirmed_at, ?) WHERE asset_id=?", (stamp, asset_id))

    def update_retention(self, asset_id: str, policy: str):
        if policy not in RETENTION:
            raise AudioError("音频保留策略无效。")
        self.row(asset_id)
        with self.db() as db:
            db.execute("UPDATE assets SET retention=? WHERE asset_id=?", (policy, asset_id))

    def delete(self, asset_id: str, at: datetime | None = None):
        row = self.row(asset_id)
        path = self.safe_path(row["relative_path"])
        path.unlink(missing_ok=True)
        with self.db() as db:
            db.execute("UPDATE assets SET deleted_at=? WHERE asset_id=?",
                       ((at or datetime.now(timezone.utc)).isoformat(), asset_id))

    def cleanup_due(self, at: datetime | None = None) -> list[str]:
        current = at or datetime.now(timezone.utc)
        with self.db() as db:
            rows = db.execute("SELECT * FROM assets WHERE confirmed_at IS NOT NULL AND deleted_at IS NULL AND retention!='keep'").fetchall()
        deleted = []
        for row in rows:
            due = datetime.fromisoformat(row["confirmed_at"]) + timedelta(days=7 if row["retention"] == "7days" else 0)
            if current >= due:
                self.delete(row["asset_id"], current)
                deleted.append(row["original_name"])
        return deleted
