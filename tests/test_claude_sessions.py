"""Tests for claude-sessions: python3 -m unittest discover tests

Uses synthetic transcripts in a temporary CLAUDE_CONFIG_DIR, a fake ollama server whose
"embeddings" are bags of words with a few synonyms folded together, and a fake tmux on PATH.
"""

import contextlib
import hashlib
import http.server
import importlib.machinery
import importlib.util
import io
import json
import math
import os
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "claude-sessions"
loader = importlib.machinery.SourceFileLoader("claude_sessions", str(SCRIPT))
spec = importlib.util.spec_from_loader("claude_sessions", loader)
cs = importlib.util.module_from_spec(spec)
loader.exec_module(cs)

SYNONYMS = {"car": "vehicle", "cars": "vehicle", "motorcar": "vehicle", "motorcars": "vehicle",
            "automobile": "vehicle", "vehicles": "vehicle"}
DIM = 64


def fake_vector(text):
    text = text.split("|", 1)[-1]  # drop the task/title prompt prefix
    vec = [0.0] * DIM
    for w in re.findall(r"\w+", text.lower()):
        if w in ("text", "query", "none"):
            continue
        w = SYNONYMS.get(w, w)
        vec[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeOllama(http.server.BaseHTTPRequestHandler):
    requests = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.requests.append(body)
        if body["model"] != "fake-embed":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"model '{body['model']}' not found"}).encode())
            return
        out = json.dumps({"embeddings": [fake_vector(t) for t in body["input"]]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


def user(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, "cwd": extra.pop("cwd", "/work/proj"),
            "gitBranch": "main", "timestamp": "2026-10-01T10:00:00Z", "entrypoint": "cli",
            "isSidechain": False, **extra}


def assistant(*blocks, **extra):
    content = [{"type": t, ("text" if t == "text" else t): v} for t, v in blocks]
    return {"type": "assistant", "message": {"role": "assistant", "content": content},
            "cwd": "/work/proj", "entrypoint": "cli", "isSidechain": False, **extra}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "claude/projects").mkdir(parents=True)
        (self.root / "tasks").mkdir()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        # Fake tmux: logs its arguments, lists panes from a file, prints a new pane id.
        (bin_dir / "tmux").write_text(
            "#!/bin/sh\n"
            f'echo "$@" >> {self.root}/tmux.log\n'
            f'case "$1" in list-panes) cat {self.root}/panes 2>/dev/null ;; new-window) echo %99 ;; esac\n'
        )
        (bin_dir / "tmux").chmod(0o755)
        patches = {"CLAUDE_DIR": self.root / "claude", "STATE": self.root / "state",
                   "TASKS": self.root / "tasks", "MODEL": "fake-embed",
                   "OLLAMA": f"http://127.0.0.1:{self.server.server_address[1]}"}
        for name, value in patches.items():
            old = getattr(cs, name)
            setattr(cs, name, value)
            self.addCleanup(setattr, cs, name, old)
        old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{bin_dir}:{old_path}"
        self.addCleanup(os.environ.__setitem__, "PATH", old_path)
        os.environ.pop("CLAUDE_SESSIONS_HERE", None)
        os.environ.pop("TMUX", None)
        FakeOllama.requests.clear()
        self.db = cs.connect()
        self.addCleanup(lambda: self.db.close())

    def write(self, sid, records, project="-work-proj"):
        d = self.root / "claude/projects" / project
        d.mkdir(exist_ok=True)
        path = d / f"{sid}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        return path

    def index(self):
        cs.update_text(self.db)
        cs.update_vectors(self.db)

    def ids(self, ranked):
        return [sid for sid, _ in ranked]


class ParseTest(Base):
    def test_keeps_only_what_you_and_claude_wrote(self):
        path = self.write("s1", [
            {"type": "mode", "mode": "default"},
            user("<local-command-caveat>Caveat: the messages below were generated</local-command-caveat>"),
            user("<command-name>/clear</command-name>\n<command-message>clear</command-message>\n<command-args></command-args>"),
            user("<command-name>/review</command-name>\n<command-message>review</command-message>\n<command-args>the parser</command-args>"),
            user("How do I fix the parser?<system-reminder>secret reminder</system-reminder>"),
            assistant(("thinking", "private thoughts"), ("text", "Use a state machine.")),
            assistant(("tool_use", {"name": "Bash"})),
            user([{"type": "tool_result", "content": "tool output here"}]),
            assistant(("text", "Done.")),
            user("summary of earlier conversation", isCompactSummary=True),
            user("meta stuff", isMeta=True),
            user("sidechain prompt", isSidechain=True),
            user("<task-notification>finished</task-notification>", origin={"kind": "task-notification"}),
            user("<bash-input>ls -la</bash-input>"),
            user("<bash-stdout>file1 file2</bash-stdout>"),
            user('<pasted_content id="abc">pasted log line</pasted_content>'),
            user("[Request interrupted by user]"),
            {"type": "ai-title", "aiTitle": "Parser fix"},
        ])
        s = cs.parse(path)
        prompts = [p for p, _ in s["turns"]]
        self.assertEqual(prompts, ["/review the parser", "How do I fix the parser?", "! ls -la", "pasted log line"])
        self.assertEqual(s["turns"][1][1], "Use a state machine.\n\nDone.")
        blob = json.dumps(s["turns"])
        for leaked in ("private thoughts", "tool output", "secret reminder", "summary of earlier",
                       "meta stuff", "sidechain", "finished", "file1"):
            self.assertNotIn(leaked, blob)
        self.assertEqual((s["title"], s["title_kind"]), ("Parser fix", "ai"))
        self.assertEqual((s["cwd"], s["branch"]), ("/work/proj", "main"))
        self.assertFalse(s["hidden"])

    def test_title_precedence(self):
        path = self.write("s1", [user("first prompt line\nsecond"), {"type": "ai-title", "aiTitle": "AI"},
                                 {"type": "custom-title", "customTitle": "Mine"}, {"type": "ai-title", "aiTitle": "AI 2"}])
        self.assertEqual(cs.parse(path)["title"], "Mine")
        path = self.write("s2", [user("first prompt line\nsecond")])
        self.assertEqual((cs.parse(path)["title"], cs.parse(path)["title_kind"]), ("first prompt line", "prompt"))

    def test_hidden_sessions(self):
        empty = self.write("empty", [user("<command-name>/exit</command-name><command-args></command-args>")])
        sdk = self.write("sdk", [user("hello", entrypoint="sdk-cli")])
        self.assertTrue(cs.parse(empty)["hidden"])
        self.assertTrue(cs.parse(sdk)["hidden"])

    def test_tolerates_garbage_lines(self):
        d = self.root / "claude/projects/-work-proj"
        d.mkdir()
        (d / "bad.jsonl").write_text('not json\n[1,2]\n{"type": "user", "message": "x"}\n'
                                     + json.dumps(user("real prompt")) + '\n{"type": "assis')
        self.assertEqual([p for p, _ in cs.parse(d / "bad.jsonl")["turns"]], ["real prompt"])

    def test_windows_cover_long_text(self):
        text = " ".join(f"word{i}" for i in range(2000))
        pieces = cs.windows(text)
        self.assertGreater(len(pieces), 1)
        self.assertLessEqual(len(pieces), cs.MAX_WINDOWS)
        self.assertTrue(all(len(p) <= cs.WINDOW for p in pieces))
        self.assertTrue(text.startswith(pieces[0]))
        self.assertEqual(cs.windows("short"), ["short"])


class IndexTest(Base):
    def test_incremental_update(self):
        p1 = self.write("s1", [user("alpha topic")])
        self.write("s2", [user("beta topic")])
        self.index()
        self.assertEqual(self.db.execute("SELECT count(*) FROM sessions").fetchone()[0], 2)
        embedded = len(FakeOllama.requests)
        self.assertGreater(embedded, 0)

        self.index()  # nothing changed: no re-reads, no new embeddings
        self.assertEqual(len(FakeOllama.requests), embedded)

        p1.write_text(p1.read_text() + json.dumps(user("gamma followup")) + "\n")
        os.utime(p1, (time.time() + 5, time.time() + 5))
        self.index()
        self.assertEqual(self.ids(cs.keyword_rank(self.db, "gamma")), ["s1"])
        # Only the new exchange is embedded; the unchanged one is reused by hash.
        new_inputs = [t for r in FakeOllama.requests[embedded:] for t in r["input"]]
        self.assertEqual(len(new_inputs), 1)
        self.assertIn("gamma", new_inputs[0])

        p1.unlink()
        self.index()
        self.assertEqual(self.ids(cs.keyword_rank(self.db, "alpha")), [])
        self.assertEqual(self.db.execute("SELECT count(*) FROM vectors").fetchone()[0],
                         self.db.execute("SELECT count(DISTINCT hash) FROM pieces").fetchone()[0])

    def test_subagent_transcripts_ignored(self):
        self.write("s1", [user("main")])
        sub = self.root / "claude/projects/-work-proj/s1/subagents"
        sub.mkdir(parents=True)
        (sub / "agent-x.jsonl").write_text(json.dumps(user("subagent work")) + "\n")
        cs.update_text(self.db)
        self.assertEqual([r[0] for r in self.db.execute("SELECT id FROM sessions")], ["s1"])

    def test_schema_upgrade_rebuilds_but_keeps_vectors(self):
        self.write("s1", [user("alpha")])
        self.index()
        embedded = len(FakeOllama.requests)
        self.db.execute("PRAGMA user_version=0")
        self.db.close()
        self.db = cs.connect()
        self.assertEqual(self.db.execute("SELECT count(*) FROM sessions").fetchone()[0], 0)
        self.index()
        self.assertEqual(self.ids(cs.keyword_rank(self.db, "alpha")), ["s1"])
        self.assertEqual(len(FakeOllama.requests), embedded)  # nothing re-embedded

    def test_recency_uses_last_message_not_mtime(self):
        old = self.write("old", [user("old one", timestamp="2026-01-01T00:00:00Z")])
        new = self.write("new", [user("new one", timestamp="2026-01-01T00:00:00Z"),
                                 user("later", timestamp="2026-03-01T00:00:00.123Z")])
        os.utime(new, (1, 1))  # e.g. a sync tool copied this file long ago...
        os.utime(old, None)  # ...and rewrote the other one just now
        cs.update_text(self.db)
        self.assertEqual([line.split("\t")[0] for line in cs.rows(self.db)], ["new", "old"])


class SearchTest(Base):
    def setUp(self):
        super().setUp()
        self.write("shell", [user("Write a fish shell function for my prompt"),
                             assistant(("text", "Here is a fish function using set_color."))])
        self.write("cars", [user("I want to build a website listing motorcars for sale"),
                            assistant(("text", "Use a static site generator for the listing pages."))])
        self.write("travel", [user("plánujem cestovanie do Vietnamu", cwd="/home/travel"),
                              assistant(("text", "Vietnam travel checklist: visa, vaccines."), cwd="/home/travel")],
                   project="-home-travel")
        self.write("titled", [user("misc question"), {"type": "custom-title", "customTitle": "Thesis outline"}])
        self.index()

    def test_keyword_any_term_ranked(self):
        self.assertEqual(self.ids(cs.keyword_rank(self.db, "fish prompt"))[0], "shell")
        self.assertIn("cars", self.ids(cs.keyword_rank(self.db, "shell motorcars")))

    def test_prefix_on_word_being_typed(self):
        self.assertEqual(self.ids(cs.keyword_rank(self.db, "motorc")), ["cars"])
        self.assertEqual(self.ids(cs.keyword_rank(self.db, "motorc ")), [])  # finished word: exact

    def test_stemming_and_diacritics(self):
        self.assertIn("cars", self.ids(cs.keyword_rank(self.db, "listings ")))
        self.assertEqual(self.ids(cs.keyword_rank(self.db, "planujem")), ["travel"])

    def test_title_match_previews_best_exchange(self):
        self.write("rc", [user("set up a rust project"), assistant(("text", "done")),
                          user("drive it over bluetooth"), assistant(("text", "added BLE")),
                          {"type": "ai-title", "aiTitle": "Bluetooth RC car"}])
        cs.update_text(self.db)
        self.assertEqual(dict(cs.keyword_rank(self.db, "bluetooth"))["rc"], 1)
        self.assertEqual(dict(cs.keyword_rank(self.db, "rc "))["rc"], 0)  # title only

    def test_title_and_path_searchable(self):
        self.assertEqual(self.ids(cs.keyword_rank(self.db, "outline")), ["titled"])
        self.assertEqual(set(self.ids(cs.keyword_rank(self.db, "proj "))), {"shell", "cars", "titled"})

    def test_query_syntax_is_inert(self):
        for q in ('"', "AND", "NOT OR", "a*b", "col:x", "(", "^", "---"):
            cs.keyword_rank(self.db, q)  # must not raise

    def test_semantic_finds_what_keywords_miss(self):
        self.assertEqual(self.ids(cs.keyword_rank(self.db, "vehicle ")), [])
        self.assertEqual(self.ids(cs.semantic_rank(self.db, "vehicle"))[0], "cars")
        self.assertEqual(self.ids(cs.hybrid_rank(self.db, "vehicle"))[0], "cars")

    def test_semantic_without_numpy_matches(self):
        import builtins
        real_import = builtins.__import__

        def no_numpy(name, *a, **kw):
            if name == "numpy":
                raise ImportError
            return real_import(name, *a, **kw)

        with_np = cs.semantic_rank(self.db, "fish shell")
        builtins.__import__ = no_numpy
        try:
            self.assertEqual(cs.semantic_rank(self.db, "fish shell"), with_np)
        finally:
            builtins.__import__ = real_import

    def test_hybrid_falls_back_when_ollama_fails(self):
        cs.MODEL = "missing-model"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ranked = cs.hybrid_rank(self.db, "fish")
        self.assertEqual(self.ids(ranked), ["shell"])
        self.assertIn("not found", err.getvalue())

    def test_rows_and_here_filter(self):
        lines = cs.rows(self.db)
        self.assertEqual(len(lines), 4)
        by_id = {line.split("\t")[0]: line.split("\t") for line in lines}
        self.assertEqual(by_id["shell"][1], "0")
        self.assertIn("proj", by_id["shell"][2])
        os.environ["CLAUDE_SESSIONS_HERE"] = "/home/travel"
        try:
            self.assertEqual([line.split("\t")[0] for line in cs.rows(self.db)], ["travel"])
            os.environ["CLAUDE_SESSIONS_HERE"] = "/work"
            self.assertEqual(len(cs.rows(self.db)), 4 - 1)
        finally:
            del os.environ["CLAUDE_SESSIONS_HERE"]

    def test_fit_handles_wide_characters(self):
        self.assertEqual(cs.width(cs.fit("日本語のセッション名前です", 10)), 10)
        self.assertEqual(cs.fit("ab", 4), "ab  ")


class LiveAndOpenTest(Base):
    def setUp(self):
        super().setUp()
        self.cwd = self.root / "proj"
        self.cwd.mkdir()
        self.write("s1", [user("hello", cwd=str(self.cwd))])
        self.write("s2", [user("other", cwd=str(self.root / "gone"))])
        cs.update_text(self.db)

    def tmux_log(self):
        log = self.root / "tmux.log"
        return log.read_text() if log.exists() else ""

    def test_live_panes_from_claude_tasks(self):
        (self.root / "panes").write_text("%5\n%6\n")
        (self.root / "tasks/5").write_text("running\t1\t/x\ts1\n")
        (self.root / "tasks/6").write_text("idle\t1\t/x\n")  # old 3-field format
        (self.root / "tasks/7").write_text("done\t1\t/x\ts2\n")  # pane closed
        self.assertEqual(cs.live_panes(), {"s1": "%5"})
        marks = {line.split("\t")[0]: "●" in line for line in cs.rows(self.db)}
        self.assertEqual(marks, {"s1": True, "s2": False})

    def test_open_live_session_jumps(self):
        (self.root / "panes").write_text("%5\n")
        (self.root / "tasks/5").write_text("running\t1\t/x\ts1\n")
        cs.open_session(self.db, "s1")
        self.assertIn("switch-client -t %5", self.tmux_log())
        self.assertNotIn("new-window", self.tmux_log())

    def test_open_resumes_in_new_window(self):
        os.environ["TMUX"] = "fake"
        try:
            cs.open_session(self.db, "s1")
        finally:
            del os.environ["TMUX"]
        log = self.tmux_log()
        self.assertIn(f"new-window -P -F #{{pane_id}} -c {self.cwd}", log)
        self.assertIn("send-keys -t %99 -l claude --resume s1", log)

    def test_resume_command_template(self):
        os.environ["TMUX"] = "fake"
        old, cs.RESUME = cs.RESUME, "claude -r {id} --dangerously-skip-permissions"
        try:
            cs.open_session(self.db, "s1")
        finally:
            cs.RESUME = old
            del os.environ["TMUX"]
        self.assertIn("-l claude -r s1 --dangerously-skip-permissions", self.tmux_log())

    def test_open_missing_folder(self):
        with self.assertRaises(SystemExit) as e:
            cs.open_session(self.db, "s2")
        self.assertIn("no longer exists", str(e.exception))


class PreviewTest(Base):
    def test_starts_one_exchange_before_match(self):
        recs = []
        for i in range(5):
            recs += [user(f"prompt number {i}"), assistant(("text", f"reply number {i}"))]
        self.write("s1", recs)
        cs.update_text(self.db)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cs.preview(self.db, "s1", 3, "number")
        self.assertIn("\x1b[1;33mnumber", out.getvalue())
        text = re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue())
        self.assertIn("2 earlier exchanges", text)
        self.assertNotIn("prompt number 1", text)
        self.assertIn("prompt number 2", text)
        self.assertIn("best match", text)

    def test_unknown_session(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cs.preview(self.db, "nope")
        self.assertIn("not in the index", out.getvalue())


if __name__ == "__main__":
    unittest.main()
