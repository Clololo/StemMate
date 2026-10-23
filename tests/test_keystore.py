# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

from core.decryptor import decrypt_file
from core.qmc.keystore import lookup_ekey
from tests.support import craft_ekey_v1, encrypt_payload, musicex_footer, sample_plaintext


def _build_musicex(path: Path, master: bytes, ekey: str, mid: str = "002ejEdb4KTwBw") -> None:
    plain = sample_plaintext("ogg")
    path.write_bytes(
        encrypt_payload(master, plain)
        + musicex_footer(media_mid=mid, media_filename=f"O4M0000{mid}.mgg")
    )


def test_lookup_by_filename(tmp_path):
    db = tmp_path / "player_process_db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE audio_file_ekey_table (file_path TEXT, ekey TEXT)")
    conn.execute("INSERT INTO audio_file_ekey_table VALUES (?, ?)", ("/sdcard/x/O4M0000abc.mgg", "EK1"))
    conn.commit()
    conn.close()

    assert lookup_ekey(db, "other.mgg", "O4M0000abc.mgg") == "EK1"
    assert lookup_ekey(db, "missing.mgg") is None


def test_lookup_by_media_mid_substring(tmp_path):
    db = tmp_path / "player_process_db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE p2p_cache_info_table (file_id TEXT, ekey TEXT)")
    conn.execute(
        "INSERT INTO p2p_cache_info_table VALUES (?, ?)",
        ("AIM0000002ejEdb4KTwBw.mflac", "MIDKEY"),
    )
    conn.commit()
    conn.close()

    assert lookup_ekey(db, "whatever.mgg", "002ejEdb4KTwBw") == "MIDKEY"


def test_musicex_decrypts_with_keystore(tmp_path):
    master = bytes(range(256))
    ekey = craft_ekey_v1(master)
    source = tmp_path / "歌曲.mgg"
    _build_musicex(source, master, ekey)

    db = tmp_path / "player_process_db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE audio_file_ekey_table (file_path TEXT, ekey TEXT)")
    conn.execute(
        "INSERT INTO audio_file_ekey_table VALUES (?, ?)",
        ("O4M0000002ejEdb4KTwBw.mgg", ekey),
    )
    conn.commit()
    conn.close()

    workdir = tmp_path / "out"
    result = decrypt_file(source, workdir=workdir, ekey_db=db)
    assert result.output.suffix == ".ogg"
    assert result.output.read_bytes() == sample_plaintext("ogg")
