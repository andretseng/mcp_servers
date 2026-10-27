"""Integration test (needs opencode, ~15 s): the project config that workrun writes for the work session really changes the sampling that
opencode sends, and a directory without that file keeps the global sampling. A local fake OpenAI-compatible server records the requests."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

import workrun as wr

SEEN: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        SEEN.append({k: body.get(k) for k in ("temperature", "top_p", "top_k", "reasoning_effort")})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, fin in (({"role": "assistant", "content": ""}, None), ({"content": "ok"}, None), ({}, "stop")):
            chunk = {"id": "x", "object": "chat.completion.chunk", "model": body.get("model"), "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}
            if fin:
                chunk["usage"] = {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")


@unittest.skipUnless(shutil.which("opencode") and Path.home().joinpath(".config/opencode/opencode.json").is_file(), "needs opencode and the global config")
class SamplingTests(unittest.TestCase):
    def run_in(self, d: Path, config: dict) -> dict:
        SEEN.clear()
        (d / "opencode.json").write_text(json.dumps(config), encoding="utf-8")
        env = dict(os.environ, OPENCODE_GATE="off", PWD=str(d))  # opencode reads the project directory from $PWD
        subprocess.run(["opencode", "run", "--standalone", "--auto", "--model", wr.DEFAULT_MODEL, "--title", "sampling-test", "say ok"], cwd=d, env=env, capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        self.assertTrue(SEEN, "the fake server saw no request")
        return SEEN[-1]

    def test_the_work_directory_samples_colder_and_the_planner_directory_keeps_the_global_values(self):
        srv = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = {"providers": {"omlx": {"settings": {"baseURL": f"http://127.0.0.1:{srv.server_port}/v1"}}}}
        with tempfile.TemporaryDirectory() as t:
            plan_dir, work_dir = Path(t) / "plan", Path(t) / "work"
            plan_dir.mkdir(), work_dir.mkdir()
            planner = self.run_in(plan_dir, base)  # only the endpoint differs: the sampling is the global one
            cold = json.loads(json.dumps(base))
            cold["providers"]["omlx"]["models"] = wr.exec_config(wr.DEFAULT_MODEL, 0.2)["providers"]["omlx"]["models"]
            worker = self.run_in(work_dir, cold)
        srv.shutdown()
        self.assertEqual(planner["temperature"], 0.6)
        self.assertEqual(worker["temperature"], 0.2)
        self.assertEqual((worker["top_p"], worker["top_k"]), (planner["top_p"], planner["top_k"]))
        self.assertEqual(worker["reasoning_effort"], "low")


if __name__ == "__main__":
    unittest.main()
