# Agent 方案格式转换层

`runtime/planning/plan_adapter.py` 位于 Agent 方案与确定性规则校验之间。`validate_plan()` 会先调用 `normalize_plan()`，再校验单位、目标、区域和 Skill 适用性。自动推演、交互入口及外部传入的方案共用这一入口。

转换层只做含义明确的等价转换，不修改原始方案，也不补造任务决策：

| Agent 写法 | 标准写法 |
| --- | --- |
| `unitGuids` 等已知驼峰字段 | `unit_guids` 等协议字段 |
| 单个单位或目标 GUID 字符串 | 只含该 GUID 的数组 |
| `target_guid`、`target_guids`、`target_contact_guid`、`targetContactGuid`、`targetContactGuids` | `target_contact_guids` 数组，删除原别名字段 |
| `area: [{"lat": ..., "lon": ...}, ...]` | `area: {"points": [...]}` |
| `routes.outbound` / `routes.on_station` 直接写坐标点数组 | 包装为 `{"points": [...]}` |
| 可选航线写空数组 | `null`（不创建该航线） |
| 已提供的可选 GUID 数组为 `null`、`parameters` 为 `null` | 空数组、空对象 |
| 坐标点中的 `Latitude` / `Longitude` | `lat` / `lon` |

目标别名先统一为数组再比较；例如 `target_guid: "G"` 与 `target_contact_guids: ["G"]` 可以同时出现。若归一化后的值不同，会报冲突错误，不选择其中某个目标。其他别名和标准字段同时出现且值不同时也会报冲突错误。无法确认含义的结构不会被猜测，例如坐标写成 `[纬度, 经度]`、缺少打击目标、缺少巡逻区域，均交给规则校验拒绝。每条新航线仍至少需要两个合法坐标点；巡逻区仍至少需要三个合法坐标点。打击目标仍必须是蓝方当前接触。

新增 Skill 的格式别名应在此层明确登记；任务业务规则仍写在 `runtime/planning/validation.py` 和对应 Skill 合同中。
