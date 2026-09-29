<div align="center">
  <img src="assets/memory-atlas.svg" alt="Memory Atlas logo" width="88" height="88">
  <h1>Memory Atlas</h1>
  <p><strong>本地 AI 记忆图谱 · Local AI Memory Network</strong></p>
  <p>
    <a href="#中文">简体中文</a> ·
    <a href="#english">English</a> ·
    <a href="#privacy--隐私">隐私说明 Privacy</a>
  </p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/dependencies-none-2EA44F" alt="No third-party dependencies">
    <img src="https://img.shields.io/badge/runs-locally-7B61FF" alt="Runs locally">
  </p>
</div>

---

## 中文

Memory Atlas 是一个运行在本机的 AI 记忆浏览与检索工具。它把你主动选择的持久记忆文件整理成一张可搜索、可反馈、可观察学习过程的关联图谱，帮助你在不同 AI 工具留下的资料之间找到线索。

Memory Atlas 只能读取运行它的这台电脑能够访问到的持久记忆和规则文件。项目文件能否被找到，取决于文件所在位置、是否已同步到本机，以及配置的扫描目录；与底层模型是在本地还是云端运行无关。纯云端工作区里的文件若没有同步或导出到本机，本地程序就无法读取。Memory Atlas 不读取聊天记录、不调用云端 API，也不会把图谱写回其他 AI 工具。

### 快速开始

需要 Python 3.10 或更新版本，不需要安装第三方依赖。

**Windows：** 下载仓库后，双击 `一键启动.bat`。等待浏览器打开 `http://127.0.0.1:8765`；使用期间保留启动窗口，按 `Ctrl+C` 停止服务。

**macOS / Linux：** 在项目目录运行：

```bash
python3 memory_atlas.py
```

然后在浏览器打开 <http://127.0.0.1:8765>。也可以使用 `python start_website.py` 启动并自动打开浏览器。

### 功能

- **记忆图谱：** 按项目浏览已导入记忆，并查看记忆之间的文本关联。
- **本地检索：** 支持中英文关键词匹配，并沿关联连接扩散激活相关记忆。
- **反馈与修正：** 为记忆升权、降权、固定或保存修正，观察反馈如何影响后续召回。
- **学习与进化：** 检视突触权重、学习曲线、网络进化和弱连接剪枝；运行状态保存在本机。
- **按需导入：** 默认使用 Codex 来源。其他来源需在 SOURCES 面板中选择并点击导入；也可以手动添加文件夹。
- **独立启动器：** Windows 双击即可启动；重复运行时会打开已运行的网站，不会另启服务。

### 支持的文件

| 来源 | 查找的持久记忆或规则 |
| --- | --- |
| Codex | `memory_summary.md`、`MEMORY.md`；可用 `--source` 指定目录 |
| Claude Code | `CLAUDE.md`、规则文件及项目记忆文件 |
| Cursor | `.cursor/rules` 中的 `.md` / `.mdc` 文件、旧版 `.cursorrules` |
| Gemini CLI | `GEMINI.md` |
| 通用项目指令 | `AGENTS.md`、`.github/copilot-instructions.md` |
| 其他导出文件 | 手动选择仅含 `.md`、`.mdc`、`.txt` 文件的文件夹 |

项目规则只从明确支持的文件名中查找，不会读取会话数据库或 `JSONL` 聊天记录。文件导入有数量和大小限制；来源列表会先显示扫描位置和文件数量，只有明确点击导入后才读取所选文件正文。来源可以在 SOURCES 面板中取消选择或移除。

需要添加默认项目搜索位置以外的目录时，可重复传入 `--workspace`：

```bash
python memory_atlas.py --workspace "D:\\code" --workspace "/mnt/work/app"
```

### 隐私与数据

- 服务仅绑定 `127.0.0.1`，仅供这台电脑访问；页面不加载外部脚本、字体或 API。
- 来源文件只读，不会被复制、修改或删除。搜索词、来源选择与本地反馈保存在 `data/feedback.sqlite3`。
- `data/` 已加入 Git 忽略规则；提交仓库时不会包含本机数据库。
- 只导入你有权读取并希望加入本地图谱的文件。`AGENTS.md`、规则和 `GEMINI.md` 属于工具指令文件，不等同于 AI 产品内部的记忆。
- 检索使用本地关键词匹配，不提供向量语义检索。页面里的神经元、突触和学习是便于检查关联与反馈的原型模型，不是语言模型的真实训练或神经元结构。

### 开发与验证

```powershell
python -m unittest discover -s tests -v
```

没有包含用户数据库或依赖安装步骤。项目目前是**本地运行的软件**，不是已托管的在线服务。

---

## English

Memory Atlas is a local browser app for exploring and searching persistent AI memory files. It organizes sources you choose into a searchable association graph, with feedback controls and a visible learning prototype to help you trace related notes across tools.

Memory Atlas can read persistent memory and rule files only when they are accessible from the computer running it. Whether project files can be found depends on where those files live, whether they are synced locally, and which directories are configured for scanning. It does not depend on whether the underlying model runs locally or in the cloud. Files available only inside a remote cloud workspace cannot be read by this local app unless they are synced or exported to this computer. Memory Atlas does not read chat histories, call cloud APIs, or write its graph back to other AI tools.

### Quick start

Python 3.10 or newer is required. There are no third-party dependencies.

**Windows:** Download the repository and double-click `一键启动.bat`. Wait for your browser to open `http://127.0.0.1:8765`. Keep the launcher window open while using the app; press `Ctrl+C` in that window to stop it.

**macOS / Linux:** From the project directory, run:

```bash
python3 memory_atlas.py
```

Then visit <http://127.0.0.1:8765>. You can also run `python start_website.py` to start the app and open your browser automatically.

### Features

- **Memory graph:** Browse imported memories by project and inspect text-based connections.
- **Local search:** Match Chinese and English keywords, then follow associations to related memories.
- **Feedback and corrections:** Boost, lower, pin, or correct a memory and see how feedback affects later retrieval.
- **Learning prototype:** Inspect connection weights, learning history, graph evolution, and weak-edge pruning. State is stored locally.
- **Opt-in import:** Codex is selected by default. Choose other sources in the SOURCES panel and explicitly import them; folders can also be added manually.
- **One-click Windows launcher:** Double-click to start. Launching again opens the existing site without starting a second server.

### Supported files

| Source | Persistent memory or rule files |
| --- | --- |
| Codex | `memory_summary.md`, `MEMORY.md`; use `--source` to select a directory |
| Claude Code | `CLAUDE.md`, rule files, and project memory files |
| Cursor | `.md` / `.mdc` files under `.cursor/rules`, plus legacy `.cursorrules` |
| Gemini CLI | `GEMINI.md` |
| General project instructions | `AGENTS.md`, `.github/copilot-instructions.md` |
| Other exports | Manually selected folders containing `.md`, `.mdc`, or `.txt` files |

Project rules are discovered only by their supported filenames. Chat databases and `JSONL` conversation logs are not read. Imports have file-count and size limits. The SOURCES panel shows scan locations and file counts first; file contents are read only after you explicitly import selected sources. Sources can be deselected or removed from that panel.

To add project directories outside the default search locations, pass `--workspace` more than once:

```bash
python memory_atlas.py --workspace "D:\\code" --workspace "/mnt/work/app"
```

### Privacy and data

- The service binds to `127.0.0.1` and is available only on your computer. The page loads no external scripts, fonts, or APIs.
- Source files are read-only: they are not copied, modified, or deleted. Source selections and local feedback are stored in `data/feedback.sqlite3`.
- `data/` is ignored by Git, so your local database is not part of repository commits.
- Import only files you are allowed to read and want in your local graph. `AGENTS.md`, tool rules, and `GEMINI.md` are instruction files; they are not the tools' internal memory stores.
- Search uses local keyword matching rather than vector search. Neurons, synapses, and learning describe an inspectable association prototype, not the actual training or neural structure of a language model.

### Development and tests

```powershell
python -m unittest discover -s tests -v
```

This repository does not include user databases or an install step. Memory Atlas currently runs locally; it is not a hosted web service.
