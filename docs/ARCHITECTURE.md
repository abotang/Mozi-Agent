# 脚本 Skill 任务调度架构

## 1. 保留和替换的部分

保留：联合指挥 LLM 的全局判断、空军/预警机/海军/陆军 Agent 的任务分工、当前态势作为事实源、任务方案审计。

替换：旧架构把军种方案转换为 `Task/Assignment` 后交给行为树逐单元执行；本架构把军种方案转换为经过注册的 `skill_calls`，再调用已经写好并实测的墨子任务脚本。

本目录不修改旧 `src/behavior_tree`、`src/task_trees`、`src/execution` 或旧启动入口，可以并行对比和回退。

## 2. 分层职责

| 层 | 职责 | 不允许做的事 |
| --- | --- | --- |
| 联合指挥 Agent | 全局态势、主要方向、军种任务意图 | 选单位、画区域、生成脚本 |
| 空军、预警机、海军、陆军 Agent | 选择各自可控单位、目标、区域和已注册 Skill | 调用其他 Agent 的 Skill、写代码、虚构目标 |
| Skill 注册表 | 公布可用能力和输入规则 | 执行模型文本 |
| 方案格式转换层 | 修正含义明确的字段格式 | 猜测单位、目标或区域 |
| 规则校验器 | 校验归属、GUID、目标类别、区域、重复分配 | 猜测或自动补造关键参数 |
| 编译器 | 把 `skill_calls` 转成固定脚本 JSON 配置 | 修改脚本逻辑 |
| 脚本执行器 | 先全量 `-ValidateOnly`；自动推演对独立任务限流并行，单任务内部顺序执行 | 使用 shell 字符串执行模型内容 |
| 墨子脚本 | 创建区域、任务、目标、航线并分配单元 | 接受未校验的自由文本 |

## 3. 为什么更快

任务创建的 API 顺序、字段、回读和错误处理已经固化在脚本中。LLM 每轮只生成很小的结构化调用：

```json
{
  "call_id": "cap-north",
  "skill_id": "air.combat_patrol",
  "mission_name": "蓝方-北区战斗巡逻",
  "unit_guids": ["飞机GUID"],
  "area": {"points": [{"lat": 1, "lon": 2}, {"lat": 1, "lon": 3}, {"lat": 2, "lon": 3}]}
}
```

它不需要理解或重新输出 `createMission`、`PatrolMissionRules`、`AssignUnitToMission` 等底层 API，也不需要重新生成几百行 PowerShell。

## 4. 多任务与同类多实例

`skill_calls` 是数组，因此一次方案可创建多个任务，也可以多次调用同一个 Skill。每个调用独立生成配置和报告。`call_id`、`mission_name`、主任务单元必须唯一；不同任务可指向同一个敌方 contact。

## 5. 扩展模型

注册表扫描：

```text
skills/
├─ air/<task>/skill.json + SKILL.md
├─ navy/<task>/skill.json + SKILL.md
└─ army/<task>/skill.json + SKILL.md
```

新增任务只需新增固定脚本和一个 Skill 目录。预警机任务仍归 `air` 执行合同，但在 manifest 设置 `agent_owner: "awacs"`，规划时只提供给预警机 Agent。顶层联合指挥、军种并行调用、编译/执行流程无需改变。若未来参数语义超出现有七任务字段，再以向后兼容方式扩展 `SkillCall` 合同。

## 6. 执行边界

当前军种可控类型固定为：空军 `Aircraft`、海军 `Ship`、陆军 `Facility`。`Submarine` 单独保留在态势中，不属于当前三个军种的可执行单位。实时态势采集器和任务规则校验器均执行该归属规则；将来潜艇任务需要新增独立的潜艇 Agent/Skill 和服务合同。

当前脚本完成的是“墨子任务创建与分配”。墨子任务引擎随后负责按任务规则行动；上层 LLM 可在新态势或任务事件到来时再次生成增量 Skill 调用。更新已有任务时填写 `existing_mission_guid`；新建任务保持为空。

任务删除和区域删除已有固定脚本，但当前不授权 LLM 自动删除，避免一次错误规划造成不可逆状态变化。需要时可在后续增加单独的控制 Skill 和审批策略。
