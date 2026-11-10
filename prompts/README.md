# 五个 Agent 的提示词

这里是可编辑的静态角色要求。提示词说明 Agent 应如何分析、选择 Skill 与输出 JSON；任务边界、触发条件和目标有效性仍由 `rules/` 与 `runtime/planning/validation.py` 的代码强制执行。

| 文件 | 接收的任务 |
| --- | --- |
| `joint.md` | 联合指挥：看全局态势和精简历史，给出分军种任务意图，不选具体单位 |
| `air.md` | 空军：非预警飞机的巡逻及对空、对海、对地打击 |
| 独立规则 | 电子干扰机不由空军 Agent 选为主攻机；已创建飞机打击任务后由 `rules/jammer_support.py` 与固定脚本派出 |
| `awacs.md` | 预警机：开局侦察、调整区域、受威胁回撤和换班 |
| `navy.md` | 海军：水面舰艇的开局巡逻与对海攻击，不控制潜艇 |
| `army.md` | 陆军：仅使用已注册的陆军 Skill；当前没有可执行陆军脚本 |

调用路径：`agents/prompt_loader.py` 读取本文档同级的角色文件 → `runtime/planning/planner.py` 组装当轮上下文 → `agents/model_client.py` 请求模型。修改文件后重启 `run.py`。态势记忆不是对话原文，来源和上限见 `rules/situation_memory.py`。
