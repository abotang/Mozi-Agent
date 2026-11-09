"""不连接模型或墨子，验证 Agent 输入输出审计事件。"""

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agents.model_client import AgentConfig, ChatCompletionClient, ModelError
from runtime.app.agent_monitor import main as monitor_main


class FakeClient(ChatCompletionClient):
    response = '{"version":1,"mission_intents":[]}'

    def _complete(self, *args, **kwargs):
        return self.response


class AgentMonitorTests(unittest.TestCase):
    def test_monitor_once_displays_recorded_input(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            config_path = root / "llm.json"
            config_path.write_text(json.dumps({
                "base_url": "http://127.0.0.1:1/v1", "model": "mock",
                "objective": "测试", "log_dir": "logs"}), encoding="utf-8")
            client = FakeClient(AgentConfig.load(config_path))
            client.plan("joint", "完整提示词", {"situation": {"blue": "蓝方"}})
            output = StringIO()
            with redirect_stdout(output):
                code = monitor_main(["--llm-config", str(config_path), "--last", "2", "--once"])
            self.assertEqual(code, 0)
            self.assertIn("完整提示词", output.getvalue())
            self.assertIn("蓝方", output.getvalue())

    def test_input_and_output_have_same_call_id_and_full_payload(self):
        with TemporaryDirectory() as folder:
            config = AgentConfig(base_url="http://127.0.0.1:1/v1", model="mock",
                                 objective="测试", log_dir=folder)
            client = FakeClient(config)
            client.set_trace_scope(scenario_name="测试想定", cycle=3)
            result = client.plan("joint", "系统提示", {"situation": {"blue": "蓝方"}})
            client.trace_event("component_army", "skip", reason="无单位")
            events = [json.loads(line) for line in
                      (Path(folder) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event["event"] for event in events], ["input", "output", "skip"])
            self.assertEqual(events[0]["call_id"], events[1]["call_id"])
            self.assertEqual(events[0]["session_id"], events[1]["session_id"])
            self.assertEqual(events[0]["scenario_name"], "测试想定")
            self.assertEqual(events[1]["cycle"], 3)
            self.assertEqual(events[0]["input"]["system"], "系统提示")
            self.assertEqual(events[0]["input"]["context"]["situation"]["blue"], "蓝方")
            self.assertEqual(events[1]["output"], result)
            self.assertEqual(events[1]["raw_output"], FakeClient.response)

    def test_invalid_model_output_is_recorded(self):
        with TemporaryDirectory() as folder:
            config = AgentConfig(base_url="http://127.0.0.1:1/v1", model="mock",
                                 objective="测试", log_dir=folder)
            client = FakeClient(config)
            client.response = "not json"
            with self.assertRaises(ModelError):
                client.plan("component_air", "提示", {})
            events = [json.loads(line) for line in
                      (Path(folder) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event["event"] for event in events], ["input", "error"])
            self.assertEqual(events[1]["raw_output"], "not json")
            self.assertEqual(events[1]["error"]["type"], "ModelError")


if __name__ == "__main__":
    unittest.main()
