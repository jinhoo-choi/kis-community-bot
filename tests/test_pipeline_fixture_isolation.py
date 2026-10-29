"""The offline pipeline must not depend on mutable production snapshots."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PipelineFixtureIsolationTests(unittest.TestCase):
    def test_full_pipeline_never_reads_production_snapshots(self):
        # Block reads rather than rewrite production files. This covers missing,
        # malformed and policy-empty snapshots: none may affect this fixture.
        script = r'''
import builtins
import io
import os
from pathlib import Path
import runpy
import socket
import sys
from unittest.mock import patch

root = Path.cwd()
blocked = {root / "data" / name for name in
           ("posts_latest.json", "market_cache.json", "listed_cache.json")}
attempts = []
real_open = builtins.open
real_io_open = io.open

def guarded(original):
    def read(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(file)).resolve()
            if path in blocked:
                attempts.append("snapshot: " + str(path))
                raise AssertionError("Mutable production snapshot read: " + str(path))
        return original(file, *args, **kwargs)
    return read

def no_network(*args, **kwargs):
    attempts.append("network")
    raise AssertionError("Offline pipeline attempted a network connection")

with patch("builtins.open", guarded(real_open)), \
     patch("io.open", guarded(real_io_open)), \
     patch.object(socket.socket, "connect", no_network), \
     patch.object(socket.socket, "connect_ex", no_network):
    try:
        runpy.run_path(sys.argv[1], run_name="__main__")
    except SystemExit as result:
        if result.code not in (None, 0):
            raise
    assert not attempts, "Forbidden access attempts: " + repr(attempts)
'''
        for test in ("test_decide.py", "test_cost_offline.py", "test_pipeline_offline.py"):
            with self.subTest(test=test):
                result = subprocess.run(
                    [sys.executable, "-c", script, "tests/" + test], cwd=ROOT,
                    env={**os.environ, "COST_PRIORITY_MODE": "0", "OPS_RUN": "0"},
                    capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if test == "test_pipeline_offline.py":
                    self.assertIn("9/9 passed", result.stdout)



if __name__ == "__main__":
    unittest.main()
