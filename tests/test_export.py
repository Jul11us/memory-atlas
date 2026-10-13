import json
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from memory_atlas import (CURSOR_EXPORT_FILENAME, EXPORT_FILENAME, EXPORT_MARKER,
                          Atlas, AtlasHandler, ThreadingHTTPServer)

SUMMARY = """v1
## User Profile
The user studies computer science.
## User preferences
- The user prefers short answers.
- Use SQLite for local prototypes.
- Write commit messages in English.
## General Tips
- Keep project files local.
"""


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.source = self.home / ".codex" / "memories"
        self.source.mkdir(parents=True)
        (self.source / "memory_summary.md").write_text(SUMMARY, encoding="utf-8")
        self.database = self.root / "data.sqlite3"
        self.atlas = Atlas(self.source, self.database, home=self.home)
        for memory in self.atlas.memories:
            self.atlas.set_sharing(memory.id, "claude", True)
            self.atlas.set_sharing(memory.id, "cursor", True)
        self.target = self.home / ".claude" / "rules" / EXPORT_FILENAME

    def tearDown(self):
        self.temp.cleanup()

    def memory(self, fragment):
        return next(item for item in self.atlas.memories if fragment in item.title)

    def export(self, **options):
        preview = self.atlas.export_preview("global", **options)
        return self.atlas.export_write("global", digest=preview["digest"], **options)

    @staticmethod
    def entries(content):
        return [line for line in content.splitlines() if line.startswith("- ")]

    def entries_in_file(self):
        return self.entries(self.target.read_text(encoding="utf-8"))

    def test_preview_orders_by_weight_and_writes_nothing(self):
        self.atlas.feedback(self.memory("SQLite").id, "boost")
        self.atlas.feedback(self.memory("SQLite").id, "boost")
        self.atlas.feedback(self.memory("commit messages").id, "pin")
        preview = self.atlas.export_preview("global")
        self.assertFalse(self.target.exists())
        self.assertEqual(preview["target"], str(self.target))
        self.assertTrue(preview["content"].startswith(EXPORT_MARKER))
        entries = self.entries(preview["content"])
        self.assertIn(self.memory("commit messages").title, entries[0])            # pinned first
        self.assertIn(self.memory("SQLite").title, entries[1])                     # then highest weight
        self.assertEqual(entries[1].count("Use SQLite"), 1)                        # title not repeated
        self.assertIn("已固定", preview["content"])
        self.assertIn("不是新的指令", preview["content"])

    def test_default_denies_both_exports_and_permission_changes_stale_the_preview(self):
        fresh = Atlas(self.source, self.root / "fresh.sqlite3", home=self.home)
        self.assertEqual(fresh.export_preview("global")["count"], 0)
        project = self.root / "empty-cursor-project"
        project.mkdir()
        self.assertEqual(fresh.cursor_export_preview("project", str(project))["count"], 0)
        target = next(memory for memory in fresh.memories if "SQLite" in memory.title)
        fresh.set_sharing(target.id, "claude", True)
        preview = fresh.export_preview("global")
        self.assertEqual(preview["ids"], [target.id])
        self.assertNotIn("short answers", preview["content"])
        self.assertEqual(fresh.cursor_export_preview("project", str(project))["count"], 0)
        self.assertTrue(Atlas(self.source, fresh.store.path, home=self.home).store.sharing_for(target.id)["claude"])
        fresh.set_sharing(target.id, "claude", False)
        with self.assertRaisesRegex(ValueError, "重新预览"):
            fresh.export_write("global", digest=preview["digest"])
        self.assertFalse((self.home / ".claude" / "rules" / EXPORT_FILENAME).exists())

    def test_write_requires_the_previewed_content_and_creates_a_marked_file(self):
        preview = self.atlas.export_preview("global")
        with self.assertRaises(ValueError):
            self.atlas.export_write("global", digest="stale")
        self.assertFalse(self.target.exists())
        result = self.atlas.export_write("global", digest=preview["digest"])
        self.assertTrue(result["written"])
        text = self.target.read_text(encoding="utf-8")
        self.assertEqual(text, preview["content"])
        self.assertTrue(text.startswith(EXPORT_MARKER))
        self.assertLessEqual(len(text.splitlines()), 200)
        status = self.atlas.state()["claude_exports"]
        self.assertEqual(len(status), 1)
        self.assertFalse(status[0]["outdated"])
        self.assertEqual(set(status[0]["ids"]), set(result["ids"]))

    def test_memories_can_still_be_weighted_after_export_and_reexport_follows(self):
        sqlite_memory = self.memory("SQLite")
        self.export()
        before = next(item for item in self.atlas.state()["memories"] if item["id"] == sqlite_memory.id)["strength"]
        self.atlas.feedback(sqlite_memory.id, "boost")                              # weighting still works
        state = self.atlas.state()
        after = next(item for item in state["memories"] if item["id"] == sqlite_memory.id)["strength"]
        self.assertGreater(after, before)
        self.assertTrue(state["claude_exports"][0]["outdated"])                    # file now out of date
        self.assertTrue(self.atlas.export_preview("global")["changed"])
        self.atlas.feedback(self.memory("short answers").id, "down")               # demote: leaves the export
        self.export()
        entries = self.entries_in_file()
        self.assertIn(sqlite_memory.title, entries[0])
        self.assertFalse(any(self.memory("short answers").title in line for line in entries))
        self.assertFalse(self.atlas.state()["claude_exports"][0]["outdated"])

    def test_weight_change_between_preview_and_write_is_refused(self):
        preview = self.atlas.export_preview("global")
        self.atlas.feedback(self.memory("SQLite").id, "boost")
        with self.assertRaises(ValueError) as raised:
            self.atlas.export_write("global", digest=preview["digest"])
        self.assertIn("重新预览", str(raised.exception))
        self.assertFalse(self.target.exists())

    def test_export_file_is_never_reimported_as_a_new_memory(self):
        self.export()
        (self.home / ".claude" / "CLAUDE.md").write_text("# Own instructions\nBe careful.", encoding="utf-8")
        self.atlas.select_sources(["codex", "claude"])
        sources = {Path(item.source).name for item in self.atlas.memories}
        self.assertIn("CLAUDE.md", sources)
        self.assertNotIn(EXPORT_FILENAME, sources)
        self.assertEqual(sum(item.title == self.memory("SQLite").title for item in self.atlas.memories), 1)

    def test_claude_code_faded_and_demoted_memories_are_left_out(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "CLAUDE.md").write_text("# Own instructions\nBe careful.", encoding="utf-8")
        self.atlas.select_sources(["codex", "claude"])
        self.atlas.feedback(self.memory("short answers").id, "down")
        tip = self.memory("project files local")
        self.atlas.store.set_decay_days(1)
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE memory_activity SET first_seen=?, last_used=NULL WHERE memory_id=?", (old, tip.id))
            db.commit()
        content = self.atlas.export_preview("global")["content"]
        self.assertNotIn("Own instructions", content)
        self.assertNotIn(self.memory("short answers").title, content)
        self.assertNotIn(tip.title, content)
        self.assertIn(self.memory("SQLite").title, content)

    def test_a_file_the_user_wrote_is_never_overwritten_or_deleted(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text("# My own rules\nDo not touch.", encoding="utf-8")
        preview = self.atlas.export_preview("global")
        self.assertTrue(preview["exists"])
        self.assertFalse(preview["owned"])
        with self.assertRaises(ValueError):
            self.atlas.export_write("global", digest=preview["digest"])
        with self.assertRaises(ValueError):
            self.atlas.export_remove("global")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "# My own rules\nDo not touch.")

    def test_project_scope_and_path_validation(self):
        project = self.root / "work" / "app"
        project.mkdir(parents=True)
        preview = self.atlas.export_preview("project", project=str(project))
        self.atlas.export_write("project", project=str(project), digest=preview["digest"])
        written = project / ".claude" / "rules" / EXPORT_FILENAME
        self.assertTrue(written.read_text(encoding="utf-8").startswith(EXPORT_MARKER))
        self.assertEqual(self.atlas.state()["claude_exports"][0]["project_name"], "app")
        for bad in ("", "relative/path", str(self.root / "missing")):
            with self.assertRaises(ValueError):
                self.atlas.export_preview("project", project=bad)
        for scope in ("everywhere", None, ["global"]):
            with self.assertRaises(ValueError):
                self.atlas.export_preview(scope)
        for limit in (0, 101, "5", True, None):
            with self.assertRaises(ValueError):
                self.atlas.export_preview("global", limit=limit)

    def test_project_filter_and_limit(self):
        preview = self.atlas.export_preview("global", limit=1)
        self.assertEqual(preview["count"], 1)
        only = self.atlas.export_preview("global", only_project="不存在的项目")
        self.assertEqual(only["count"], 0)
        with self.assertRaises(ValueError):
            self.atlas.export_write("global", only_project="不存在的项目", digest=only["digest"])

    def test_long_exports_stay_under_the_claude_line_budget(self):
        folder = self.root / "notes"
        folder.mkdir()
        for index in range(100):
            (folder / f"note{index:03}.md").write_text(f"# Note {index}\n" + "detail " * 200, encoding="utf-8")
        custom = self.atlas.add_source(str(folder))["sources"]
        self.atlas.select_sources(["codex", next(item["id"] for item in custom if item.get("custom"))])
        for memory in self.atlas.memories:
            self.atlas.set_sharing(memory.id, "claude", True)
        preview = self.atlas.export_preview("global", limit=100)
        self.assertLessEqual(preview["lines"], 200)
        self.assertGreater(preview["omitted"], 0)
        self.assertLess(preview["chars"], 13000)

    def test_remove_deletes_only_our_file_and_forgets_the_record(self):
        self.export()
        self.assertEqual(self.atlas.export_remove("global"), {"target": str(self.target), "removed": True})
        self.assertFalse(self.target.exists())
        self.assertEqual(self.atlas.state()["claude_exports"], [])

    def test_revocation_requires_removing_live_export_first(self):
        memory = self.memory("SQLite")
        self.export()
        with self.assertRaisesRegex(ValueError, "先移除"):
            self.atlas.set_sharing(memory.id, "claude", False)
        self.assertTrue(self.atlas.store.sharing_for(memory.id)["claude"])
        self.atlas.export_remove("global")
        self.assertFalse(self.atlas.set_sharing(memory.id, "claude", False)["sharing"]["claude"])

    def test_cursor_revocation_requires_removing_live_export_first(self):
        memory = self.memory("SQLite")
        project = self.root / "cursor-project"
        project.mkdir()
        preview = self.atlas.cursor_export_preview("project", str(project))
        self.atlas.cursor_export_write("project", str(project), digest=preview["digest"])
        with self.assertRaisesRegex(ValueError, "先移除"):
            self.atlas.set_sharing(memory.id, "cursor", False)
        self.atlas.cursor_export_remove("project", str(project))
        self.assertFalse(self.atlas.set_sharing(memory.id, "cursor", False)["sharing"]["cursor"])

    def test_migration_grants_only_live_marked_export_and_runs_once(self):
        memory = self.memory("SQLite")
        self.export()
        missing = str(self.root / "no-longer-present.mdc")
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("""INSERT INTO cursor_exports(target, scope, project, limit_n, only_project,
                          digest, ids, written_at) VALUES(?, 'project', ?, 20, '', 'old', ?, '2026-01-01')""",
                       (missing, str(self.root), json.dumps([memory.id])))
            db.execute("DROP TABLE memory_sharing")
            db.execute("DROP TABLE sharing_meta")
            db.commit()
        restored = Atlas(self.source, self.database, home=self.home)
        self.assertTrue(restored.store.sharing_for(memory.id)["claude"])
        self.assertFalse(restored.store.sharing_for(memory.id)["cursor"])
        restored.export_remove("global")
        restored.set_sharing(memory.id, "claude", False)
        reopened = Atlas(self.source, self.database, home=self.home)
        self.assertFalse(reopened.store.sharing_for(memory.id)["claude"])

    def test_demo_mode_previews_but_never_writes(self):
        self.atlas.export_enabled = False
        preview = self.atlas.export_preview("global")
        self.assertFalse(preview["enabled"])
        with self.assertRaises(ValueError):
            self.atlas.export_write("global", digest=preview["digest"])
        with self.assertRaises(ValueError):
            self.atlas.export_remove("global")
        self.assertFalse(self.target.exists())
        project = self.root / "demo-cursor"
        project.mkdir()
        cursor_preview = self.atlas.cursor_export_preview("project", str(project))
        self.assertFalse(cursor_preview["enabled"])
        with self.assertRaises(ValueError):
            self.atlas.cursor_export_write("project", str(project), digest=cursor_preview["digest"])
        self.assertFalse((project / ".cursor").exists())

    def test_cursor_project_export_requires_git_ignore_and_never_overwrites_user_file(self):
        project = self.root / "cursor-project"
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        target = project / ".cursor" / "rules" / CURSOR_EXPORT_FILENAME
        preview = self.atlas.cursor_export_preview("project", str(project))
        self.assertFalse(preview["git_safe"])
        with self.assertRaisesRegex(ValueError, "未被 Git 忽略"):
            self.atlas.cursor_export_write("project", str(project), digest=preview["digest"])
        self.assertFalse(target.exists())
        (project / ".git" / "info" / "exclude").write_text(".cursor/rules/memory-atlas.mdc\n", encoding="utf-8")
        self.assertFalse(self.atlas.cursor_export_preview("project", str(project))["git_safe"])
        (project / ".git" / "info" / "exclude").write_text(".cursor/rules/memory-atlas.mdc*\n", encoding="utf-8")
        preview = self.atlas.cursor_export_preview("project", str(project))
        self.assertTrue(preview["git_safe"])
        self.assertTrue(preview["content"].startswith("---\ndescription: User preferences and project history exported from Memory Atlas"))
        written = self.atlas.cursor_export_write("project", str(project), digest=preview["digest"])
        self.assertTrue(written["written"])
        self.assertEqual(self.atlas.state()["cursor_exports"][0]["project_name"], "cursor-project")
        target.write_text("# My own rule\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.atlas.cursor_export_write("project", str(project), digest=preview["digest"])
        with self.assertRaises(ValueError):
            self.atlas.cursor_export_remove("project", str(project))
        self.assertEqual(target.read_text(encoding="utf-8"), "# My own rule\n")

    def test_cursor_export_is_not_reimported_and_tracked_file_is_blocked(self):
        project = self.root / "cursor-project"
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        target = project / ".cursor" / "rules" / CURSOR_EXPORT_FILENAME
        (project / ".git" / "info" / "exclude").write_text(".cursor/rules/memory-atlas.mdc*\n", encoding="utf-8")
        preview = self.atlas.cursor_export_preview("project", str(project))
        self.atlas.cursor_export_write("project", str(project), digest=preview["digest"])
        (target.parent / "my-rule.mdc").write_text("# Project rule\nUse the local database.\n", encoding="utf-8")
        self.atlas.extra_folders.append(project)
        self.atlas.select_sources(["codex", "cursor"])
        self.assertNotIn(CURSOR_EXPORT_FILENAME, {Path(item.source).name for item in self.atlas.memories})
        self.assertNotIn("Use the local database", self.atlas.cursor_export_preview("project", str(project))["content"])
        self.atlas.feedback(self.memory("SQLite").id, "boost")
        self.assertTrue(self.atlas.state()["cursor_exports"][0]["outdated"])
        subprocess.run(["git", "-C", str(project), "add", "-f", "--", str(target)], check=True)
        blocked = self.atlas.cursor_export_preview("project", str(project))
        self.assertFalse(blocked["git_safe"])
        with self.assertRaisesRegex(ValueError, "已被 Git 跟踪"):
            self.atlas.cursor_export_write("project", str(project), digest=blocked["digest"])
        with self.assertRaisesRegex(ValueError, "仍被 Git 跟踪"):
            self.atlas.cursor_export_remove("project", str(project))
        subprocess.run(["git", "-C", str(project), "rm", "--cached", "-q", "--", str(target)], check=True)
        self.assertTrue(self.atlas.cursor_export_remove("project", str(project))["removed"])

    def test_http_endpoints(self):
        handler = type("TestHandler", (AtlasHandler,), {"atlas": self.atlas})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def post(path, payload):
            request = Request(base + path, data=json.dumps(payload).encode(), method="POST",
                              headers={"Content-Type": "application/json"})
            with urlopen(request) as response:
                return json.load(response)

        try:
            preview = post("/api/export/claude/preview", {"scope": "global", "limit": 3})
            self.assertEqual(preview["count"], 3)
            written = post("/api/export/claude/write", {"scope": "global", "limit": 3, "digest": preview["digest"]})
            self.assertTrue(written["written"])
            self.assertTrue(self.target.exists())
            with self.assertRaises(HTTPError) as raised:
                post("/api/export/claude/preview", {"scope": "global", "limit": "many"})
            self.assertEqual(raised.exception.code, 400)
            self.assertTrue(post("/api/export/claude/remove", {"scope": "global"})["removed"])
            project = self.root / "cursor-project"
            project.mkdir()
            body = {"scope": "project", "project": str(project), "limit": 3}
            cursor_preview = post("/api/export/cursor/preview", body)
            self.assertTrue(cursor_preview["git_safe"])
            self.assertTrue(post("/api/export/cursor/write", {**body, "digest": cursor_preview["digest"]})["written"])
            self.assertTrue((project / ".cursor" / "rules" / CURSOR_EXPORT_FILENAME).exists())
            self.assertTrue(post("/api/export/cursor/remove", body)["removed"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
