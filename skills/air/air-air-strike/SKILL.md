# 飞机对空打击

用于主动打击蓝方当前已探测的敌方飞机接触。Agent 选择主攻机、可选护航机以及 `contacts.air` 中的目标 GUID；固定脚本负责创建 Strike/Air 任务、分配主攻/护航单元并绑定目标。

必填：`mission_name`、`unit_guids`、`target_contact_guids`。目标只能来自蓝方当前接触，不能填写红方真实单元 GUID。任务不使用 `area`。
