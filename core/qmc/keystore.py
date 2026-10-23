# -*- coding: utf-8 -*-
"""本地 EKey 密钥库查询。

PC 新版 musicex 与安卓 STag 文件不内嵌密钥。若用户手上有 QQ 音乐客户端的
密钥数据库（``player_process_db`` 等 SQLite 文件），可以按文件名或 media mid
直接取回 EKey，全程离线。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

# 已知的表结构：(表名, 文件名/标识列, 密钥列)
KNOWN_TABLES = (
    ("audio_file_ekey_table", "file_path", "ekey"),
    ("EKeyFileInfo", "filePath", "eKey"),
    ("p2p_cache_info_table", "file_id", "ekey"),
)

_ENV_VAR = "STEMMATE_EKEY_DB"
_STORE_ENV = "STEMMATE_KEY_STORE"

STORE_TABLE = "ekeys"
STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ekeys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT,
    media_mid TEXT,
    ekey TEXT NOT NULL,
    source TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ekeys_mid ON ekeys(media_mid);
CREATE INDEX IF NOT EXISTS idx_ekeys_filename ON ekeys(filename);
"""


def app_store_path() -> Path:
    """程序内置密钥库路径（可从环境变量覆盖，便于测试）。"""
    override = os.environ.get(_STORE_ENV)
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return base / "StemMate" / "ekeys.db"


def candidate_names(*names: str | None) -> list[str]:
    """整理用于匹配的文件名候选（含去掉目录后的 basename）。"""
    result: list[str] = []
    for name in names:
        if not name:
            continue
        text = str(name).strip()
        if not text:
            continue
        if text not in result:
            result.append(text)
        base = Path(text).name
        if base and base not in result:
            result.append(base)
    return result


def default_db_paths() -> list[Path]:
    """按环境变量与常见安装位置列出候选密钥库。"""
    paths: list[Path] = []

    env_value = os.environ.get(_ENV_VAR)
    if env_value:
        for part in env_value.split(os.pathsep):
            if part.strip():
                paths.append(Path(part).expanduser())

    paths.extend([
        Path.home() / "player_process_db",
        Path.home() / "Desktop" / "player_process_db",
        Path.home() / "Downloads" / "player_process_db",
    ])
    return paths


def _iter_rows(db_path: Path):
    """逐表产出 (文件标识, ekey)。"""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return
    try:
        cursor = conn.cursor()
        try:
            tables = {
                row[0] for row in cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        except sqlite3.Error:
            return
        for table, name_col, key_col in KNOWN_TABLES:
            if table not in tables:
                continue
            try:
                rows = cursor.execute(
                    f"SELECT {name_col}, {key_col} FROM {table}"
                ).fetchall()
            except sqlite3.Error:
                continue
            for name, ekey in rows:
                if name is None or ekey is None:
                    continue
                yield str(name), str(ekey).strip()
    finally:
        conn.close()


def lookup_ekey(db_path: str | Path, *names: str | None) -> str | None:
    """在密钥库中按名称查找 EKey，未找到返回 ``None``。"""
    path = Path(db_path).expanduser()
    if not path.is_file():
        return None

    candidates = candidate_names(*names)
    if not candidates:
        return None

    candidates_set = set(candidates)
    for record_name, ekey in _iter_rows(path):
        if not ekey:
            continue
        base = Path(record_name).name
        if record_name in candidates_set or base in candidates_set:
            return ekey

    # 星级匹配：文件名里包含 media mid（如 AIM0000<mid>.mflac）。
    for record_name, ekey in _iter_rows(path):
        if not ekey:
            continue
        for candidate in candidates:
            if len(candidate) >= 8 and candidate in record_name:
                return ekey
    return None


def search_default_databases(*names: str | None) -> tuple[str, Path] | None:
    """依次搜索默认密钥库，返回 (ekey, 数据库路径)。"""
    store = app_store_path()
    ekey = lookup_app_store(store, *names)
    if ekey:
        return ekey, store

    for db_path in default_db_paths():
        ekey = lookup_ekey(db_path, *names)
        if ekey:
            return ekey, db_path
    return None


# ---------------------------------------------------------------------------
# 程序内置密钥库
# ---------------------------------------------------------------------------


def _connect_store(path: Path) -> sqlite3.Connection | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.executescript(STORE_SCHEMA)
        return conn
    except sqlite3.Error:
        return None


def save_ekey(
    db_path: str | Path,
    ekey: str,
    *,
    filename: str | None = None,
    media_mid: str | None = None,
    source: str | None = None,
) -> bool:
    """把 EKey 写入内置密钥库；同一首歌的旧记录会被替换。"""
    conn = _connect_store(Path(db_path))
    if conn is None:
        return False
    try:
        if media_mid:
            conn.execute("DELETE FROM ekeys WHERE media_mid = ?", (media_mid,))
        elif filename:
            conn.execute("DELETE FROM ekeys WHERE filename = ?", (filename,))
        conn.execute(
            "INSERT INTO ekeys (filename, media_mid, ekey, source, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                filename,
                media_mid,
                ekey.strip(),
                source,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def lookup_app_store(db_path: str | Path, *names: str | None) -> str | None:
    """在内置密钥库里按文件名或 media mid 查找 EKey。"""
    path = Path(db_path)
    if not path.is_file():
        return None
    candidates = candidate_names(*names)
    if not candidates:
        return None

    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        cursor = conn.cursor()
        for candidate in candidates:
            row = cursor.execute(
                "SELECT ekey FROM ekeys WHERE filename = ? OR media_mid = ? "
                "ORDER BY id DESC LIMIT 1",
                (candidate, candidate),
            ).fetchone()
            if row and row[0]:
                return str(row[0])
        for candidate in candidates:
            if len(candidate) < 8:
                continue
            row = cursor.execute(
                "SELECT ekey FROM ekeys WHERE filename LIKE ? OR media_mid LIKE ? "
                "ORDER BY id DESC LIMIT 1",
                (f"%{candidate}%", f"%{candidate}%"),
            ).fetchone()
            if row and row[0]:
                return str(row[0])
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return None
