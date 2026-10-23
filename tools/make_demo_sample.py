# -*- coding: utf-8 -*-
"""生成一个「可播放」的合成 .mgg 演示文件。

用途：数据集里的真实 .mgg 是 musicex 封装（密钥在服务端），无法离线演示；
本脚本把一段自己生成的 OGG 音频按 QMC v2（PcV1Legacy + Map）加密，
生成内嵌密钥的 .mgg，方便验证「拖入 → 解密 → 可播放」全链路。

用法：
    python tools/make_demo_sample.py
    python tools/make_demo_sample.py --seconds 10 --out samples/demo.mgg
"""
from __future__ import annotations

import argparse
import base64
import io
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.qmc import tea  # noqa: E402
from core.qmc.ciphers import make_cipher  # noqa: E402
from core.qmc.constants import EKEY_SEED_KEY  # noqa: E402
from utils.audio import probe_audio  # noqa: E402

SALT = bytes([0xA5, 0x6E, 0x35, 0xBC, 0x7C, 0x31, 0x04, 0x55, 0xA0, 0xBF])


def craft_ekey(master_key: bytes) -> str:
    """把主密钥封装成单层 EKey（仅演示/测试用）。"""
    header, plain = master_key[:8], master_key[8:]
    tea_key = bytearray()
    for seed_byte, header_byte in zip(EKEY_SEED_KEY, header):
        tea_key.append(seed_byte)
        tea_key.append(header_byte)
    return base64.b64encode(header + tea.encrypt(plain, bytes(tea_key), SALT)).decode("ascii")


def render_ogg(seconds: float = 8.0, sample_rate: int = 44100) -> bytes:
    """合成一段带和弦与节拍的小样，输出 OGG/Vorbis 字节。"""
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    chords = [
        (261.63, 329.63, 392.00),   # C
        (220.00, 261.63, 329.63),   # Am
        (174.61, 220.00, 261.63),   # F
        (196.00, 246.94, 293.66),   # G
    ]
    beat = 1.0
    left = np.zeros_like(t)
    right = np.zeros_like(t)

    for index, freqs in enumerate(chords):
        start = index * beat
        end = min(start + beat, seconds)
        mask = (t >= start) & (t < end)
        local = t[mask] - start
        envelope = np.exp(-2.2 * local)
        for offset, freq in enumerate(freqs):
            tone = 0.22 * envelope * np.sin(2 * np.pi * freq * local)
            pan = 0.35 + 0.3 * offset
            left[mask] += tone * (1 - pan)
            right[mask] += tone * pan

    # 简单的四拍底鼓与军鼓，让听感更像「音乐」。
    for start in np.arange(0, seconds, 0.5):
        mask = (t >= start) & (t < start + 0.12)
        local = t[mask] - start
        freq = 65.0 if (start * 2) % 2 == 0 else 180.0
        hit = 0.35 * np.exp(-28 * local) * np.sin(2 * np.pi * freq * local)
        left[mask] += hit
        right[mask] += hit

    peak = max(np.max(np.abs(left)), np.max(np.abs(right)), 1e-6)
    left = (left / peak * 0.85).astype(np.float32)
    right = (right / peak * 0.85).astype(np.float32)
    stereo = np.stack([left, right], axis=1)

    buffer = io.BytesIO()
    sf.write(buffer, stereo, sample_rate, format="OGG", subtype="VORBIS")
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description="生成可播放的合成 .mgg 演示文件")
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--out", type=Path, default=ROOT / "samples" / "演示歌曲.mgg")
    parser.add_argument("--mode", choices=["map", "rc4"], default="map",
                        help="map：256 字节主密钥；rc4：512 字节主密钥")
    args = parser.parse_args()

    ogg = render_ogg(args.seconds)
    master = os.urandom(256 if args.mode == "map" else 512)
    ekey = craft_ekey(master)
    encrypted = make_cipher(master).decrypt(ogg, 0)

    footer = ekey.encode("ascii") + len(ekey.encode("ascii")).to_bytes(4, "little")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(encrypted + footer)

    # 自检：用正式解密流程解回来，确认能播放。
    import tempfile

    from core.decryptor import decrypt_file

    result = decrypt_file(args.out, workdir=Path(tempfile.mkdtemp(prefix="stemmate-demo-")))
    info = probe_audio(result.output)
    print(f"已生成：{args.out}（{args.out.stat().st_size} 字节）")
    print(f"封装：PcV1Legacy / {'Map' if args.mode == 'map' else 'RC4'}")
    print(f"EKey：{ekey}")
    print(f"解密自检：{result.output.name}，时长 {info.duration:.2f}s，"
          f"{info.sample_rate} Hz / {info.channels} ch" if info and info.duration else
          f"解密自检：{result.output}（无法读取时长）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
