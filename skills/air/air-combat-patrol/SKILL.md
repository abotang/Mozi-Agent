# 飞机战斗巡逻

当总体方案需要持续防空覆盖、空域警戒或战斗巡逻时使用。Agent 只需选择蓝方飞机、填写唯一任务名和巡逻区；固定脚本负责创建区域、创建任务、写入巡逻规则并分配飞机。

必填：`mission_name`、`unit_guids`、`area`。`area` 使用已有 `existing_area_guid`，或提供至少三个 `points`。换机时只替换单位 GUID；需要换挂时填写已知 `loadout_guid`。

禁止填写 `target_contact_guids`。巡逻区不是航线，也不能只给一个中心点。

固定脚本写入飞机巡逻规则时启用 `OTR=true`，对应墨子任务编辑器中的“1/3规则”；Agent 无需填写这一固定参数。
