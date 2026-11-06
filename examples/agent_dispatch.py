"""上层 Agent 系统调用示例：不写任务脚本，只提交结构化方案。"""

import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT),):
    if path not in sys.path:
        sys.path.insert(0, path)

from runtime import dispatch


situation = json.loads((HERE / "situation.example.json").read_text(encoding="utf-8"))
plan = json.loads((HERE / "plan.multiple.example.json").read_text(encoding="utf-8"))

# execute=False：只校验；上线时确认参数后改为 True。任务逻辑始终复用固定脚本。
result = dispatch(plan, situation, execute=False, run_id="agent-example")
print(json.dumps(result, ensure_ascii=False, indent=2))
