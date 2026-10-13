"""Fictional Codex-to-Cursor and Codex-to-MCP acceptance, with no real agent profile."""

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from memory_atlas import Atlas, AtlasHandler, CURSOR_EXPORT_FILENAME, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
SUMMARY = """v1
## User Profile
This is a fictional cross-agent test user.
## User preferences
- In the fictional project, use lavender labels for Cursor-facing diagrams.
- In the fictional project, the MCP deployment channel is comet-blue.
- In the fictional project, the unshared marker is cobalt otter.
"""


def rpc(identifier, method, params=None):
    message = {"jsonrpc": "2.0", "id": identifier, "method": method}
    if params is not None:
        message["params"] = params
    return message


class CrossAgentFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "fictional-home"
        self.source = self.home / ".codex" / "memories"
        self.source.mkdir(parents=True)
        (self.source / "memory_summary.md").write_text(SUMMARY, encoding="utf-8")
        self.database = self.root / "fictional.sqlite3"
        self.project = self.root / "fictional-cursor-project"
        self.project.mkdir()
        self.atlas = Atlas(self.source, self.database, home=self.home)
        handler = type("AcceptanceHandler", (AtlasHandler,), {"atlas": self.atlas})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=3)
        self.temporary.cleanup()

    def memory_id(self, fragment):
        return next(item.id for item in self.atlas.memories if fragment in item.text)

    def post(self, path, body):
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    def mcp(self, calls):
        """Start the real stdio server as an MCP client would, in this test's fake home."""
        commands = [
            rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "fictional-client", "version": "1"}}),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            rpc(2, "tools/list"),
            *[rpc(index + 3, "tools/call", {"name": name, "arguments": args})
              for index, (name, args) in enumerate(calls)],
        ]
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "memory_atlas_mcp.py"),
             "--source", str(self.source), "--database", str(self.database), "--home", str(self.home)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT,
        )
        try:
            out, err = process.communicate(
                "".join(json.dumps(command, ensure_ascii=False) + "\n" for command in commands).encode("utf-8"),
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            self.fail("MCP stdio server did not answer within 30 seconds")
        self.assertEqual(process.returncode, 0, err.decode("utf-8", errors="replace"))
        self.assertEqual(err.strip(), b"")
        replies = [json.loads(line) for line in out.decode("utf-8").splitlines()]
        self.assertEqual([reply["id"] for reply in replies], list(range(1, len(calls) + 3)))
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "memory-atlas")
        self.assertIn("search_memory", [item["name"] for item in replies[1]["result"]["tools"]])
        return replies[2:]

    def mcp_payload(self, reply):
        self.assertFalse(reply["result"]["isError"], reply)
        return json.loads(reply["result"]["content"][0]["text"])

    def test_fictional_codex_memory_reaches_only_approved_cursor_and_mcp_targets(self):
        cursor_id = self.memory_id("lavender labels")
        mcp_id = self.memory_id("comet-blue")
        private_id = self.memory_id("cobalt otter")
        body = {"scope": "project", "project": str(self.project), "limit": 10}

        # Selection as a source does not authorize disclosure to any destination.
        state = {row["id"]: row for row in self.atlas.state()["memories"]}
        for identifier in (cursor_id, mcp_id, private_id):
            self.assertFalse(any(state[identifier]["sharing"].values()))
        self.assertEqual(self.post("/api/export/cursor/preview", body)["count"], 0)
        self.assertEqual(self.post("/api/recall", {"query": "comet-blue"})["results"], [])
        before = self.mcp([("search_memory", {"query": "comet-blue"}),
                           ("get_memory", {"id": private_id}),
                           ("network_status", {})])
        self.assertEqual(self.mcp_payload(before[0])["results"], [])
        self.assertTrue(before[1]["result"]["isError"])
        self.assertEqual(self.mcp_payload(before[2])["memories"], 0)

        # Approve one record for Cursor and a different one for on-demand MCP use.
        self.post("/api/sharing", {"id": cursor_id, "target": "cursor", "allowed": True})
        self.post("/api/sharing", {"id": mcp_id, "target": "mcp", "allowed": True})
        preview = self.post("/api/export/cursor/preview", body)
        self.assertEqual(preview["ids"], [cursor_id])
        self.assertIn("lavender labels", preview["content"])
        self.assertNotIn("comet-blue", preview["content"])
        self.assertNotIn("cobalt otter", preview["content"])
        self.assertTrue(preview["git_safe"])
        written = self.post("/api/export/cursor/write", {**body, "digest": preview["digest"]})
        rule = self.project / ".cursor" / "rules" / CURSOR_EXPORT_FILENAME
        self.assertEqual(rule.read_text(encoding="utf-8"), preview["content"])
        self.assertTrue(written["written"])
        self.assertTrue(rule.read_text(encoding="utf-8").startswith("---\n"))
        self.assertIn("alwaysApply: false", rule.read_text(encoding="utf-8"))
        http_results = self.post("/api/recall", {"query": "comet-blue", "limit": 8})["results"]
        self.assertEqual([row["id"] for row in http_results], [mcp_id])
        self.assertEqual(http_results[0]["source"], "memory_summary.md")

        after = self.mcp([("search_memory", {"query": "comet-blue", "limit": 8}),
                          ("get_memory", {"id": mcp_id}),
                          ("get_memory", {"id": cursor_id}),
                          ("get_memory", {"id": private_id}),
                          ("network_status", {})])
        found = self.mcp_payload(after[0])
        self.assertEqual([row["id"] for row in found["results"]], [mcp_id])
        self.assertTrue(found["results"][0]["source"].startswith("memory_summary.md:"))
        self.assertNotIn("lavender labels", json.dumps(found))
        self.assertNotIn("cobalt otter", json.dumps(found))
        detail = self.mcp_payload(after[1])
        self.assertIn("comet-blue", detail["text"])
        self.assertTrue(detail["source"].startswith("memory_summary.md:"))
        self.assertNotIn(cursor_id, [item["id"] for item in detail["related"]])
        self.assertNotIn(private_id, [item["id"] for item in detail["related"]])
        self.assertTrue(after[2]["result"]["isError"])
        self.assertTrue(after[3]["result"]["isError"])
        self.assertEqual(self.mcp_payload(after[4])["memories"], 1)

        # MCP revocation takes effect for the next process. Static rules require
        # removal first, so a revoked record cannot remain in a generated file.
        self.post("/api/sharing", {"id": mcp_id, "target": "mcp", "allowed": False})
        revoked = self.mcp([("search_memory", {"query": "comet-blue"}),
                            ("get_memory", {"id": mcp_id})])
        self.assertEqual(self.mcp_payload(revoked[0])["results"], [])
        self.assertTrue(revoked[1]["result"]["isError"])
        self.assertEqual(self.post("/api/recall", {"query": "comet-blue"})["results"], [])
        with self.assertRaises(HTTPError) as blocked:
            self.post("/api/sharing", {"id": cursor_id, "target": "cursor", "allowed": False})
        self.assertEqual(blocked.exception.code, 400)
        self.assertTrue(self.post("/api/export/cursor/remove", body)["removed"])
        self.post("/api/sharing", {"id": cursor_id, "target": "cursor", "allowed": False})
        self.assertFalse(rule.exists())
        self.assertEqual(self.post("/api/export/cursor/preview", body)["count"], 0)


if __name__ == "__main__":
    unittest.main()
