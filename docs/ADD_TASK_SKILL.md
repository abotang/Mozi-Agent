# 新增任务 Skill

## 步骤

1. 先把该任务的墨子 API 流程写成固定、可参数化、可回读验证的 PowerShell 脚本。
2. 将入口脚本放入 `scripts/powershell/`。脚本必须支持 `-ConfigPath` 和 `-ValidateOnly`，不得依赖 LLM 生成代码。
3. 在对应军种目录创建一个子目录，加入 `skill.json` 和 `SKILL.md`。
4. 在 manifest 中声明军种、任务键、脚本、单位类型、是否需要区域/目标、目标类别和能力开关。若由专门的预警机 Agent 规划空军 Skill，设置 `"agent_owner": "awacs"`；省略时归所属军种 Agent。
5. 任务专用的确定性规则单独放进根目录 `rules/`，从规划或运行层调用；跨任务通用的 GUID、军种归属等校验留在 `runtime/planning/validation.py`。
6. 增加规则校验和编译测试；执行 `python run.py list-skills` 确认自动发现。

## Skill manifest 最小示例

```json
{
  "schema_version": 1,
  "skill_id": "army.ground_patrol",
  "service": "army",
  "display_name": "地面巡逻",
  "description": "为蓝方地面机动单位创建地面巡逻任务。",
  "task_key": "groundPatrol",
  "script": "09-地面巡逻.ps1",
  "mission_type": "Patrol",
  "classify": "GROUND",
  "unit_types": ["Facility"],
  "requires_area": true,
  "area_type_id": "421",
  "requires_target": false,
  "target_kind": null,
  "supports_escort": false,
  "supports_loadout": false,
  "supports_routes": true,
  "parameter_schema": {
    "speed_kmh": {"type": "number", "minimum": 1, "maximum": 200, "required": false}
  },
  "rules": ["必须确认单位具备机动能力"]
}
```

## 扩展原则

- 一种明确任务语义对应一个 Skill，不做一个接受任意自由文本的“万能脚本”。
- 适用条件写进 manifest，任务专用计算与限制写进 `rules/`，API 步骤放在固定脚本，LLM 只选择任务和填参数。
- 目标 GUID 必须来自当前蓝方接触；区域、目的地、货物按任务语义分别建模。
- 新任务先离线验证、再接测试想定、最后加入 LLM 可见目录。
- 任务专用参数写入 `parameter_schema`；可用类型为 `string/number/integer/boolean/array/object`，可声明 `required`、`enum`、`minimum`、`maximum`。Agent 传入未声明参数会被拒绝。
