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

联合指挥和军种 Agent 的当轮输入均含 `operational_rules.scenario_time`：本局总时长、已过/剩余推演分钟及当前规划优先级。`scenario.start_time` 在本项目中是**当前推演时间**，开局时间为 `initial_time`。时间优先级只提醒 Agent 调整侦察、控制、打击与返航节奏；合法目标和单位仍须通过代码校验。

空军与预警机 Agent 还会收到 `existing_areas` 和 `operational_rules.patrol_spacing`。前者是墨子当前任务实际关联的航空巡逻/预警区，后者给出边界净距阈值、预警区相对战斗巡逻区的前后方判定与现有冲突。Agent 可据此调整原巡逻任务区域；最终仍由规则代码阻断重叠、间距不足或预警区前置的区域。

空军、海军、陆军三份提示词各最多 2000 字符，由加载器检查。这个数只约束静态提示词文件；每轮附加的态势 JSON 另计。`component_max_tokens` 现为 8192，是各专业 Agent 的输出上限；完整方案仍须通过 JSON 解析和规则校验才会进入任务脚本。
