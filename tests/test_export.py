import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from memory_atlas import EXPORT_FILENAME, EXPORT_MARKER, Atlas, AtlasHandler, ThreadingHTTPServer

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
        preview = self.atlas.export_preview("global", limit=100)
        self.assertLessEqual(preview["lines"], 200)
        self.assertGreater(preview["omitted"], 0)
        self.assertLess(preview["chars"], 13000)

    def test_remove_deletes_only_our_file_and_forgets_the_record(self):
        self.export()
        self.assertEqual(self.atlas.export_remove("global"), {"target": str(self.target), "removed": True})
        self.assertFalse(self.target.exists())
        self.assertEqual(self.atlas.state()["claude_exports"], [])

    def test_demo_mode_previews_but_never_writes(self):
        self.atlas.export_enabled = False
        preview = self.atlas.export_preview("global")
        self.assertFalse(preview["enabled"])
        with self.assertRaises(ValueError):
            self.atlas.export_write("global", digest=preview["digest"])
        with self.assertRaises(ValueError):
            self.atlas.export_remove("global")
        self.assertFalse(self.target.exists())

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
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
