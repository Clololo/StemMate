# -*- coding: utf-8 -*-
"""从本地 QQ 音乐数据中「淘」出 EKey。

不依赖具体的数据库表名或文件格式：扫描给定路径里的 SQLite / 文本 / 二进制
内容，收集形似 EKey 的 base64 串，再用目标文件**实际试解密**验证。这样即使
QQ 音乐换了库结构，只要能读到密钥就能命中。

使用场景：musicex / STag 文件不内嵌密钥，用户从客户端数据目录导出密钥后，
本工具自动找出与目标歌曲匹配的那一条。
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from core.qmc import keystore
from core.qmc.ciphers import container_from_magic, make_cipher
from core.qmc.ekey import EKeyError, decrypt_ekey
from core.qmc.footer import Footer, FooterParseError, parse_footer

BASE64_RUN = re.compile(rb"[A-Za-z0-9+/=_-]{48,}")
SQLITE_MAGIC = b"SQLite format 3\x00"
MAX_TEXT_BYTES = 64 * 1024 * 1024      # 纯文本/二进制文件扫描上限
MAX_BINARY_BYTES = 64 * 1024 * 1024
MAX_SQL_ROWS = 50_000
MAX_CELL_CHARS = 16_384

ProgressCallback = Callable[[str, int, int, str], None]


@dataclass(frozen=True)
class Candidate:
    value: str
    source: Path
    context: str = ""
    score: int = 0


@dataclass
class Match:
    target: Path
    ekey: str
    source: Path
    context: str = ""
    media_mid: str | None = None
    media_filename: str | None = None


@dataclass
class HarvestResult:
    matches: dict[str, Match] = field(default_factory=dict)
    scanned_files: int = 0
    candidate_count: int = 0
    skipped_targets: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return len(self.matches)


def _notify(progress: ProgressCallback | None, stage: str, done: int, total: int, message: str) -> None:
    if progress is not None:
        progress(stage, done, total, message)


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------


def _decode_cell(raw: bytes) -> str:
    if b"\x00" in raw[:64] and raw.count(b"\x00") > len(raw) // 4:
        try:
            return raw.decode("utf-16le", "ignore")
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode("utf-8", "ignore")
    except UnicodeDecodeError:
        return raw.decode("latin-1", "ignore")


def _scan_sqlite(path: Path, add: Callable[[str, Path, str], None]) -> None:
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return
    try:
        cursor = conn.cursor()
        try:
            tables = [
                row[0]
                for row in cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
        except sqlite3.Error:
            return

        for table in tables:
            safe_table = str(table).replace('"', '""')
            try:
                rows = cursor.execute(
                    f'SELECT * FROM "{safe_table}" LIMIT {MAX_SQL_ROWS}'
                )
            except sqlite3.Error:
                continue
            try:
                for row in rows:
                    parts: list[str] = []
                    for cell in row:
                        if isinstance(cell, bytes):
                            text = _decode_cell(cell[:MAX_CELL_CHARS])
                        elif cell is None:
                            continue
                        else:
                            text = str(cell)[:MAX_CELL_CHARS]
                        if text:
                            parts.append(text)
                    if not parts:
                        continue
                    context = " | ".join(parts)[:800]
                    for token in BASE64_RUN.finditer(context.encode("latin-1", "ignore")):
                        add(token.group().decode("latin-1"), path, context)
            except sqlite3.Error as exc:
                # 单表读取失败不影响其它表
                continue
    finally:
        conn.close()


def _looks_textual(data: bytes) -> bool:
    sample = data[:4096]
    if not sample:
        return False
    if b"\x00" in sample:
        return False
    printable = sum(1 for byte in sample if 32 <= byte < 127 or byte in (9, 10, 13))
    return printable / len(sample) > 0.85


def _scan_blob(path: Path, data: bytes, add: Callable[[str, Path, str], None]) -> None:
    if _looks_textual(data):
        try:
            text = data.decode("utf-8", "ignore")
        except Exception:
            text = data.decode("latin-1", "ignore")
        for token in BASE64_RUN.finditer(text.encode("latin-1", "ignore")):
            add(token.group().decode("latin-1"), path, path.name)
        return

    for token in BASE64_RUN.finditer(data):
        add(token.group().decode("latin-1"), path, path.name)


def scan_file(path: Path, add: Callable[[str, Path, str], None]) -> None:
    try:
        head = path.open("rb").read(16)
    except OSError:
        return

    if head.startswith(SQLITE_MAGIC):
        _scan_sqlite(path, add)
        return

    try:
        size = path.stat().st_size
    except OSError:
        return
    limit = MAX_TEXT_BYTES if _looks_textual(head) else MAX_BINARY_BYTES
    if size > limit:
        return
    try:
        data = path.read_bytes()
    except OSError:
        return
    _scan_blob(path, data, add)


def _iter_files(paths: list[str | Path], exclude: set[Path]) -> Iterator[Path]:
    seen: set[Path] = set()
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_file():
            if path.resolve() not in exclude:
                yield path
            continue
        if not path.is_dir():
            continue
        for child in path.rglob("*"):
            try:
                if not child.is_file():
                    continue
                if child.resolve() in exclude:
                    continue
                if child.resolve() in seen:
                    continue
                seen.add(child.resolve())
            except OSError:
                continue
            yield child


def collect_candidates(
    paths: list[str | Path],
    targets: list[Path],
    progress: ProgressCallback | None = None,
) -> tuple[list[Candidate], int]:
    exclusion = {path.resolve() for path in targets if path.exists()}

    files = [p for p in _iter_files(paths, exclusion)]
    total = len(files)
    collected: dict[str, Candidate] = {}

    def add(value: str, source: Path, context: str) -> None:
        value = value.strip().strip('"\'')
        # 形如 ``ekey=xxxx`` / ``vkey=xxxx`` 时去掉标签：base64 允许 '='，
        # 正则会把 "ekey=" 一起吃进来。
        if "=" in value:
            head, tail = value.split("=", 1)
            if len(tail) >= 48 and 0 < len(head) <= 24 and head.replace("_", "").isalnum():
                value = tail.strip().strip('"\'')
        if len(value) < 48:
            return
        score = 0
        lowered = context.lower()
        if "ekey" in lowered or "evkey" in lowered:
            score += 20
        for target in targets:
            name = target.name
            if name and name.lower() in lowered:
                score += 100
        if len(value) >= 200:
            score += 5
        existing = collected.get(value)
        if existing is None or score > existing.score:
            collected[value] = Candidate(value=value, source=source, context=context[:300], score=score)

    for index, file_path in enumerate(files, start=1):
        _notify(progress, "scan", index, total, f"扫描 {file_path.name}")
        try:
            scan_file(file_path, add)
        except Exception as exc:  # 单个文件异常不影响整体
            continue

    _notify(progress, "scan", total, total, f"完成扫描：{total} 个文件")
    return sorted(collected.values(), key=lambda item: item.score, reverse=True), total


# ---------------------------------------------------------------------------
# 验证
# ---------------------------------------------------------------------------


def _target_footer(path: Path) -> Footer | None:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - 4096))
            tail = fh.read(4096)
        return parse_footer(tail)
    except (OSError, FooterParseError):
        return None


def _head_bytes(path: Path, length: int = 8192) -> bytes:
    try:
        with path.open("rb") as fh:
            return fh.read(length)
    except OSError:
        return b""


def _matches_target(master_key: bytes, sample: bytes) -> bool:
    try:
        cipher = make_cipher(master_key)
    except ValueError:
        return False
    decrypted = cipher.decrypt(sample, 0)
    return container_from_magic(decrypted) is not None


def harvest(
    paths: list[str | Path],
    targets: list[str | Path],
    progress: ProgressCallback | None = None,
) -> HarvestResult:
    """扫描 ``paths`` 并找出能解开 ``targets`` 的 EKey。"""
    result = HarvestResult()
    target_paths = [Path(t) for t in targets]

    target_samples: dict[Path, tuple[Footer | None, bytes]] = {}
    for target in target_paths:
        if not target.is_file():
            result.errors.append(f"目标文件不存在：{target}")
            continue
        footer = _target_footer(target)
        if footer is not None and footer.has_embedded_key:
            result.skipped_targets.append(f"{target.name}（密钥已内嵌）")
            continue
        if footer is None:
            result.errors.append(f"无法识别尾部格式：{target.name}")
            continue
        target_samples[target] = (footer, _head_bytes(target))
        if not target_samples[target][1]:
            result.errors.append(f"目标文件无法读取：{target.name}")

    if not target_samples:
        return result

    candidates, scanned_files = collect_candidates(paths, target_paths, progress)
    result.candidate_count = len(candidates)
    result.scanned_files = scanned_files

    unresolved = dict(target_samples)
    for index, candidate in enumerate(candidates, start=1):
        if not unresolved:
            break
        _notify(progress, "validate", index, len(candidates), f"验证候选 {index}/{len(candidates)}")
        try:
            master_key = decrypt_ekey(candidate.value)
        except (EKeyError, ValueError):
            continue

        for target in list(unresolved):
            footer, sample = unresolved[target]
            if not sample or not _matches_target(master_key, sample):
                continue
            result.matches[str(target)] = Match(
                target=target,
                ekey=candidate.value,
                source=candidate.source,
                context=candidate.context,
                media_mid=footer.media_mid if footer else None,
                media_filename=footer.media_filename if footer else None,
            )
            del unresolved[target]

    _notify(
        progress,
        "done",
        result.scanned_files,
        result.scanned_files,
        f"匹配 {result.matched_count}/{len(target_samples)} 个目标",
    )
    return result


# ---------------------------------------------------------------------------
# 导出 / 入库
# ---------------------------------------------------------------------------


def export_matches(result: HarvestResult, out_dir: str | Path) -> list[Path]:
    """把命中的 EKey 写成 ``<mid 或文件名>.ekey``，并输出清单 JSON。"""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    manifest = []
    for key, match in result.matches.items():
        stem = match.media_mid or Path(key).stem
        target = directory / f"{stem}.ekey"
        target.write_text(match.ekey, encoding="ascii")
        written.append(target)
        manifest.append(
            {
                "target": str(match.target),
                "media_mid": match.media_mid,
                "media_filename": match.media_filename,
                "source": str(match.source),
                "ekey_file": target.name,
            }
        )

    manifest_path = directory / "ekeys.json"
    manifest_path.write_text(
        json.dumps(
            {"exported_at": datetime.now().isoformat(timespec="seconds"), "items": manifest},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    written.append(manifest_path)
    return written


def store_matches(result: HarvestResult, db_path: str | Path | None = None) -> int:
    """把命中的 EKey 写入程序内置密钥库，返回成功条数。"""
    store = Path(db_path) if db_path else keystore.app_store_path()
    saved = 0
    for key, match in result.matches.items():
        filename = match.media_filename or Path(key).name
        if keystore.save_ekey(
            store,
            match.ekey,
            filename=filename,
            media_mid=match.media_mid,
            source=str(match.source),
        ):
            saved += 1
    return saved
