"""Local, read-only memory explorer with selected AI-tool sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DEFAULT_SOURCE = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser() / "memories"
DEFAULT_DB = ROOT / "data" / "feedback.sqlite3"
MAX_IMPORT_FILES = 200
MAX_TOTAL_IMPORT_FILES = 250
MAX_IMPORT_BYTES = 256 * 1024
IMPORT_SUFFIXES = {".md", ".mdc", ".txt"}


@dataclass(frozen=True)
class Memory:
    id: str
    title: str
    text: str
    kind: str
    project: str
    source: str
    source_line: int
    date: str
    importance: float
    tool: str = "Codex"

    @property
    def preview(self) -> str:
        clean = " ".join(self.text.split())
        return clean[:240] + ("…" if len(clean) > 240 else "")


def memory_id(*parts: object) -> str:
    value = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def project_label(raw: str) -> str:
    value = raw.replace("\\", "/").strip().rstrip("/")
    if "Desktop/" in value:
        return value.split("Desktop/", 1)[1]
    return value.rsplit("/", 1)[-1] or "全局"


def source_date(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()


def short_title(value: str, limit: int = 70) -> str:
    if len(value) <= limit:
        return value
    cut = value[:limit]
    if " " in cut[-14:]:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def section(lines: list[str], name: str) -> list[tuple[int, str]]:
    start = next((i for i, line in enumerate(lines) if line.strip() == f"## {name}"), -1)
    if start < 0:
        return []
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return [(i + 1, lines[i]) for i in range(start + 1, end)]


def parse_summary(path: Path) -> list[Memory]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    date = source_date(path)
    source = str(path)
    memories: list[Memory] = []

    profile = section(lines, "User Profile")
    profile_text = " ".join(line.strip() for _, line in profile if line.strip())
    if profile_text:
        memories.append(Memory(memory_id("summary", "profile"), "用户画像", profile_text,
                               "profile", "全局", source, profile[0][0], date, 0.9))

    for heading, kind, importance in (("User preferences", "preference", 0.9),
                                      ("General Tips", "tip", 0.77)):
        for line_no, line in section(lines, heading):
            if not line.startswith("- "):
                continue
            text = line[2:].strip()
            if text:
                memories.append(Memory(memory_id("summary", heading, text), short_title(text), text,
                                       kind, "全局", source, line_no, date, importance))

    topic_lines = section(lines, "What's in Memory")
    current_project = "全局"
    current_date = date
    topic: dict[str, object] | None = None

    def flush_topic() -> None:
        nonlocal topic
        if topic is None:
            return
        memories.append(Memory(
            memory_id("summary", topic["project"], topic["title"]),
            str(topic["title"]), " ".join(topic["parts"]), "overview",
            str(topic["project"]), source, int(topic["line"]), str(topic["date"]), 0.62,
        ))
        topic = None

    for line_no, line in topic_lines:
        if line.startswith("### "):
            flush_topic()
            current_project = project_label(line[4:])
        elif line.startswith("#### "):
            flush_topic()
            value = line[5:].strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                current_date = value
            elif "Desktop" in value or "\\" in value or "/" in value:
                current_project = project_label(value)
                current_date = date
        elif line.startswith("- "):
            flush_topic()
            title = line[2:].strip()
            topic = {"project": current_project, "date": current_date,
                     "line": line_no, "title": title, "parts": [title]}
        elif topic is not None and line.strip().startswith("- "):
            topic["parts"].append(line.strip()[2:])
    flush_topic()
    return memories


def parse_registry(path: Path) -> list[Memory]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    source = str(path)
    file_date = source_date(path)
    group_starts = [i for i, line in enumerate(lines) if line.startswith("# Task Group: ")]
    memories: list[Memory] = []

    for group_pos, start in enumerate(group_starts):
        end = group_starts[group_pos + 1] if group_pos + 1 < len(group_starts) else len(lines)
        group = lines[start:end]
        group_title = group[0][len("# Task Group: "):].strip()
        scope = next((line[len("scope:"):].strip() for line in group if line.startswith("scope:")), "")
        applies = next((line for line in group if line.startswith("applies_to:")), "")
        cwd_match = re.search(r"cwd=([^;]+)", applies)
        project = project_label(cwd_match.group(1)) if cwd_match else group_title
        task_starts = [i for i, line in enumerate(group) if re.match(r"## Task \d+: ", line)]
        group_notes = [line[2:].strip() for line in group if line.startswith("- ") and "[Task " in line]

        for task_pos, task_start in enumerate(task_starts):
            task_end = task_starts[task_pos + 1] if task_pos + 1 < len(task_starts) else len(group)
            task_lines = group[task_start:task_end]
            match = re.match(r"## Task (\d+): (.*)", task_lines[0])
            if match is None:
                continue
            number, title = match.groups()
            useful: list[str] = []
            in_keywords = False
            for line in task_lines[1:]:
                if line.startswith("### "):
                    in_keywords = line.strip() == "### keywords"
                elif line.startswith("## "):
                    in_keywords = False
                elif in_keywords and line.startswith("- "):
                    useful.append(line[2:].strip())
            related = [note for note in group_notes if f"[Task {number}]" in note]
            text = " ".join([title, scope, *useful, *related])[:6500]
            dates = re.findall(r"updated_at=(\d{4}-\d{2}-\d{2})", "\n".join(task_lines))
            date = max(dates) if dates else file_date
            memories.append(Memory(memory_id("registry", group_title, number), title,
                                   text, "task", project, source, start + task_start + 1,
                                   date, 0.68))
    return memories


def load_memories(source: Path) -> list[Memory]:
    memories = [*parse_summary(source / "memory_summary.md"),
                *parse_registry(source / "MEMORY.md")]
    return sorted(memories, key=lambda item: (item.project, item.kind, item.title))


def import_files(directory: Path, depth: int = 2,
                 suffixes: set[str] | None = None) -> list[Path]:
    """List bounded plain-text memory files without opening their contents."""
    if not directory.is_dir() or directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
        return []
    found: list[Path] = []
    pending = [(directory, 0)]
    while pending and len(found) < MAX_IMPORT_FILES:
        current, level = pending.pop(0)
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for child in children[:1000]:
            if child.is_symlink() or getattr(child, "is_junction", lambda: False)():
                continue
            try:
                if child.is_dir() and level < depth:
                    pending.append((child, level + 1))
                elif child.is_file() and child.suffix.lower() in (suffixes or IMPORT_SUFFIXES) \
                        and child.stat().st_size <= MAX_IMPORT_BYTES:
                    found.append(child)
                    if len(found) >= MAX_IMPORT_FILES:
                        break
            except OSError:
                continue
    return found


DEV_FOLDER_NAMES = ("Projects", "projects", "Project", "dev", "Dev", "code", "Code", "src", "repos", "Repos",
                    "workspace", "workspaces", "Workspace", "GitHub", "github", "Developer", "git", "work")
WORKSPACE_TTL = 20.0


def slug_of(path: Path) -> str:
    """Claude Code names a project's data folder by turning every non-alphanumeric character into '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def shell_folder(kind: str, home: Path, host: bool) -> list[Path]:
    """Desktop / Documents as the OS reports them (OneDrive redirection, localized folder names)."""
    found = [home / kind, home / "OneDrive" / kind,
             home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / kind]
    if not host:
        return found
    if sys.platform == "win32":
        try:
            import winreg
            name = {"Desktop": "Desktop", "Documents": "Personal"}[kind]
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                found.append(Path(os.path.expandvars(winreg.QueryValueEx(key, name)[0])))
        except (ImportError, OSError, KeyError):
            pass
    else:
        variable = {"Desktop": "XDG_DESKTOP_DIR", "Documents": "XDG_DOCUMENTS_DIR"}[kind]
        try:
            for line in (home / ".config" / "user-dirs.dirs").read_text(encoding="utf-8").splitlines():
                if line.startswith(variable + "="):
                    found.append(Path(line.split("=", 1)[1].strip().strip('"').replace("$HOME", str(home))))
        except OSError:
            pass
    return found


def project_roots(home: Path, host: bool = False, folders: list[Path] | tuple = (),
                  projects: list[Path] | tuple = ()) -> list[Path]:
    """Workspace folders in which a fixed set of well-known memory / rule file names is looked for.

    Auto-detected: the first level of Desktop, Documents and common dev folders under the home directory.
    `folders` are explicit parents (CLI flag / env var): the folder and its children are checked, wherever
    they are. `projects` are single project folders (e.g. recorded by Codex). File contents are not read here.
    """
    roots: list[Path] = [ROOT]
    home_resolved = home.resolve()

    def local_directory(path: Path) -> Path | None:
        try:
            resolved = path.resolve()
            return resolved if resolved.is_dir() and resolved.is_relative_to(home_resolved) else None
        except OSError:
            return None

    def children_of(parent: Path) -> list[Path]:
        try:
            return sorted(parent.iterdir(), key=lambda item: item.name.lower())[:200]
        except OSError:
            return []

    parents = [*shell_folder("Desktop", home, host), *shell_folder("Documents", home, host),
               *(home / name for name in DEV_FOLDER_NAMES), home / "source" / "repos"]
    if host:
        roots.append(Path.cwd())
    for parent in dict.fromkeys(parents):
        if parent.is_dir():
            roots.extend(child for child in map(local_directory, children_of(parent)) if child is not None)
    for folder in folders:
        try:
            resolved = folder.expanduser().resolve()
        except OSError:
            continue
        if resolved.is_dir():
            roots.append(resolved)
            roots.extend(child for child in children_of(resolved) if child.is_dir())
    for project in projects:
        try:
            resolved = project.resolve()
        except OSError:
            continue
        if resolved.is_dir():
            roots.append(resolved)
    return list(dict.fromkeys(roots))[:500]


def readable_file(path: Path, suffixes: set[str]) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and path.suffix.lower() in suffixes \
            and path.stat().st_size <= MAX_IMPORT_BYTES
    except OSError:
        return False


def named_files(paths: list[Path], suffixes: set[str] | None = None) -> list[Path]:
    wanted = suffixes or {".md"}
    return list(dict.fromkeys(path for path in paths if readable_file(path, wanted)))[:MAX_IMPORT_FILES]


def claude_memory_files(root: Path, workspaces: list[Path]) -> list[Path]:
    files: list[Path] = named_files([root / "CLAUDE.md"])
    files.extend(import_files(root / "rules", depth=1, suffixes={".md"}))
    projects = root / "projects"
    if projects.is_dir():
        try:
            for project in sorted(projects.iterdir(), key=lambda item: item.name.lower())[:500]:
                if project.is_dir() and not project.is_symlink():
                    files.extend(import_files(project / "memory", depth=1, suffixes={".md"}))
                    if len(files) >= MAX_IMPORT_FILES:
                        break
        except OSError:
            pass
    for workspace in workspaces:
        files.extend(named_files([workspace / "CLAUDE.md", workspace / "CLAUDE.local.md",
                                  workspace / ".claude" / "CLAUDE.md"]))
        files.extend(import_files(workspace / ".claude" / "rules", depth=1, suffixes={".md"}))
        if len(files) >= MAX_IMPORT_FILES:
            break
    return list(dict.fromkeys(files))[:MAX_IMPORT_FILES]


def cursor_rule_files(root: Path, workspaces: list[Path]) -> list[Path]:
    rule_suffixes = {".mdc", ".md"}
    files = import_files(root / "rules", depth=1, suffixes=rule_suffixes)
    for workspace in workspaces:
        files.extend(import_files(workspace / ".cursor" / "rules", depth=2, suffixes=rule_suffixes))
        legacy = workspace / ".cursorrules"
        if readable_file(legacy, {""}):
            files.append(legacy)
        if len(files) >= MAX_IMPORT_FILES:
            break
    return list(dict.fromkeys(files))[:MAX_IMPORT_FILES]


def cursor_app_dir(home: Path, host: bool) -> Path:
    if sys.platform == "win32":
        base = Path(os.environ["APPDATA"]) if host and os.environ.get("APPDATA") else home / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = home / ".config"
    return base / "Cursor"


def workspace_name(path: Path) -> str:
    parent = path.parent
    if parent.name == "rules" and parent.parent.name in {".claude", ".cursor"}:
        return parent.parent.parent.name
    if parent.name in {".claude", ".cursor", ".github"}:
        return parent.parent.name
    return parent.name


def friendly_slug(slug: str, known: dict[str, Path]) -> str:
    """Readable name for a Claude Code project data folder."""
    if slug in known:
        return known[slug].name
    parts = [part for part in slug.split("-") if part]
    for marker in ("Desktop", "Documents"):
        if marker in parts and parts.index(marker) + 1 < len(parts):
            return "-".join(parts[parts.index(marker) + 1:])
    return slug if len(parts) < 4 else "-".join(parts[-2:])


def parse_import_file(path: Path, tool: str, project: str) -> Memory | None:
    try:
        if path.stat().st_size > MAX_IMPORT_BYTES:
            return None
        content = path.read_text(encoding="utf-8-sig", errors="replace").strip()
        date = source_date(path)
    except OSError:
        return None
    if not content or "\x00" in content:
        return None
    lines = content.splitlines()
    line_offset = 0
    if lines[0].strip() == "---":
        end = next((i for i in range(1, min(len(lines), 80)) if lines[i].strip() == "---"), None)
        if end is not None:
            line_offset = end + 1
            lines = lines[end + 1:]
    meaningful = [(i + 1, line.strip()) for i, line in enumerate(lines) if line.strip()]
    if not meaningful:
        return None
    heading = next(((i, line.lstrip("# ").strip()) for i, line in meaningful if line.startswith("# ")), None)
    title = short_title(heading[1] if heading else meaningful[0][1] or path.stem)
    text = "\n".join(lines).strip()[:16000]
    return Memory(memory_id("import", str(path.resolve()).casefold()), title, text, "overview",
                  project, str(path), (heading[0] if heading else meaningful[0][0]) + line_offset,
                  date, 0.65, tool)


def terms(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).lower()
    english = re.findall(r"[a-z][a-z0-9]{1,}|\d{3,}", normalized)
    chinese_runs = re.findall(r"[\u3400-\u9fff]+", normalized)
    chinese = [run[i:i + 2] for run in chinese_runs for i in range(max(0, len(run) - 1))]
    chinese.extend(run for run in chinese_runs if len(run) == 1)
    stop = {"the", "and", "for", "from", "with", "this", "that", "task", "user", "memory"}
    return {term for term in [*english, *chinese] if term not in stop}


def relevance(query: str, text: str) -> float:
    query_terms = terms(query)
    if not query_terms:
        return 0.0
    text_terms = terms(text)
    overlap = len(query_terms & text_terms) / len(query_terms)
    normalized_query = unicodedata.normalize("NFKC", query).lower().strip()
    phrase = 0.2 if len(normalized_query) >= 3 and normalized_query in unicodedata.normalize("NFKC", text).lower() else 0.0
    return min(1.0, 0.8 * overlap + phrase)


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


class FeedbackStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS feedback (
                memory_id TEXT PRIMARY KEY,
                boost INTEGER NOT NULL DEFAULT 0,
                pinned INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                correction TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS source_selection (
                source_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS custom_sources (
                source_id TEXT PRIMARY KEY,
                path TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS synapse (
                a TEXT NOT NULL,
                b TEXT NOT NULL,
                base REAL NOT NULL,
                weight REAL NOT NULL,
                origin TEXT NOT NULL DEFAULT 'base',
                pruned INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(a, b)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS network_meta (
                key TEXT PRIMARY KEY,
                value REAL NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS learning_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                generation INTEGER NOT NULL,
                loss REAL,
                accuracy REAL,
                created_at TEXT NOT NULL
            )""")
            db.execute("INSERT OR IGNORE INTO source_selection(source_id, enabled) VALUES('codex', 1)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def all(self) -> dict[str, dict]:
        with self.connect() as db:
            return {row["memory_id"]: dict(row) for row in db.execute("SELECT * FROM feedback")}

    def enabled_sources(self) -> set[str]:
        with self.connect() as db:
            return {row["source_id"] for row in db.execute(
                "SELECT source_id FROM source_selection WHERE enabled=1")}

    def custom_sources(self) -> dict[str, Path]:
        with self.connect() as db:
            return {row["source_id"]: Path(row["path"]) for row in db.execute(
                "SELECT source_id, path FROM custom_sources")}

    def set_sources(self, ids: set[str], known_ids: set[str]) -> None:
        with self.connect() as db:
            for source_id in known_ids:
                db.execute("""INSERT INTO source_selection(source_id, enabled) VALUES(?, ?)
                              ON CONFLICT(source_id) DO UPDATE SET enabled=excluded.enabled""",
                           (source_id, int(source_id in ids)))

    def add_custom_source(self, source_id: str, path: Path) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO custom_sources(source_id, path) VALUES(?, ?)",
                       (source_id, str(path)))
            db.execute("INSERT OR IGNORE INTO source_selection(source_id, enabled) VALUES(?, 0)",
                       (source_id,))

    def remove_custom_source(self, source_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM custom_sources WHERE source_id=?", (source_id,))
            db.execute("DELETE FROM source_selection WHERE source_id=?", (source_id,))

    def synapses(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM synapse")]

    def save_synapses(self, rows: list[dict]) -> None:
        with self.connect() as db:
            db.executemany("""INSERT INTO synapse(a, b, base, weight, origin, pruned)
                              VALUES(:a, :b, :base, :weight, :origin, :pruned)
                              ON CONFLICT(a, b) DO UPDATE SET base=excluded.base,
                              weight=excluded.weight, origin=excluded.origin,
                              pruned=excluded.pruned""", rows)

    def log_event(self, event: str, epoch: int, generation: int,
                  loss: float | None, accuracy: float | None) -> None:
        with self.connect() as db:
            db.execute("""INSERT INTO learning_log(event, epoch, generation, loss, accuracy, created_at)
                          VALUES(?, ?, ?, ?, ?, ?)""",
                       (event, epoch, generation, loss, accuracy, datetime.now(timezone.utc).isoformat()))

    def history(self, limit: int = 200) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""SELECT event, epoch, generation, loss, accuracy FROM learning_log
                                 ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def meta(self) -> dict[str, float]:
        with self.connect() as db:
            return {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM network_meta")}

    def set_meta(self, values: dict[str, float]) -> None:
        with self.connect() as db:
            for key, value in values.items():
                db.execute("""INSERT INTO network_meta(key, value) VALUES(?, ?)
                              ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, value))

    def change(self, memory_id_value: str, action: str, correction: str = "") -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM feedback WHERE memory_id=?", (memory_id_value,)).fetchone()
            current = dict(row) if row else {"memory_id": memory_id_value, "boost": 0,
                                             "pinned": 0, "archived": 0, "correction": ""}
            if action == "boost":
                current["boost"] = min(3, current["boost"] + 1)
            elif action == "down":
                current["boost"] = max(-3, current["boost"] - 1)
            elif action == "pin":
                current["pinned"] = 1 - current["pinned"]
            elif action == "archive":
                current["archived"] = 1 - current["archived"]
            elif action == "correct":
                current["correction"] = correction.strip()
                if current["correction"]:
                    current["boost"] = max(1, current["boost"])
            elif action == "reset":
                current.update(boost=0, pinned=0, archived=0, correction="")
            else:
                raise ValueError("未知反馈操作")
            current["updated_at"] = datetime.now(timezone.utc).isoformat()
            db.execute("""INSERT INTO feedback(memory_id, boost, pinned, archived, correction, updated_at)
                          VALUES(:memory_id, :boost, :pinned, :archived, :correction, :updated_at)
                          ON CONFLICT(memory_id) DO UPDATE SET boost=excluded.boost,
                          pinned=excluded.pinned, archived=excluded.archived,
                          correction=excluded.correction, updated_at=excluded.updated_at""", current)
            return current


def effective_strength(memory: Memory, feedback: dict) -> float:
    return clamp(memory.importance + 0.12 * int(feedback.get("boost", 0))
                 + 0.08 * int(feedback.get("pinned", 0)))


def recency(memory: Memory, now: datetime | None = None) -> float:
    if memory.kind in {"profile", "preference", "tip"}:
        return 1.0
    try:
        date = datetime.fromisoformat(memory.date[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.5
    age_days = max(0, ((now or datetime.now(timezone.utc)) - date).total_seconds() / 86400)
    half_life = 90 if memory.kind == "task" else 120
    return math.exp(-math.log(2) * age_days / half_life)


ASSOCIATION_MIN = 0.12


def rank_memories(memories: list[Memory], feedback: dict[str, dict], query: str,
                  network: "Network | None" = None) -> list[dict]:
    active_query = bool(query.strip())
    relevances: dict[str, float] = {}
    for memory in memories:
        state = feedback.get(memory.id, {})
        if state.get("archived"):
            continue
        correction = state.get("correction")
        if correction:
            relevances[memory.id] = relevance(query, correction)
        else:
            relevances[memory.id] = clamp(0.50 * relevance(query, memory.title)
                                          + 0.55 * relevance(query, memory.project)
                                          + 0.20 * relevance(query, memory.text))
    # Spreading activation: keyword hits excite their synapse neighbours; learned support is a standing prior.
    spread = network.spread({key: value for key, value in relevances.items() if value > 0})         if network and active_query else {}
    support = network.support(network.labels(feedback)) if network else {}

    results: list[dict] = []
    for memory in memories:
        if memory.id not in relevances:
            continue
        state = feedback.get(memory.id, {})
        rel = relevances[memory.id]
        link = spread.get(memory.id, 0.0)
        if active_query and rel <= 0 and link < ASSOCIATION_MIN:
            continue
        drive = clamp(rel + 0.5 * link)
        prior = support.get(memory.id, 0.0)
        strength = effective_strength(memory, state)
        freshness = recency(memory)
        raw = (0.72 * drive + 0.20 * strength + 0.08 * freshness) if active_query             else (0.72 * strength + 0.28 * freshness)
        score = clamp(raw + 0.15 * prior)
        results.append({"id": memory.id, "score": round(score, 3),
                        "relevance": round(rel, 3), "strength": round(strength, 3),
                        "recency": round(freshness, 3), "assoc": round(link, 3),
                        "support": round(prior, 3), "activation": round(drive, 3) if active_query else 0.0,
                        "reason": "修正内容匹配" if state.get("correction") and rel > 0 else
                                  "关键词匹配" if rel > 0 else
                                  "联想激活" if active_query else "按重要性排列"})
    return sorted(results, key=lambda result: (-result["score"], result["id"]))


def build_links(memories: list[Memory]) -> tuple[list[dict], list[dict]]:
    projects = sorted({memory.project for memory in memories})
    hubs = [{"id": f"project:{memory_id(project)}", "name": project,
             "count": sum(item.project == project for item in memories)} for project in projects]
    hub_for = {hub["name"]: hub["id"] for hub in hubs}
    links = [{"source": hub_for[memory.project], "target": memory.id,
              "kind": "project"} for memory in memories]
    seen: set[tuple[str, str]] = set()
    signatures = {memory.id: terms(memory.title + " " + memory.preview) for memory in memories}
    for memory in memories:
        candidates: list[tuple[float, Memory]] = []
        for other in memories:
            if other.id == memory.id or other.project == memory.project:
                continue
            shared = signatures[memory.id] & signatures[other.id]
            if len(shared) < 2:
                continue
            similarity = len(shared) / max(1, len(signatures[memory.id] | signatures[other.id]))
            if similarity >= 0.12:
                candidates.append((similarity, other))
        for _, other in sorted(candidates, key=lambda pair: -pair[0])[:2]:
            pair = tuple(sorted((memory.id, other.id)))
            if pair not in seen:
                seen.add(pair)
                links.append({"source": pair[0], "target": pair[1], "kind": "related"})
    return hubs, links


def edge_key(first: str, second: str) -> tuple[str, str]:
    return (first, second) if first < second else (second, first)


def label_value(state: dict) -> float:
    """Manual feedback as a signed learning signal: -1 suppress ... +1 reinforce."""
    return clamp(int(state.get("boost", 0)) / 3 + 0.34 * int(state.get("pinned", 0)), -1.0, 1.0)


class Network:
    """Associative layer: every memory is a neuron, every weighted link a synapse.

    Synapses start from text similarity. Learning moves their weights toward targets implied by
    the user's feedback; evolution prunes weak synapses and grows new ones. Nothing here edits
    source files or the memories themselves.
    """

    LEARNING_RATE = 0.25
    PRUNE_BELOW = 0.12
    GROW_LIMIT = 6
    GROW_MIN_SCORE = 0.2
    CONVERGED_LOSS = 1e-4

    def __init__(self, memories: list[Memory], store: FeedbackStore):
        self.store = store
        self.ids = {memory.id for memory in memories}
        self.edges: dict[tuple[str, str], dict] = {}
        self._build_base(memories)
        for row in store.synapses():
            key = (row["a"], row["b"])
            if row["a"] not in self.ids or row["b"] not in self.ids:
                continue
            edge = self.edges.get(key)
            if edge is None:
                if row["origin"] != "grown":
                    continue
                edge = self.edges[key] = {"base": row["base"], "origin": "grown"}
            edge["w"] = row["weight"]
            edge["pruned"] = bool(row["pruned"])
        for edge in self.edges.values():
            edge.setdefault("w", edge["base"])
            edge.setdefault("pruned", False)
        meta = store.meta()
        self.epoch = int(meta.get("epoch", 0))
        self.generation = int(meta.get("generation", 0))
        self.loss: float | None = meta.get("loss")

    def _build_base(self, memories: list[Memory]) -> None:
        signature = {memory.id: terms(memory.title + " " + memory.preview) for memory in memories}
        for memory in memories:
            same: list[tuple[float, str]] = []
            cross: list[tuple[float, str]] = []
            for other in memories:
                if other.id == memory.id:
                    continue
                shared = len(signature[memory.id] & signature[other.id])
                union = len(signature[memory.id] | signature[other.id]) or 1
                similarity = shared / union
                if other.project == memory.project:
                    same.append((similarity, other.id))
                elif shared >= 2 and similarity >= 0.10:
                    cross.append((similarity, other.id))
            for bucket, is_same in ((same, True), (cross, False)):
                for similarity, other_id in sorted(bucket, key=lambda pair: (-pair[0], pair[1]))[:2]:
                    key = edge_key(memory.id, other_id)
                    if key not in self.edges:
                        weight = clamp(0.3 + 1.6 * similarity + (0.1 if is_same else 0.0), 0.3, 0.9)
                        self.edges[key] = {"base": weight, "w": weight, "origin": "base", "pruned": False}

    def labels(self, feedback: dict[str, dict]) -> dict[str, float]:
        return {memory_id: label_value(feedback.get(memory_id, {})) for memory_id in self.ids}

    def adjacency(self) -> dict[str, list[tuple[str, float]]]:
        adjacent: dict[str, list[tuple[str, float]]] = {}
        for (first, second), edge in self.edges.items():
            if edge["pruned"]:
                continue
            adjacent.setdefault(first, []).append((second, edge["w"]))
            adjacent.setdefault(second, []).append((first, edge["w"]))
        return adjacent

    def support(self, labels: dict[str, float]) -> dict[str, float]:
        """Weighted mean feedback of a neuron's neighbours (its own label is never included)."""
        support: dict[str, float] = {}
        for memory_id, neighbours in self.adjacency().items():
            total = sum(weight for _, weight in neighbours)
            support[memory_id] = sum(weight * labels.get(other, 0.0) for other, weight in neighbours) / total \
                if total else 0.0
        return support

    def spread(self, seeds: dict[str, float], hops: int = 2, decay: float = 0.5) -> dict[str, float]:
        """Propagate activation along synapses; each hop is scaled by weight and decay."""
        adjacent = self.adjacency()
        activation: dict[str, float] = {}
        frontier = [(memory_id, value, None) for memory_id, value in seeds.items()]
        for _ in range(hops):
            reached: dict[tuple[str, str], float] = {}
            for memory_id, value, origin in frontier:
                for other, weight in adjacent.get(memory_id, ()):
                    if other != origin:
                        reached[(other, memory_id)] = reached.get((other, memory_id), 0.0) + value * weight * decay
            for (other, _), value in reached.items():
                activation[other] = activation.get(other, 0.0) + value
            frontier = [(other, value, origin) for (other, origin), value in reached.items()]
        return {memory_id: min(1.0, value) for memory_id, value in activation.items()}

    @staticmethod
    def _target(edge: dict, first: float, second: float) -> float:
        base = edge["base"]
        product = first * second
        if product > 0:      # same sign: associated memories are reinforced (or suppressed) together
            return min(1.0, base + 0.6 * min(abs(first), abs(second)))
        if product < 0:      # opposite sign: the association is unreliable
            return max(0.0, base - 0.8 * min(abs(first), abs(second)))
        return min(1.0, base + 0.3 * max(abs(first), abs(second)))   # Hebbian co-activation / rest at base

    def _persist(self, keys: list[tuple[str, str]]) -> None:
        self.store.save_synapses([{"a": a, "b": b, "base": self.edges[(a, b)]["base"],
                                   "weight": self.edges[(a, b)]["w"], "origin": self.edges[(a, b)]["origin"],
                                   "pruned": int(self.edges[(a, b)]["pruned"])} for a, b in keys])
        meta = {"epoch": self.epoch, "generation": self.generation}
        if self.loss is not None:
            meta["loss"] = self.loss
        self.store.set_meta(meta)

    def learn_step(self, labels: dict[str, float], rate: float | None = None) -> float:
        rate = self.LEARNING_RATE if rate is None else rate
        squared = 0.0
        changed: list[tuple[str, str]] = []
        active = [(key, edge) for key, edge in self.edges.items() if not edge["pruned"]]
        for (first, second), edge in active:
            error = self._target(edge, labels.get(first, 0.0), labels.get(second, 0.0)) - edge["w"]
            squared += error * error
            if abs(error) > 1e-9:
                edge["w"] = clamp(edge["w"] + rate * error)
                changed.append((first, second))
        self.loss = squared / len(active) if active else 0.0
        self.epoch += 1
        self._persist(changed)
        return self.loss

    def accuracy(self, labels: dict[str, float]) -> tuple[float | None, int]:
        """Among synapses whose ends both carry feedback: share whose weight moved the right way.

        Same-sign pairs should be strengthened above their starting weight; opposite-sign pairs
        should be weakened below it (or pruned). Neutral memories never count.
        """
        correct = total = 0
        for (first, second), edge in self.edges.items():
            product = labels.get(first, 0.0) * labels.get(second, 0.0)
            if product == 0:
                continue
            total += 1
            if product > 0:
                correct += not edge["pruned"] and edge["w"] > edge["base"] + 0.01
            else:
                correct += edge["pruned"] or edge["w"] < edge["base"] - 0.01
        return (correct / total if total else None), total

    def prune(self) -> int:
        removed = [key for key, edge in self.edges.items() if not edge["pruned"] and edge["w"] < self.PRUNE_BELOW]
        for key in removed:
            self.edges[key]["pruned"] = True
        if removed:
            self._persist(removed)
        return len(removed)

    def evolve(self, labels: dict[str, float]) -> int:
        """Grow new synapses: friends-of-friends and memories reinforced together. Pruned links never return."""
        neighbours = {memory_id: dict(links) for memory_id, links in self.adjacency().items()}
        candidates: dict[tuple[str, str], float] = {}
        for links in neighbours.values():
            items = sorted(links.items())
            for i, (first, first_weight) in enumerate(items):
                for second, second_weight in items[i + 1:]:
                    key = edge_key(first, second)
                    if key not in self.edges:
                        candidates[key] = max(candidates.get(key, 0.0), first_weight * second_weight)
        marked = sorted(memory_id for memory_id, value in labels.items() if value != 0 and memory_id in self.ids)
        for i, first in enumerate(marked):
            for second in marked[i + 1:]:
                key = edge_key(first, second)
                if key not in self.edges and labels[first] * labels[second] > 0:
                    candidates[key] = candidates.get(key, 0.0) + 0.6 * min(abs(labels[first]), abs(labels[second]))
        chosen = sorted(((score, key) for key, score in candidates.items() if score >= self.GROW_MIN_SCORE),
                        key=lambda item: (-item[0], item[1]))[:self.GROW_LIMIT]
        for score, key in chosen:
            weight = clamp(0.3 + 0.5 * min(score, 1.0), 0.3, 0.8)
            self.edges[key] = {"base": weight, "w": weight, "origin": "grown", "pruned": False}
        self.generation += 1
        self._persist([key for _, key in chosen])
        return len(chosen)

    def snapshot(self, labels: dict[str, float]) -> dict:
        accuracy, sample = self.accuracy(labels)
        active = sorted(((key, edge) for key, edge in self.edges.items() if not edge["pruned"]),
                        key=lambda item: item[0])
        return {"synapses": [{"a": a, "b": b, "w": round(edge["w"], 3), "base": round(edge["base"], 3),
                              "origin": edge["origin"]} for (a, b), edge in active],
                "history": [{**row, "loss": None if row["loss"] is None else round(row["loss"], 5),
                             "accuracy": None if row["accuracy"] is None else round(row["accuracy"], 3)}
                            for row in self.store.history()],
                "stats": {"neurons": len(self.ids), "connections": len(active),
                          "pruned": sum(edge["pruned"] for edge in self.edges.values()),
                          "grown": sum(edge["origin"] == "grown" for edge in self.edges.values()),
                          "weak": sum(edge["w"] < self.PRUNE_BELOW for _, edge in active),
                          "epoch": self.epoch, "generation": self.generation,
                          "loss": None if self.loss is None else round(self.loss, 5),
                          "accuracy": None if accuracy is None else round(accuracy, 3),
                          "accuracy_n": sample, "learning_rate": self.LEARNING_RATE,
                          "labeled": sum(value != 0 for value in labels.values())}}


class Atlas:
    def __init__(self, source: Path, database: Path, home: Path | None = None,
                 workspaces: list[Path] | None = None):
        self.source = source
        self.use_host_paths = home is None
        self.home = home or Path.home()
        self.claude_root = Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser() \
            if home is None and os.environ.get("CLAUDE_CONFIG_DIR") else self.home / ".claude"
        # Explicit extra project locations: --workspace flags, plus MEMORY_ATLAS_WORKSPACES on the real host.
        variable = os.environ.get("MEMORY_ATLAS_WORKSPACES", "") if home is None else ""
        self.extra_folders = [*(workspaces or []), *(Path(item) for item in variable.split(os.pathsep) if item.strip())]
        self._workspace_cache: tuple[float, list[Path]] | None = None
        self.store = FeedbackStore(database)
        self.memories: list[Memory] = []
        self.network: Network
        self.reload()

    def workspaces(self, refresh: bool = False) -> list[Path]:
        """Project folders to check for well-known file names; cached because listing them touches the disk."""
        now = time.monotonic()
        if refresh or self._workspace_cache is None or now - self._workspace_cache[0] > WORKSPACE_TTL:
            recorded = self._recorded_projects() if "codex" in self.store.enabled_sources() else []
            self._workspace_cache = (now, project_roots(self.home, self.use_host_paths,
                                                        self.extra_folders, recorded))
        return self._workspace_cache[1]

    def _recorded_projects(self) -> list[Path]:
        """Project folders Codex itself recorded (cwd=...) in the already-selected MEMORY.md."""
        path = self.source / "MEMORY.md"
        try:
            if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
                return []
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return []
        found = [Path(raw.strip().strip('"')) for raw in dict.fromkeys(re.findall(r"cwd=([^;\n]+)", text))]
        return [item for item in found if item.is_absolute()][:200]

    def source_catalog(self, refresh: bool = False) -> list[dict]:
        selected = self.store.enabled_sources()
        workspaces = self.workspaces(refresh)
        codex_files = [path for path in (self.source / "memory_summary.md", self.source / "MEMORY.md")
                       if path.is_file()]
        claude_root = self.claude_root
        cursor_root = self.home / ".cursor"
        gemini_root = self.home / ".gemini"
        codex_home = self.source.parent
        catalog = [
            {"id": "codex", "name": "Codex", "description": "本地记忆摘要与任务索引",
             "path": str(self.source), "installed": self.source.is_dir(), "files": codex_files},
            {"id": "claude", "name": "Claude Code", "description": "自动记忆、全局与项目 CLAUDE.md、.claude/rules",
             "path": str(claude_root), "installed": claude_root.is_dir(),
             "files": claude_memory_files(claude_root, workspaces)},
            {"id": "cursor", "name": "Cursor 规则", "description": "工作区项目规则；不包含应用内 Memories 或聊天记录",
             "path": str(cursor_root),
             "installed": cursor_root.is_dir() or cursor_app_dir(self.home, self.use_host_paths).is_dir(),
             "files": cursor_rule_files(cursor_root, workspaces)},
            {"id": "gemini", "name": "Gemini CLI", "description": "全局与项目 GEMINI.md",
             "path": str(gemini_root), "installed": gemini_root.is_dir(),
             "files": named_files([gemini_root / "GEMINI.md", *(item / "GEMINI.md" for item in workspaces)])},
            {"id": "agents", "name": "AGENTS.md", "description": "通用 Agent 指令：AGENTS.md、.github/copilot-instructions.md",
             "path": f"{codex_home} + 项目目录", "installed": False,
             "missing": "扫描的项目目录中没有 AGENTS.md",
             "files": named_files([codex_home / "AGENTS.md",
                                   *(item / "AGENTS.md" for item in workspaces),
                                   *(item / ".github" / "copilot-instructions.md" for item in workspaces)])},
        ]
        for source_id, path in self.store.custom_sources().items():
            catalog.append({"id": source_id, "name": path.name or str(path),
                            "description": "手动添加的 Markdown / 文本记忆文件夹",
                            "path": str(path), "installed": path.is_dir(),
                            "files": import_files(path), "custom": True})
        for item in catalog:
            item["file_count"] = len(item["files"])
            item["available"] = bool(item["files"])
            item["selected"] = item["id"] in selected
            item["status"] = "可导入" if item["available"] else item.get("missing") or (
                "已检测到工具，但未找到支持的记忆或规则文件" if item["installed"] else "未检测到工具目录")
            item["examples"] = [str(path) for path in item["files"][:3]]
        return catalog

    def sources(self) -> dict:
        return {"sources": [{key: value for key, value in item.items() if key != "files"}
                            for item in self.source_catalog(refresh=True)],
                "workspace_count": len(self.workspaces())}

    def add_source(self, raw_path: str) -> dict:
        if len(raw_path) > 2048:
            raise ValueError("路径过长")
        path = Path(raw_path.strip().strip('"')).expanduser()
        if not path.is_absolute() or not path.is_dir() or path.is_symlink() \
                or getattr(path, "is_junction", lambda: False)():
            raise ValueError("请输入本机已存在的记忆文件夹绝对路径")
        path = path.resolve()
        if not import_files(path):
            raise ValueError("文件夹中没有可导入的 .md、.mdc 或 .txt 文件")
        source_id = "custom:" + memory_id(str(path).casefold())
        self.store.add_custom_source(source_id, path)
        return self.sources()

    def remove_source(self, source_id: str) -> dict:
        if source_id not in self.store.custom_sources():
            raise ValueError("只能移除手动添加的来源")
        self.store.remove_custom_source(source_id)
        self.reload()
        return self.sources()

    def select_sources(self, source_ids: list[str]) -> dict:
        if len(source_ids) > 50 or not all(isinstance(item, str) for item in source_ids):
            raise ValueError("来源列表无效")
        catalog = self.source_catalog(refresh=True)
        known = {item["id"] for item in catalog}
        available = {item["id"] for item in catalog if item["available"]}
        requested = set(source_ids)
        if requested - known or requested - (available | self.store.enabled_sources()):
            raise ValueError("包含未知或不可导入的来源")
        if sum(item["file_count"] for item in catalog if item["id"] in requested) > MAX_TOTAL_IMPORT_FILES:
            raise ValueError("所选来源超过 250 个文件，请减少勾选范围")
        self.store.set_sources(requested, known)
        self.reload()
        return {"count": len(self.memories), **self.sources()}

    def _project_for(self, source_id: str, path: Path, tool: str, known: dict[str, Path]) -> str:
        if source_id == "claude":
            if path == self.claude_root / "CLAUDE.md" or path.parent == self.claude_root / "rules":
                return "Claude 全局"
            if path.parent.name == "memory" and path.parent.parent.parent.name == "projects":
                return "Claude / " + friendly_slug(path.parent.parent.name, known)
            return "Claude / " + workspace_name(path)
        if source_id == "cursor":
            return "Cursor 规则"
        if source_id == "gemini":
            return "Gemini 全局" if path.parent == self.home / ".gemini" else "Gemini / " + workspace_name(path)
        if source_id == "agents":
            return "Agent 全局指令" if path.parent == self.source.parent else "AGENTS / " + workspace_name(path)
        return "导入 / " + tool

    def reload(self) -> None:
        memories: list[Memory] = []
        seen_files: set[Path] = set()
        known = {slug_of(item): item for item in self.workspaces(refresh=True)}
        for item in self.source_catalog():
            if not item["selected"]:
                continue
            if item["id"] == "codex":
                memories.extend(load_memories(self.source))
                seen_files.update(path.resolve() for path in item["files"])
                continue
            for path in item["files"]:
                resolved = path.resolve()
                if resolved in seen_files:
                    continue
                seen_files.add(resolved)
                memory = parse_import_file(path, item["name"], self._project_for(item["id"], path, item["name"], known))
                if memory:
                    memories.append(memory)
        self.memories = sorted(memories, key=lambda item: (item.project, item.kind, item.title))
        self.network = Network(self.memories, self.store)

    def state(self) -> dict:
        feedback = self.store.all()
        hubs, links = build_links(self.memories)
        items = []
        for memory in self.memories:
            item = asdict(memory)
            item["preview"] = memory.preview
            item["feedback"] = {key: feedback.get(memory.id, {}).get(key, value)
                                for key, value in {"boost": 0, "pinned": 0,
                                                   "archived": 0, "correction": ""}.items()}
            item["strength"] = round(effective_strength(memory, item["feedback"]), 3)
            items.append(item)
        active = [item["name"] for item in self.source_catalog() if item["selected"] and item["available"]]
        return {"source": " + ".join(active) or "尚未选择来源", "source_exists": bool(active),
                "active_sources": active,
                "count": len(items), "memories": items, "hubs": hubs, "links": links,
                "network": self.network.snapshot(self.network.labels(feedback))}

    def search(self, query: str) -> dict:
        return {"query": query,
                "results": rank_memories(self.memories, self.store.all(), query, self.network)[:30]}

    def network_state(self) -> dict:
        return self.network.snapshot(self.network.labels(self.store.all()))

    def _log(self, event: str) -> None:
        accuracy, _ = self.network.accuracy(self.network.labels(self.store.all()))
        self.store.log_event(event, self.network.epoch, self.network.generation, self.network.loss, accuracy)

    def learn_step(self) -> dict:
        loss = self.network.learn_step(self.network.labels(self.store.all()))
        self._log("learn")
        return {"loss": loss, "converged": loss < Network.CONVERGED_LOSS, "network": self.network_state()}

    def evolve(self, action: str) -> dict:
        if action == "prune":
            changed = self.network.prune()
        elif action == "grow":
            changed = self.network.evolve(self.network.labels(self.store.all()))
        else:
            raise ValueError("未知进化操作")
        self._log(action)
        return {"changed": changed, "network": self.network_state()}

    def feedback(self, memory_id_value: str, action: str, correction: str = "") -> dict:
        if not any(memory.id == memory_id_value for memory in self.memories):
            raise ValueError("记忆不存在，请先同步数据")
        if len(correction) > 2000:
            raise ValueError("修正内容最多 2000 字")
        return self.store.change(memory_id_value, action, correction)


class AtlasHandler(BaseHTTPRequestHandler):
    atlas: Atlas
    server_version = "MemoryAtlas/0.1"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        # Queries and corrections may contain private memory text.
        return

    def _valid_host(self) -> bool:
        host = self.headers.get("Host", "").lower()
        return host in {f"127.0.0.1:{self.server.server_port}",
                        f"localhost:{self.server.server_port}"}

    def _send(self, data: bytes, status: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, value: dict, status: int = 200) -> None:
        self._send(json.dumps(value, ensure_ascii=False).encode("utf-8"), status,
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if not self._valid_host():
            self._json({"error": "Invalid host"}, 403)
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._json(self.atlas.state())
        elif parsed.path == "/api/sources":
            self._json(self.atlas.sources())
        elif parsed.path == "/api/search":
            query = parse_qs(parsed.query).get("q", [""])[0][:200]
            self._json(self.atlas.search(query))
        elif parsed.path in {"/", "/index.html", "/styles.css", "/app.js"}:
            filename = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
            content_type = {"index.html": "text/html", "styles.css": "text/css",
                            "app.js": "text/javascript"}[filename]
            self._send((STATIC / filename).read_bytes(), 200, content_type + "; charset=utf-8")
        elif parsed.path == "/favicon.ico":
            self._send(b"", 204, "image/x-icon")
        else:
            self._json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        if not self._valid_host():
            self._json({"error": "Invalid host"}, 403)
            return
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}",
                                    f"http://localhost:{self.server.server_port}"}:
            self._json({"error": "Invalid origin"}, 403)
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self._json({"error": "JSON required"}, 415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 8192:
                raise ValueError("请求过大")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("请求格式错误")
            if self.path == "/api/feedback":
                result = self.atlas.feedback(str(body.get("id", "")),
                                             str(body.get("action", "")),
                                             str(body.get("correction", "")))
                self._json({"feedback": result})
            elif self.path == "/api/learn/step":
                self._json(self.atlas.learn_step())
            elif self.path == "/api/evolve":
                self._json(self.atlas.evolve(str(body.get("action", ""))))
            elif self.path == "/api/sync":
                self.atlas.reload()
                self._json({"count": len(self.atlas.memories)})
            elif self.path == "/api/sources/add":
                self._json(self.atlas.add_source(str(body.get("path", ""))))
            elif self.path == "/api/sources/remove":
                self._json(self.atlas.remove_source(str(body.get("id", ""))))
            elif self.path == "/api/sources/select":
                ids = body.get("ids")
                if not isinstance(ids, list):
                    raise ValueError("来源列表无效")
                self._json(self.atlas.select_sources(ids))
            else:
                self._json({"error": "Not found"}, 404)
        except (ValueError, json.JSONDecodeError) as error:
            self._json({"error": str(error)}, 400)


def main() -> None:
    parser = argparse.ArgumentParser(description="Explore selected local AI memories")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="Codex memory directory, read only")
    parser.add_argument("--database", type=Path, default=DEFAULT_DB,
                        help="Separate SQLite feedback database")
    parser.add_argument("--workspace", type=Path, action="append", default=[],
                        help="Extra project folder (or a folder of projects) to scan for CLAUDE.md, "
                             ".cursor/rules, GEMINI.md, AGENTS.md; may be repeated")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    atlas = Atlas(args.source, args.database, workspaces=args.workspace)
    handler = type("BoundAtlasHandler", (AtlasHandler,), {"atlas": atlas})
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"Memory Atlas: http://127.0.0.1:{server.server_port}")
    print(f"Read-only sources: {atlas.state()['source']} ({len(atlas.memories)} records)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
