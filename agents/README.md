# Agent 模型层

本目录只定义 Agent 角色、提示词读取和模型通信。任务选择由 `runtime/planning/planner.py` 编排；创建墨子任务由固定脚本完成。

| 代码 | 职责 | 主要输入 → 输出 |
| --- | --- | --- |
| `roles.py` | 联合指挥、空军、预警机、海军、陆军五个角色及各自的 `role_key` | 提示词与上下文 → 结构化方案 |
| `prompt_loader.py` | 按角色读取根目录 `prompts/` | 角色名 → 提示词文本 |
| `model_client.py` | 读取 `config/llm.json`、调用兼容 Chat Completions 的服务、解析 JSON、统计用量 | system prompt 与 context → JSON 对象或 `ModelError` |
| `monitor_log.py` | 把输入、原始输出、错误、耗时和跳过事件写入本地 JSONL | Agent 事件 → `runs/agent_monitor/events.jsonl` |
| `__init__.py` | 导出五个角色类 | Python 包入口 |

`model_client.py` 每次请求只发送本轮 system prompt 和 context；跨轮态势摘要由 `rules/situation_memory.py` 生成，再由规划器注入。模型密钥只从环境变量或本机密钥文件读取，不写入监控日志。修改提示词去 `prompts/`；修改调用顺序去 `runtime/planning/`。请求按 objective、task_skills/available_task_skills、动态态势的顺序序列化；稳定的 Skill 合同位于变化信息之前，实际缓存命中与耗时以模型服务返回的 usage 和监控记录为准。
