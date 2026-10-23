# 伴生 StemMate

本地离线处理 QQ 音乐下载的加密音频：**解密 → 人声分离 → 导出伴奏**。
音频与密钥全程只在本机处理，不上传任何服务器。

> 当前版本：**v1.1.1**（阶段 1–5 + m4a 转码 + 本地密钥提取）

---

## 安装（推荐用 Release）

1. 打开本仓库的 **Releases** 页面，下载最新的 `StemMate-v1.1.1-win64.zip`；
2. 解压到任意目录（整个 `StemMate` 文件夹一起保留）；
3. 双击 `StemMate.exe`；
4. 首次使用会提示下载分离模型（快速档约 330 MB，高质量档约 1.3 GB），
   下载一次后即可完全离线使用。

无需安装 Python，支持 Windows 10/11 64 位。

---

## 已实现能力

| 能力 | 状态 |
| --- | --- |
| PySide6 主窗口、工具栏、拖拽导入、队列表格、状态栏 | ✅ |
| 后台线程执行解密 + 分离（UI 不冻结） | ✅ |
| QMC v2 内嵌密钥：`QTag` / `PcV1Legacy` | ✅ |
| QMC v2 无内嵌密钥：`MusicEx` / `STag`（手动 ekey、密钥库） | ✅ |
| QMC v1 静态密钥（`.qmcflac` / `.qmc0` / `.qmc3` / `.tkm` 等） | ✅ |
| `.m4a` / `.opus` 等 libsndfile 不支持的格式：PyAV 自动转 WAV 后分离 | ✅ |
| 普通音频直接处理：`.mp3` / `.flac` / `.wav` / `.ogg` / `.m4a` / `.opus` | ✅ |
| 伴奏提取（karaoke：drums + bass + other） | ✅ |
| 快速档 / 高质量档切换，可选保留人声轨 | ✅ |
| 批量队列：进度、耗时秒表、`[i/n]` 计数、停止、失败重试 | ✅ |
| 输出目录：主工具栏直接选择，设置对话框也可配置 | ✅ |
| 设置对话框：输出格式（MP3 / WAV）、码率、人声轨、模型目录、线程数、密钥库 | ✅ |
| 本地密钥提取：扫描 QQ 音乐数据（SQLite / INI / 缓存 / 目录），试解密验证后入库 | ✅ |
| 设置持久化（`%APPDATA%\StemMate\settings.json`）与模型缓存清理 | ✅ |
| 输出 `原文件名_instrumental.mp3`（默认 320kbps；可选 WAV / `_vocals`） | ✅ |
| 首次运行模型检查与下载提示，下载后离线可用 | ✅ |
| 低内存机器自动降级 ONNX Runtime 图优化（避免 `bad allocation`） | ✅ |
| 解密结果音频头校验、错误密钥明确报错 | ✅ |
| 首次运行模型下载对话框（文件级 + 字节级进度） | ✅ |
| PyInstaller 目录模式打包，`--selftest` 自检，干净环境可运行 | ✅ |
| 深色模式、崩溃日志（`%LOCALAPPDATA%\StemMate\logs\crash.log`） | ✅ |
| 无界面 `--process` 模式（脚本 / 自动化验收） | ✅ |
| pytest（86 项） | ✅ |

### 设置项

主工具栏「选择输出目录…」可直接指定保存位置，选择后立即生效并持久化。
也可以从工具栏「设置…」（`Ctrl+,`）配置：

| 设置 | 说明 |
| --- | --- |
| 输出格式 | MP3（默认，128/192/256/320 kbps）或 WAV（PCM 16-bit） |
| 保留人声轨 | 额外输出 `_vocals.mp3` / `_vocals.wav` |
| 输出目录 | 主工具栏直接选择；设置中留空 = 与源文件同目录 |
| 模型目录 | 默认 `%LOCALAPPDATA%\StemMate\models`，可清理缓存（当前约 1.6 GB） |
| 推理线程数 | 0 = 自动（min(4, CPU 核心数)），1~16 手动 |
| 密钥库 | QQ 音乐 `player_process_db`，用于 musicex / STag |

设置保存到 `%APPDATA%\StemMate\settings.json`，下次启动自动恢复。

### 从 QQ 音乐本地数据提取密钥

工具栏「提取密钥…」：把需要密钥的文件留在队列里，添加 QQ 音乐的数据文件/目录，
工具会自动扫描并**用目标文件实际试解密**验证候选，命中后可直接写入内置密钥库。

可以给的路径（文件或整个文件夹都行）：

| 来源 | 典型路径 |
| --- | --- |
| 安卓密钥库 | `player_process_db`（`audio_file_ekey_table` / `p2p_cache_info_table`） |
| Windows 数据 | QQ 音乐安装/数据目录、`*.db`、`*.ini`、缓存目录 |
| macOS | `qqmusic.sqlite` 所在文件夹 |
| 兼容 | 任何包含 ekey 的文本、JSON、SQLite 或二进制缓存 |

扫描不依赖固定表名：只要文件里出现了能解开目标文件的 EKey 字符串就能命中；
误报的 base64 串会在验证阶段被丢弃。命中结果：

- **写入内置密钥库** → `%APPDATA%\StemMate\ekeys.db`，之后处理该文件无需再输入；
- **导出 ekey 文件** → `<mid>.ekey` + `ekeys.json` 清单。

命令行同样可用：

```bash
StemMate.exe --harvest "D:\QQMusic" --harvest "C:\path\player_process_db" \
             --target "D:\music\浮夸_L.mgg" --store-keys --harvest-report harvest.json
```

注意：如果客户端（如 Windows 22.x）只把登录凭据放在**进程内存**里、磁盘上没有
ekey，扫描不会命中——这时需要从实际下载过该歌曲的设备导出密钥库。

### 队列行为

- 文件严格按加入顺序处理，状态列为「解密中 / 下载模型 / 分离中 / 导出中 + 已用秒数」；
- 「停止」在**当前文件结束后**生效，剩余文件标记为「已停止」（ONNX 推理不可中断）；
- 「重试失败项」把失败 / 缺密钥 / 已停止的文件重新放回队列；
- 状态栏显示 `[i/n]` 与当前文件名，结束后汇总完成 / 失败 / 中断数量。

### 分离后端

| 质量档 | 模型 | 说明 |
| --- | --- | --- |
| 快速（默认） | Spleeter 2-stem → 回退 `htdemucs` | 当前 Python 3.13 + 新版 TensorFlow 环境下 **Spleeter 无法安装**（旧 numpy/TF 约束），实际使用 `htdemucs` 单文件模型 |
| 高质量 | `htdemucs_ft` 专家模型组 | 质量最佳；耗时约为快速档的 3 倍，首次下载约 1.3 GB |

模型缓存目录：`%LOCALAPPDATA%\StemMate\models`（阶段 4 可在设置中修改）。

在 4 核 i5-8265U / 16 GB 内存上实测：

- 快速档：8 秒音频分离约 12 秒，首次加载模型 30~40 秒；同一进程内后续文件不再重复加载。
- 高质量档：首次需下载约 1.3 GB 模型并逐个加载 4 个会话（约 5 分钟），
  之后分离速度约为音频时长的 6 倍左右。

低内存环境下会自动把 ONNX Runtime 图优化降级为 `ORT_DISABLE_ALL`，
避免出现 `bad allocation`；也可用环境变量 `STEMMATE_ORT_LEVEL=basic|disable`
手动指定。

### 关于数据集的真实样本（重要）

`Desktop\musictest\dataset\陈奕迅 - 浮夸_L.mgg` 是 **musicex 封装**：

```
song_id        1251166
media_mid      002ejEdb4KTwBw
media_filename O4M000015Tgb2RIq59.mgg
footer         192 字节，末尾 magic = "musicex\0"
```

musicex **不内嵌密钥**，EKey 由 QQ 音乐服务端随下载请求下发（需登录态），
因此该文件在纯离线、无额外密钥的情况下**无法解密**——这是新版客户端的格式设计，
不是实现缺陷。程序会明确提示「需要密钥」，不会静默失败。

可用的离线路径（GUI 均已提供入口）：

1. **自动提取**：工具栏「提取密钥…」，给它任意 QQ 音乐本地路径（见下文），
   扫描 + 试解密验证后写入内置密钥库；
2. **导入 EKey 文本**：工具栏「导入 EKey…」，选中文件后载入即可重试；
3. **导入密钥库**：工具栏「选择密钥库…」，支持 QQ 音乐
   `player_process_db`（`audio_file_ekey_table` / `EKeyFileInfo` /
   `p2p_cache_info_table`），按文件名或 media mid 自动匹配；
4. **环境变量**：`STEMMATE_EKEY=<ekey>`（适合脚本化自测）。

如果样本是 `QTag` / `PcV1Legacy` 封装（密钥内嵌），则无需任何额外操作。

---

## 目录结构

```
StemMate/
├── main.py                  # 应用入口
├── requirements.txt
├── pyproject.toml           # 依赖 + pytest 配置
├── core/
│   ├── decryptor.py         # 格式检测、解密入口、错误类型
│   ├── separator.py         # demucs-onnx / Spleeter 适配层、模型管理
│   ├── pipeline.py          # 解密 → 分离 → 导出
│   └── qmc/
│       ├── constants.py     # 静态表、EKey 种子、扩展名映射
│       ├── tea.py           # tc_tea（TEA + 魔改 CBC）
│       ├── ekey.py          # EKey V1/V2 解码
│       ├── ciphers.py       # QMC v1 静态表 / v2 Map / v2 RC4
│       ├── footer.py        # QTag / STag / MusicEx / PcV1Legacy 解析
│       └── keystore.py      # 本地密钥库查询
├── ui/main_window.py        # 主窗口、质量档、后台流水线线程
├── utils/audio.py           # 容器嗅探、时长探测
├── tools/make_demo_sample.py# 生成可播放的合成 .mgg 演示样本
├── samples/                 # 演示输入（解密后可分离）
└── tests/                   # pytest 用例与样本构造器
```

---

## 运行

```bash
pip install -r requirements.txt
python main.py
```

拖入文件 → 选择质量档 →「开始处理」→ 完成后点「打开输出目录」。
普通 `.mp3 / .flac / .wav / .ogg` 会跳过解密直接进入分离；`.m4a` / `.opus`
先由内置 PyAV 转成 44.1 kHz WAV 再分离（无需安装 ffmpeg）。
`.aac` / `.wma` / `.ape` 不在支持列表内，拖入时会提示忽略。

### 无界面处理（脚本 / 自动化验收）

```bash
StemMate.exe --process "D:\music\song.mgg" --output-dir "D:\out" \
             --format mp3 --quality fast --keep-vocals --report result.json
```

成功返回 0，失败返回 2；`--report` 写入 JSON（输出路径、后端、耗时或错误）。
`--selftest` 用于检查打包后的依赖与窗口能否正常创建。

### 生成可播放的演示样本

```bash
python tools/make_demo_sample.py                                  # Map 模式
python tools/make_demo_sample.py --mode rc4 --out samples/demo_rc4.mgg
```

脚本会合成一段 8 秒立体声音频，按 QMC v2（PcV1Legacy）加密为 `.mgg`，
并自动用正式解密流程回读校验时长。把生成的 `.mgg` 拖进窗口即可跑完整流程。

---

## 测试

```bash
python -m pytest -q       # 86 passed
```

覆盖内容：

- 尾部解析：四种封装 + 损坏/版本异常；
- QMC v1 静态表：跨 `0x7FFF` 边界、分块一致性、自反性；
- QMC v2 Map / RC4：任意分块与任意偏移解密的结果和一次性解密一致；
- EKey：V1 / V2 / 空白字符 / 非法输入；
- 端到端解密：构造 `.mflac` / `.mgg` / `.qmcflac` / musicex，验证输出字节与明文一致；
- 错误路径：错误密钥、未知格式、缺少 EKey；
- 真实样本：断言识别为 `MusicEx` 且给出 `media_mid`，并抛出可读的缺钥错误；
- 密钥库：建临时 SQLite，按文件名与 media mid 子串匹配；
- 分离层：模型就绪检测、下载错误包装、后端选择；
- 流水线：输出命名、人声轨、输出目录、重名递增、阶段回调、错误透传；
- 队列（离屏 Qt）：三文件顺序批量、停止后剩余标记、失败项重试、设置下传；
- 设置：默认值、存取往返、损坏文件兜底、非法值归一化、保存建目录；
- 导出：WAV / MP3 回读校验、96 kHz 输入自动重采样到 44.1 kHz；
- 下载引导：总进度折算、worker 信号与失败回调、模型就绪后对话框完成态；
- CLI：`--process` 成功/失败返回码与 JSON 报告。

开发期还做了独立实现对拍：EKey V1/V2 解码结果、Map/RC4 密钥流与
`libtakiyasha` 的逐字节一致（该库不随项目分发）。

---

## 打包为 exe

```bash
python -m PyInstaller StemMate.spec --noconfirm --clean
```

产物：`dist/StemMate/StemMate.exe`（目录模式，约 228 MB，需连同 `_internal/` 一起分发）。

- 采用 `onedir` 而非 `--onefile`：启动不需要把数百 MB 解压到临时目录，也便于替换 DLL；
- 打包后自检：`StemMate.exe --selftest`，结果写入
  `%LOCALAPPDATA%\StemMate\logs\selftest.json`（含各依赖导入与主窗口创建结果）；
- 已在**隔离环境**验证：清空 `PATH`（仅保留 `C:\Windows\System32`）、临时
  `APPDATA/LOCALAPPDATA`，双击/命令行启动均正常，无需安装 Python。

### 运行时数据位置

| 内容 | 路径 |
| --- | --- |
| 设置 | `%APPDATA%\StemMate\settings.json` |
| 模型缓存 | `%LOCALAPPDATA%\StemMate\models` |
| 日志（崩溃 / 自检） | `%LOCALAPPDATA%\StemMate\logs` |

系统要求：Windows 10/11 64 位；快速档模型约 330 MB，高质量档约 1.3 GB；
分离全程 CPU，8 GB 内存可运行（低内存会自动降级 ONNX 图优化）。

---

## 隐私与安全

- 不解析、不听写、不上传音频；仅在**首次下载模型**时访问 Hugging Face。
- 模型下载完成后自动设置 `HF_HUB_OFFLINE=1`，后续运行不再联网。
- 源文件以只读方式打开，解密中间文件写入独立临时目录并在流程结束后删除。
- 密钥只保存在内存与用户主动选择的文件中，不写入配置。

---

## 更新日志

### v1.1.1
- 主工具栏新增「选择输出目录…」，选择后立即生效并在下次启动时恢复；
- 补齐打包和运行所需的 `requests` 依赖。

### v1.1.0
- 新增 `.m4a` 支持：PyAV（FFmpeg）自动转 WAV 后分离；
- 从可添加列表中移除 `.aac` / `.wma` / `.ape`（拖入时直接提示忽略）；
- 新增本地 QQ 音乐密钥提取（扫描 + 试解密验证 + 内置密钥库）；
- 设置新增深色模式；首次运行模型下载带字节级进度；
- exe 体积约 294 MB（含 FFmpeg 解码库）。

### v1.0.0
- 首个完整版本：QMC 解密、demucs 分离、批量、设置、打包。

---

## 已知限制

- **musicex / STag 文件必须提供 ekey 或密钥库**：这是 QQ 音乐新版客户端的格式设计，
  纯离线无法凭空还原服务端密钥；
- 「停止」在**当前文件处理结束后**生效（ONNX 推理不可中断），剩余文件标记为「已停止」；
- 目录模式产物约 228 MB，分发时需携带整个 `StemMate` 文件夹；
- 高质量档首次下载约 1.3 GB，CPU 推理速度约为实时的 1/6（视机器而定）。

---

## 项目状态

5 个阶段全部完成：解密 → 分离 → 批量 → 设置/导出 → 打包交付。
后续可选增强：exe 代码签名、模型下载断点续传、队列拖拽排序、单文件模式（体积换便携性）。
