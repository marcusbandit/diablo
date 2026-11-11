import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load_cli(tasks_dir, fifo_path):
    import importlib.util
    from importlib.machinery import SourceFileLoader

    path = Path(__file__).resolve().parent.parent / "deploy" / "diablo"
    loader = SourceFileLoader("diablo_cli", str(path))
    spec = importlib.util.spec_from_loader("diablo_cli", loader)
    module = importlib.util.module_from_spec(spec)
    os.environ["DIABLO_TASKS_DIR"] = str(tasks_dir)
    os.environ["DIABLO_THINK_FIFO"] = str(fifo_path)
    loader.exec_module(module)
    return module


class TestTasksCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="diablo-tasks-"))
        self.tasks_dir = self.tmp / "tasks"
        self.fifo = self.tmp / "think.in"
        os.mkfifo(self.fifo)
        self.cli = load_cli(self.tasks_dir, self.fifo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def drain(self):
        stop = threading.Event()

        def reader():
            with open(self.fifo, "rb", buffering=0) as f:
                while not stop.is_set():
                    try:
                        line = f.readline()
                    except OSError:
                        break
                    if line:
                        self.lines.append(line.decode().rstrip("\n"))
                    else:
                        break

        self.lines = []
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        return stop, thread

    def test_add_dispatches_to_the_workers_fifo(self):
        stop, thread = self.drain()
        try:
            code = self.cli.tasks() if False else self.cli.task_add(["smoke", "clean the vault", "then report"], False)
        finally:
            pass
        self.assertEqual(code, 0)
        import time

        time.sleep(0.2)
        stop.set()
        try:
            os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK).close()
        except OSError:
            pass
        thread.join(timeout=2)
        self.assertEqual(len(self.lines), 1)
        tid, name, prompt = self.lines[0].split("|", 2)
        self.assertTrue(tid.startswith("task-"))
        self.assertEqual(name, "smoke")
        self.assertEqual(prompt, "clean the vault then report")
        self.assertEqual((self.tasks_dir / f"{tid}.kind").read_text(), "task")
        self.assertEqual((self.tasks_dir / f"{tid}.prompt").read_text(), "clean the vault then report")

    def test_add_without_a_listener_exits_three(self):
        code = self.cli.task_add(["smoke", "do nothing"], False)
        self.assertEqual(code, 3)

    def test_add_without_prompt_fails(self):
        stop, thread = self.drain()
        code = self.cli.task_add(["--"], False)
        stop.set()
        try:
            os.close(os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK))
        except OSError:
            pass
        thread.join(timeout=2)
        self.assertEqual(code, 1)

    def running_task(self, name="sweep", status="running"):
        tid = "task-1700000000"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        (self.tasks_dir / f"{tid}.name").write_text(name)
        (self.tasks_dir / f"{tid}.prompt").write_text("sweep the crypt")
        (self.tasks_dir / f"{tid}.status").write_text(status)
        (self.tasks_dir / f"{tid}.kind").write_text("task")
        return self.cli.task_records()[0]

    def test_tell_writes_into_the_inbox_of_a_running_task(self):
        self.running_task()
        code = self.cli.task_tell(self.cli.task_records()[0], False, "also, mind the bats")
        self.assertEqual(code, 0)
        inbox = (self.tasks_dir / "task-1700000000.inbox").read_text()
        self.assertIn("mind the bats", inbox)
        self.assertIn("[", inbox)

    def test_tell_refuses_a_finished_task(self):
        self.running_task(status="done")
        code = self.cli.task_tell(self.cli.task_records()[0], False, "hello?")
        self.assertEqual(code, 2)

    def test_tell_without_a_message_fails(self):
        self.running_task()
        code = self.cli.task_tell(self.cli.task_records()[0], False, "")
        self.assertEqual(code, 1)

    def test_records_expose_the_inbox_count(self):
        self.running_task()
        with (self.tasks_dir / "task-1700000000.inbox").open("w") as inbox:
            inbox.write("[10:00:00] one\n[10:00:01] two\n")
        record = self.cli.task_records()[0]
        self.assertEqual(record["inbox"], 2)


if __name__ == "__main__":
    unittest.main()
