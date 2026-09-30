from project_evo import activity, config, dashboard, evolve
from project_evo.database import Database, PrimaryTableRow
from project_evo.task import Task

from argparse import Namespace
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock, patch

import errno
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest

class ActivityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "run.db"
        self.parent = PrimaryTableRow("Baseline", "baseline", None, None, None, 10)
        self.parent.id = 1
        self.child = PrimaryTableRow.create_new_child(self.parent, Task.EXPLORE)

    def test_socket_graph_identity_and_shutdown(self):
        with closing(Database(self.path, island_count=1)) as db:
            db.insert_baseline(self.parent)
        with closing(activity.StatusServer(self.path, 0)) as server:
            port = server.socket.getsockname()[1]
            self.assertEqual(activity.read_active(self.path, port), [])
            server.add(self.child, 0)
            with patch.object(dashboard, "DB_FILE", self.path), patch.object(dashboard, "STATUS_PORT", port):
                before = self.path.read_bytes()
                graph = dashboard.app.test_client().get("/api/graph").get_json()
                self.assertEqual(graph["active"][0]["uuid"], self.child.uuid)
                self.assertEqual(graph["nodes"][0]["uuid"], self.parent.uuid)
                self.assertEqual(self.path.read_bytes(), before)
            self.assertIsNone(activity.read_active(self.path.with_name("other.db"), port))
            server.remove(self.child.uuid)
            self.assertEqual(activity.read_active(self.path, port), [])
        self.assertFalse(server.thread.is_alive())
        self.assertIsNone(activity.read_active(self.path, port))
        with closing(activity.StatusServer(self.path, port)):
            self.assertEqual(activity.read_active(self.path, port), [])

    def test_bad_or_silent_peer_preserves_saved_graph(self):
        with closing(Database(self.path, island_count=1)) as db:
            db.insert_baseline(self.parent)
        invalid_row = {"database": str(self.path), "active": [{"uuid": "bad"}]}
        payloads = [b"not JSON", b"[]", b"\xff", json.dumps(invalid_row).encode(), b"x" * (activity.MAX_BYTES + 1), None]
        for payload in payloads:
            with self.subTest(payload=type(payload)), socket.create_server(("127.0.0.1", 0)) as listener:
                release = Event()

                def reply():
                    with listener.accept()[0] as connection:
                        if payload is None:
                            release.wait(2)
                        else:
                            try:
                                connection.sendall(payload)
                            except OSError:
                                pass # The reader may reject oversized data before it all arrives.

                thread = Thread(target=reply)
                thread.start()
                try:
                    with patch.object(dashboard, "DB_FILE", self.path), patch.object(dashboard, "STATUS_PORT", listener.getsockname()[1]):
                        started = time.monotonic()
                        response = dashboard.app.test_client().get("/api/graph")
                        self.assertLess(time.monotonic() - started, 2)
                        self.assertEqual(response.status_code, 200)
                        self.assertIsNone(response.get_json()["active"])
                        self.assertEqual(response.get_json()["nodes"][0]["score"], 10)
                finally:
                    release.set()
                    thread.join()

    def test_independent_runner_can_exit_abruptly(self):
        script = "from pathlib import Path; from project_evo.activity import StatusServer; import sys; server = StatusServer(Path(sys.argv[1]), 0); print(server.socket.getsockname()[1], flush=True); sys.stdin.read()"
        with subprocess.Popen([sys.executable, "-c", script, str(self.path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as runner:
            try:
                assert runner.stdout is not None
                port = int(runner.stdout.readline())
                self.assertEqual(activity.read_active(self.path, port), [])
            finally:
                runner.kill()
                runner.wait(timeout=5)
            self.assertIsNone(activity.read_active(self.path, port))

    def test_port_arguments_and_optional_listener(self):
        for parser, required in ((evolve.build_parser(), ["-i", "1"]), (dashboard.build_parser(), ["--db", "run.db"])):
            self.assertEqual(parser.parse_args(required).status_port, 49371)
            self.assertEqual(parser.parse_args([*required, "--status-port", "49372"]).status_port, 49372)
        self.assertIn("not required for evolution", evolve.build_parser().format_help())
        for error, answer, expected in ((errno.EADDRINUSE, "y", 0), (errno.EADDRINUSE, "n", 1), (errno.EADDRINUSE, EOFError(), 1), (errno.EACCES, "", 0)):
            prompt = Mock(side_effect=answer if isinstance(answer, Exception) else None, return_value=answer)
            work = Mock(return_value=0)
            with patch.object(config, "cfg"), patch.object(config, "Config", return_value=SimpleNamespace(db_file=self.path)), patch.object(evolve, "check_requirements"), patch.object(evolve, "init_db"), patch.object(evolve, "run_iterations", work), patch.object(activity, "StatusServer", side_effect=OSError(error, "unavailable")), patch("builtins.input", prompt), redirect_stdout(StringIO()):
                self.assertEqual(evolve.run(Namespace(status_port=49371)), expected)
                self.assertEqual(work.call_count, int(expected == 0))
                self.assertEqual(prompt.call_count, int(error == errno.EADDRINUSE))

    def test_workspace_creation_failure_removes_active_attempt(self):
        server = Mock(spec=activity.StatusServer)
        database = Mock()
        database.return_value.weighted_sample.return_value = self.parent
        with patch.object(config, "cfg", SimpleNamespace(db_file=self.path, island_count=1)), patch.object(evolve, "_status_server", server), patch.object(evolve, "Database", database), patch.object(evolve.git, "create_new_workspace", side_effect=RuntimeError("failed")):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                evolve.run_worker(0)
        child = server.add.call_args.args[0]
        server.remove.assert_called_once_with(child.uuid)

if __name__ == "__main__":
    unittest.main()
