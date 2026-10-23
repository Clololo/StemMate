# -*- coding: utf-8 -*-
import json
import sqlite3

import pytest

import main as app_main
from core.decryptor import decrypt_file
from core.qmc.harvest import export_matches, harvest, store_matches
from core.qmc.keystore import lookup_app_store
from tests.support import (
    craft_ekey_v1,
    encrypt_payload,
    musicex_footer,
    sample_plaintext,
)

MID = "002ejEdb4KTwBw"
FILENAME = "O4M000015Tgb2RIq59.mgg"


def _make_target(tmp_path, master=None, mid=MID, filename=FILENAME):
    master = master or bytes(range(256))
    ekey = craft_ekey_v1(master)
    target = tmp_path / "浮夸_L.mgg"
    target.write_bytes(
        encrypt_payload(master, sample_plaintext("ogg", 8192))
        + musicex_footer(media_mid=mid, media_filename=filename)
    )
    return target, master, ekey


def _make_db(tmp_path, rows, table="audio_file_ekey_table"):
    db = tmp_path / "player_process_db"
    conn = sqlite3.connect(db)
    if table == "audio_file_ekey_table":
        conn.execute("CREATE TABLE audio_file_ekey_table (file_path TEXT, ekey TEXT)")
        conn.executemany("INSERT INTO audio_file_ekey_table VALUES (?, ?)", rows)
    else:
        conn.execute(f"CREATE TABLE {table} (payload TEXT)")
        conn.executemany(f"INSERT INTO {table} VALUES (?)", [(row[1],) for row in rows])
    conn.commit()
    conn.close()
    return db


def test_harvest_from_sqlite(tmp_path):
    target, _master, ekey = _make_target(tmp_path)
    other = craft_ekey_v1(bytes(i & 0xFF for i in range(1, 257)))
    db = _make_db(
        tmp_path,
        [
            ("/sdcard/other.mgg", other),
            (f"/sdcard/{FILENAME}", ekey),
        ],
    )

    result = harvest([db], [target])
    assert result.matched_count == 1
    match = result.matches[str(target)]
    assert match.ekey == ekey
    assert match.source == db
    assert match.media_mid == MID
    assert result.scanned_files == 1
    assert result.candidate_count >= 2


def test_harvest_from_text_and_ini(tmp_path):
    target, _master, ekey = _make_target(tmp_path)
    ini = tmp_path / "QQMusicServiceConfig.ini"
    ini.write_text(f"[Account]\nUin=12345\nekey={ekey}\n", encoding="utf-8")

    result = harvest([ini], [target])
    assert result.matches[str(target)].ekey == ekey


def test_harvest_from_binary_blob(tmp_path):
    target, _master, ekey = _make_target(tmp_path)
    blob = tmp_path / "cache.dat"
    blob.write_bytes(b"\x00\x01\x02" + ekey.encode("ascii") + b"\x00\xff" * 64)

    result = harvest([blob], [target])
    assert result.matches[str(target)].ekey == ekey


def test_harvest_directory_and_skips_wrong_keys(tmp_path):
    target, _master, ekey = _make_target(tmp_path)
    data_dir = tmp_path / "QQMusicData"
    data_dir.mkdir()
    _make_db(data_dir, [("/sdcard/other.mgg", craft_ekey_v1(bytes(i & 0xFF for i in range(2, 258))))])
    (data_dir / "notes.txt").write_text("无关内容 " + "A" * 300, encoding="utf-8")
    (data_dir / "deep").mkdir()
    (data_dir / "deep" / "keys.ini").write_text(f"ekey={ekey}", encoding="utf-8")

    result = harvest([data_dir], [target])
    assert result.matched_count == 1
    assert result.matches[str(target)].ekey == ekey


def test_harvest_without_match(tmp_path):
    target, _master, _ekey = _make_target(tmp_path)
    db = _make_db(tmp_path, [("/sdcard/other.mgg", craft_ekey_v1(bytes(i & 0xFF for i in range(3, 259))))])

    result = harvest([db], [target])
    assert result.matched_count == 0
    assert result.candidate_count >= 1


def test_store_then_decrypt_without_explicit_key(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("STEMMATE_KEY_STORE", raising=False)
    target, master, ekey = _make_target(tmp_path)
    db = _make_db(tmp_path, [(f"/sdcard/{FILENAME}", ekey)])

    result = harvest([db], [target])
    assert store_matches(result) == 1

    from core.qmc import keystore

    store = keystore.app_store_path()
    assert store.is_file()
    assert lookup_app_store(store, MID, FILENAME) == ekey

    output = decrypt_file(target, workdir=tmp_path / "out")
    assert output.output.read_bytes() == sample_plaintext("ogg", 8192)


def test_export_matches(tmp_path):
    target, _master, ekey = _make_target(tmp_path)
    db = _make_db(tmp_path, [(f"/sdcard/{FILENAME}", ekey)])
    result = harvest([db], [target])

    out_dir = tmp_path / "export"
    written = export_matches(result, out_dir)
    key_file = out_dir / f"{MID}.ekey"
    assert key_file.is_file()
    assert key_file.read_text(encoding="ascii") == ekey
    manifest = json.loads((out_dir / "ekeys.json").read_text(encoding="utf-8"))
    assert manifest["items"][0]["media_mid"] == MID
    assert key_file in written


def test_cli_harvest(tmp_path):
    target, _master, ekey = _make_target(tmp_path)
    db = _make_db(tmp_path, [(f"/sdcard/{FILENAME}", ekey)])
    report = tmp_path / "harvest.json"

    code = app_main.main(
        ["--harvest", str(db), "--target", str(target), "--harvest-report", str(report)]
    )
    assert code == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["matched"] == 1
    assert data["items"][0]["ekey"] == ekey
