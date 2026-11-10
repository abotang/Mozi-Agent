# 扩展与架构文档

本目录解释如何理解和扩展 LLM + Skill 项目；实际运行入口仍是根目录 `run.py`。

| 文档 | 看什么 |
| --- | --- |
| `ARCHITECTURE.md` | 各层职责、Agent 到固定脚本的完整调用链 |
| `PLAN_ADAPTER.md` | LLM 输出字段的兼容写法、区域点与航线点的格式转换边界 |
| `ADD_TASK_SKILL.md` | 增加一个新任务 Skill 时需要准备的合同、脚本与测试 |
| `JAMMER_SUPPORT.md` | 飞机打击任务创建后自动派出干扰机的触发、伴随和记录 |

想快速定位代码，先看根目录 `PROJECT_STRUCTURE.md`，再打开对应目录的 README。任务字段的权威定义在 `runtime/contracts.py` 和对应任务的 `skills/<军种>/<任务>/skill.json`；本目录的文字说明不能替代运行时校验。
