# Agent 规划与校验

本目录把当前态势变成已注册 Skill 的调用参数，负责协议边界，不直接访问墨子写接口。

| 代码 | 作用 |
| --- | --- |
| `planner.py` | 先调用或复用联合方案，再并行评估空军、预警机、海军、陆军；空军输入排除干扰机，主任务和护航 GUID 均限其 `own_units` |
| `plan_adapter.py` | 对含义明确的 LLM 字段别名和经纬度点列表做格式转换；不补造目标、单位或任务意图 |
| `validation.py` | 校验蓝方单元归属、接触目标类别、区域/航线、Skill 参数、GUID、重复分配等；拒绝普通空军任务或护航字段分配干扰机，产出 `SkillCall` |
| `__init__.py` | 包标记 |

输入是 `runtime/mozi/` 的当前态势、`rules/` 的触发与历史摘要，以及 `skills/` 注册的公开合同。输出经 `runtime/skills/compiler.py` 转为固定脚本配置。联合指挥只提总体任务意图；具体单位、目标、区域由专业 Agent 选择，并接受硬规则校验。详细字段兼容规则见 `docs/PLAN_ADAPTER.md`。
