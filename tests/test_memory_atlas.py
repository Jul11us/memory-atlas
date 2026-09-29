import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from memory_atlas import (Atlas, AtlasHandler, FeedbackStore, Memory, Network, ThreadingHTTPServer,
                          edge_key, rank_memories)


SUMMARY = """v1
## User Profile
The user studies computer science.
## User preferences
- The user prefers short answers.
## General Tips
- Keep project files local.
## What's in Memory
### C:\\Users\\Example\\Desktop\\Project A
#### 2026-09-28
- Old address: New York
  - desc: An outdated location note.
  - learnings: The user used to live in New York.
"""

REGISTRY = """# Task Group: Project A research
scope: Build a private memory prototype.
applies_to: cwd=C:\\Users\\Example\\Desktop\\Project A; reuse_rule=local

## Task 1: Implement private index, success
### rollout_summary_files
- rollout_summaries/example.md (updated_at=2026-09-28T00:00:00+00:00)
### keywords
- memory, retrieval, private index
## User preferences
- Keep the private index local. [Task 1]
"""


class AtlasTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "memories"
        self.source.mkdir()
        (self.source / "memory_summary.md").write_text(SUMMARY, encoding="utf-8")
        (self.source / "MEMORY.md").write_text(REGISTRY, encoding="utf-8")
        (self.source / "secret.jsonl").write_text("PRIVATE SESSION CONTENT", encoding="utf-8")
        self.atlas = Atlas(self.source, self.root / "data" / "feedback.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_only_selected_summary_files(self):
        self.assertEqual(len(self.atlas.memories), 5)
        self.assertFalse(any("PRIVATE SESSION CONTENT" in item.text for item in self.atlas.memories))
        self.assertEqual({Path(item.source).name for item in self.atlas.memories},
                         {"MEMORY.md", "memory_summary.md"})

    def test_correction_replaces_old_search_text_and_preserves_source(self):
        before = (self.source / "memory_summary.md").read_bytes()
        old = next(item for item in self.atlas.memories if "Old address" in item.title)
        original_strength = next(item["strength"] for item in self.atlas.state()["memories"] if item["id"] == old.id)
        self.assertIn(old.id, [row["id"] for row in self.atlas.search("New York")["results"]])
        self.atlas.feedback(old.id, "correct", "Current address: Boston")
        self.assertNotIn(old.id, [row["id"] for row in self.atlas.search("New York")["results"]])
        self.assertIn(old.id, [row["id"] for row in self.atlas.search("Boston")["results"]])
        corrected_strength = next(item["strength"] for item in self.atlas.state()["memories"] if item["id"] == old.id)
        self.assertGreater(corrected_strength, original_strength)
        self.assertEqual(before, (self.source / "memory_summary.md").read_bytes())
        self.assertEqual(self.atlas.state()["count"], 5)

    def test_feedback_changes_strength_without_self_reinforcement(self):
        target = next(item for item in self.atlas.memories if item.kind == "task")
        before = self.atlas.state()["memories"]
        base = next(item["strength"] for item in before if item["id"] == target.id)
        self.atlas.search("private index")
        self.atlas.search("private index")
        unchanged = next(item["strength"] for item in self.atlas.state()["memories"] if item["id"] == target.id)
        self.assertEqual(base, unchanged)
        self.atlas.feedback(target.id, "boost")
        boosted = next(item["strength"] for item in self.atlas.state()["memories"] if item["id"] == target.id)
        self.assertGreater(boosted, base)
        self.assertGreater(rank_memories([target], self.atlas.store.all(), "private")[0]["score"], 0)

    def test_selected_ai_sources_merge_and_persist_without_changing_files(self):
        claude_dir = self.root / ".claude" / "projects" / "sample-project" / "memory"
        claude_dir.mkdir(parents=True)
        claude_file = claude_dir / "MEMORY.md"
        claude_file.write_text("# Claude decision\nUse the offline index.", encoding="utf-8")
        cursor_dir = self.root / ".cursor" / "rules"
        cursor_dir.mkdir(parents=True)
        cursor_file = cursor_dir / "style.mdc"
        cursor_file.write_text("---\ndescription: style\n---\n# Cursor preference\nKeep answers concise.", encoding="utf-8")
        workspace = self.root / "Desktop" / "metrik"
        workspace.mkdir(parents=True)
        project_claude = workspace / "CLAUDE.md"
        project_claude.write_text("# Project conventions\nKeep tests focused.", encoding="utf-8")
        project_cursor = workspace / ".cursor" / "rules"
        project_cursor.mkdir(parents=True)
        (project_cursor / "project.mdc").write_text("# Project rule\nUse local data.", encoding="utf-8")
        atlas = Atlas(self.source, self.root / "data" / "sources.sqlite3", home=self.root)
        self.assertEqual(atlas.state()["count"], 5)
        catalog = {item["id"]: item for item in atlas.sources()["sources"]}
        self.assertEqual(catalog["claude"]["file_count"], 2)
        self.assertEqual(catalog["cursor"]["file_count"], 2)
        self.assertFalse(catalog["claude"]["selected"])
        original = claude_file.read_bytes(), cursor_file.read_bytes(), project_claude.read_bytes()
        atlas.select_sources(["codex", "claude", "cursor"])
        self.assertEqual(atlas.state()["count"], 9)
        self.assertIn("Claude decision", [item.title for item in atlas.memories])
        self.assertIn("Cursor preference", [item.title for item in atlas.memories])
        self.assertIn("Project conventions", [item.title for item in atlas.memories])
        self.assertEqual({item.tool for item in atlas.memories}, {"Codex", "Claude Code", "Cursor 规则"})
        restored = Atlas(self.source, self.root / "data" / "sources.sqlite3", home=self.root)
        self.assertEqual(restored.state()["count"], 9)
        self.assertEqual(original, (claude_file.read_bytes(), cursor_file.read_bytes(), project_claude.read_bytes()))

    def test_detected_tool_with_no_files_has_explicit_status(self):
        (self.root / ".claude").mkdir()
        (self.root / ".cursor").mkdir()
        atlas = Atlas(self.source, self.root / "data" / "status.sqlite3", home=self.root)
        sources = {item["id"]: item for item in atlas.sources()["sources"]}
        for source_id in ("claude", "cursor"):
            self.assertTrue(sources[source_id]["installed"])
            self.assertFalse(sources[source_id]["available"])
            self.assertIn("已检测到工具", sources[source_id]["status"])

    def test_custom_source_requires_explicit_selection_and_skips_unrelated_files(self):
        folder = self.root / "other-ai-memory"
        folder.mkdir()
        (folder / "note.txt").write_text("# Imported note\nA private preference.", encoding="utf-8")
        (folder / "session.jsonl").write_text("SECRET SESSION", encoding="utf-8")
        atlas = Atlas(self.source, self.root / "data" / "custom.sqlite3", home=self.root)
        with self.assertRaises(ValueError):
            atlas.add_source("relative/path")
        added = atlas.add_source(str(folder))["sources"]
        custom = next(item for item in added if item.get("custom"))
        self.assertEqual(custom["file_count"], 1)
        self.assertFalse(custom["selected"])
        self.assertEqual(atlas.state()["count"], 5)
        with self.assertRaises(ValueError):
            atlas.select_sources(["unknown"])
        atlas.select_sources(["codex", custom["id"]])
        self.assertEqual(atlas.state()["count"], 6)
        self.assertFalse(any("SECRET SESSION" in item.text for item in atlas.memories))
        atlas.remove_source(custom["id"])
        self.assertEqual(atlas.state()["count"], 5)
        atlas.select_sources([])
        self.assertEqual(atlas.state()["count"], 0)
        atlas.select_sources(["codex"])
        self.assertEqual(atlas.state()["count"], 5)

    def test_local_http_api_rejects_foreign_origin(self):
        handler = type("TestHandler", (AtlasHandler,), {"atlas": self.atlas})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base + "/api/state") as response:
                self.assertEqual(json.load(response)["count"], 5)
            request = Request(base + "/api/sync", data=b"{}", method="POST",
                              headers={"Content-Type": "application/json", "Origin": "https://example.com"})
            with self.assertRaises(HTTPError) as raised:
                urlopen(request)
            self.assertEqual(raised.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class DiscoveryTests(unittest.TestCase):
    """A stranger's machine: projects in ~/code, OneDrive desktop, outside the home folder, other agents."""

    def write(self, path, text="# Note\nKeep answers concise."):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.home = base / "home"
        self.outside = base / "elsewhere"
        self.home.mkdir()
        self.source = self.home / ".codex" / "memories"
        self.source.mkdir(parents=True)
        (self.source / "memory_summary.md").write_text(SUMMARY, encoding="utf-8")
        self.registry = REGISTRY.replace("C:\\Users\\Example\\Desktop\\Project A", str(self.outside / "recorded"))
        self.project = self.home / "code" / "myproj"
        self.write(self.project / "CLAUDE.md", "# Project rules\nUse tests.")
        self.write(self.project / "CLAUDE.local.md", "# Local rules\nPrivate note.")
        self.write(self.project / ".claude" / "rules" / "style.md", "# Style\nTerse.")
        self.write(self.project / ".cursor" / "rules" / "nested" / "deep.md", "# Deep cursor rule\nShort.")
        self.write(self.project / "GEMINI.md", "# Gemini project\nBe brief.")
        self.write(self.project / "AGENTS.md", "# Agents\nRun the tests.")
        self.write(self.project / ".github" / "copilot-instructions.md", "# Copilot\nUse types.")
        self.write(self.home / ".claude" / "rules" / "global.md", "# Global rule\nAlways local.")
        self.write(self.home / ".gemini" / "GEMINI.md", "# Gemini global\nAnswer plainly.")
        self.write(self.home / "OneDrive" / "Desktop" / "onedrive-app" / "CLAUDE.md", "# OneDrive project\nSynced.")
        self.write(self.outside / "recorded" / "CLAUDE.md", "# Recorded project\nFound through Codex.")
        self.write(self.outside / "flagged" / "child" / "CLAUDE.md", "# Flagged child\nFound through a flag.")
        slug = "".join(char if char.isalnum() and char.isascii() else "-" for char in str(self.project))
        self.write(self.home / ".claude" / "projects" / slug / "memory" / "MEMORY.md", "# Auto memory\nRemembered.")

    def tearDown(self):
        self.temp.cleanup()

    def atlas(self, **kwargs):
        return Atlas(self.source, Path(self.temp.name) / "d.sqlite3", home=self.home, **kwargs)

    def catalog(self, atlas):
        return {item["id"]: item for item in atlas.sources()["sources"]}

    def files(self, atlas, source_id):
        return {Path(path).name for path in next(item for item in atlas.source_catalog(refresh=True)
                                                 if item["id"] == source_id)["files"]}

    def test_projects_in_common_dev_and_onedrive_folders_are_found(self):
        atlas = self.atlas()
        self.assertTrue({"CLAUDE.md", "CLAUDE.local.md", "style.md", "global.md", "MEMORY.md"}
                        <= self.files(atlas, "claude"))
        self.assertIn("onedrive-app", {path.parent.name for path in next(
            item for item in atlas.source_catalog(refresh=True) if item["id"] == "claude")["files"]})

    def test_cursor_rules_accept_md_and_nested_folders(self):
        self.assertIn("deep.md", self.files(self.atlas(), "cursor"))

    def test_gemini_and_generic_agent_files_are_new_sources(self):
        atlas = self.atlas()
        self.assertEqual(self.files(atlas, "gemini"), {"GEMINI.md"})
        self.assertEqual(self.files(atlas, "agents"), {"AGENTS.md", "copilot-instructions.md"})
        self.assertEqual(self.catalog(atlas)["gemini"]["file_count"], 2)

    def test_explicit_workspace_folders_may_live_outside_home(self):
        without = self.atlas()
        self.assertNotIn("Flagged child", " ".join(item.title for item in without.memories))
        self.assertNotIn("child", {path.parent.name for path in next(
            item for item in without.source_catalog(refresh=True) if item["id"] == "claude")["files"]})
        flagged = self.atlas(workspaces=[self.outside / "flagged"])
        self.assertIn("child", {path.parent.name for path in next(
            item for item in flagged.source_catalog(refresh=True) if item["id"] == "claude")["files"]})

    def test_projects_recorded_by_selected_codex_memory_are_discovered(self):
        (self.source / "MEMORY.md").write_text(self.registry, encoding="utf-8")
        atlas = self.atlas()
        self.assertIn("recorded", {path.parent.name for path in next(
            item for item in atlas.source_catalog(refresh=True) if item["id"] == "claude")["files"]})
        atlas.store.set_sources(set(), {"codex"})
        atlas.reload()
        self.assertNotIn("recorded", {path.parent.name for path in next(
            item for item in atlas.source_catalog(refresh=True) if item["id"] == "claude")["files"]})

    def test_imported_projects_get_readable_names(self):
        atlas = self.atlas()
        atlas.select_sources(["claude", "gemini", "agents"])
        projects = {item.project for item in atlas.memories}
        self.assertIn("Claude / myproj", projects)
        self.assertIn("Claude 全局", projects)
        self.assertIn("Gemini 全局", projects)
        self.assertIn("Gemini / myproj", projects)
        self.assertIn("AGENTS / myproj", projects)
        self.assertFalse(any("-" * 3 in project for project in projects))

    def test_scan_reports_how_many_project_folders_were_checked(self):
        self.assertGreaterEqual(self.atlas().sources()["workspace_count"], 3)


def note(identifier, title, project="P"):
    return Memory(identifier, title, title, "tip", project, "test.md", 1, "2026-09-28", 0.7)


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "network.sqlite3"
        self.store = FeedbackStore(self.db)
        self.memories = [
            note("a", "alpha beta gamma delta"),
            note("b", "alpha beta epsilon zeta"),
            note("c", "zeta eta theta iota"),
            note("d", "kappa lambda mu nu", project="Q"),
        ]
        self.network = Network(self.memories, self.store)

    def tearDown(self):
        self.temp.cleanup()

    def weight(self, first, second):
        return self.network.edges[edge_key(first, second)]["w"]

    def test_every_memory_is_a_neuron_and_synapses_stay_between_memories(self):
        stats = self.network.snapshot({})["stats"]
        self.assertEqual(stats["neurons"], 4)
        self.assertTrue(all(first != second and first in self.network.ids and second in self.network.ids
                            for first, second in self.network.edges))
        self.assertNotIn("d", {end for pair in self.network.edges for end in pair})

    def test_learning_reduces_loss_and_moves_weights_by_feedback_sign(self):
        labels = {"a": 1.0, "b": 1.0, "c": -1.0, "d": 0.0}
        start = {pair: edge["w"] for pair, edge in self.network.edges.items()}
        losses = [self.network.learn_step(labels) for _ in range(30)]
        self.assertLess(losses[-1], losses[0])
        self.assertLess(losses[-1], Network.CONVERGED_LOSS)
        self.assertGreater(self.weight("a", "b"), start[edge_key("a", "b")])
        self.assertLess(self.weight("a", "c"), start[edge_key("a", "c")])
        self.assertEqual(self.network.epoch, 30)

    def test_prune_removes_weak_synapses_and_they_never_regrow(self):
        labels = {"a": 1.0, "b": 1.0, "c": -1.0, "d": 0.0}
        for _ in range(40):
            self.network.learn_step(labels)
        self.assertEqual(self.network.prune(), 2)
        self.assertNotIn(edge_key("a", "c"), {(item["a"], item["b"]) for item in self.network.snapshot(labels)["synapses"]})
        self.network.evolve({})
        self.assertTrue(self.network.edges[edge_key("a", "c")]["pruned"])

    def test_evolution_grows_connections_between_memories_reinforced_together(self):
        labels = {"a": 1.0, "d": 1.0}
        self.assertNotIn(edge_key("a", "d"), self.network.edges)
        self.assertGreaterEqual(self.network.evolve(labels), 1)
        grown = self.network.edges[edge_key("a", "d")]
        self.assertEqual(grown["origin"], "grown")
        self.assertEqual(self.network.generation, 1)

    def test_learning_and_evolution_persist_across_restart(self):
        labels = {"a": 1.0, "b": 1.0, "c": -1.0, "d": 1.0}
        for _ in range(5):
            self.network.learn_step(labels)
        self.network.evolve(labels)
        learned = self.weight("a", "b")
        restored = Network(self.memories, FeedbackStore(self.db))
        self.assertAlmostEqual(restored.edges[edge_key("a", "b")]["w"], learned)
        self.assertEqual((restored.epoch, restored.generation), (5, 1))
        self.assertEqual(restored.edges[edge_key("a", "d")]["origin"], "grown")

    def test_accuracy_is_none_without_informative_labels(self):
        self.assertEqual(self.network.accuracy({"a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0}), (None, 0))
        self.assertEqual(self.network.accuracy({"a": 1.0, "b": 0.0, "c": 0.0, "d": 1.0}), (None, 0))

    def test_accuracy_starts_at_zero_and_rises_as_weights_follow_feedback(self):
        labels = {"a": 1.0, "b": 1.0, "c": -1.0, "d": 0.0}
        self.assertEqual(self.network.accuracy(labels), (0.0, 3))
        for _ in range(30):
            self.network.learn_step(labels)
        self.assertEqual(self.network.accuracy(labels), (1.0, 3))
        self.network.prune()
        self.assertEqual(self.network.accuracy(labels), (1.0, 3))

    def test_search_uses_spreading_activation_without_touching_feedback(self):
        plain = rank_memories(self.memories, {}, "gamma")
        self.assertEqual([row["id"] for row in plain], ["a"])
        spread = rank_memories(self.memories, {}, "gamma", self.network)
        by_id = {row["id"]: row for row in spread}
        self.assertEqual(by_id["a"]["reason"], "关键词匹配")
        self.assertEqual(by_id["b"]["reason"], "联想激活")
        self.assertGreater(by_id["b"]["assoc"], 0)
        self.assertNotIn("d", by_id)
        self.assertEqual(self.store.all(), {})

    def test_learned_support_lifts_neighbours_of_reinforced_memories(self):
        feedback = {"a": {"boost": 3, "pinned": 0}}
        before = {row["id"]: row["score"] for row in rank_memories(self.memories, {}, "")}
        after = {row["id"]: row["score"] for row in rank_memories(self.memories, feedback, "", self.network)}
        self.assertGreater(after["b"], before["b"])
        self.assertEqual(after["d"], before["d"])


class NetworkApiTests(unittest.TestCase):
    def test_learning_and_evolution_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "memories"
            source.mkdir()
            (source / "memory_summary.md").write_text(SUMMARY, encoding="utf-8")
            (source / "MEMORY.md").write_text(REGISTRY, encoding="utf-8")
            atlas = Atlas(source, root / "data" / "feedback.sqlite3")
            handler = type("TestHandler", (AtlasHandler,), {"atlas": atlas})
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"

                def post(path, payload):
                    request = Request(base + path, data=json.dumps(payload).encode(), method="POST",
                                      headers={"Content-Type": "application/json"})
                    with urlopen(request) as response:
                        return json.load(response)

                with urlopen(base + "/api/state") as response:
                    state = json.load(response)
                self.assertEqual(state["network"]["stats"]["neurons"], 5)
                first = post("/api/learn/step", {})
                self.assertEqual(first["network"]["stats"]["epoch"], 1)
                self.assertIn("loss", first)
                self.assertEqual(post("/api/evolve", {"action": "prune"})["changed"], 0)
                grown = post("/api/evolve", {"action": "grow"})
                self.assertEqual(grown["network"]["stats"]["generation"], 1)
                history = grown["network"]["history"]
                self.assertEqual([row["event"] for row in history], ["learn", "prune", "grow"])
                self.assertEqual([row["epoch"] for row in history], [1, 1, 1])
                self.assertIsNotNone(history[0]["loss"])
                self.assertEqual(history[-1]["generation"], 1)
                with self.assertRaises(HTTPError) as raised:
                    post("/api/evolve", {"action": "explode"})
                self.assertEqual(raised.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
