# 墨子态势读取

| 代码 | 作用 |
| --- | --- |
| `live_situation.py` | 从墨子 WebSocket 读取态势帧，从 REST 读取想定时间、任务占用和现行航空巡逻/预警区；筛出蓝方现有单位和当前接触 |
| `mission_assignments.py` | 只读 `MissionGant`，缺少 `assignUnit` 时回读 `Mission` 详情的 `assignedCombatUnit`；把已分配任务的蓝方飞机映射到任务 GUID。可选 `active_strike_target_guids` 输出已有主攻飞机的打击任务目标，复用详情读取；不把只有护航飞机或只有舰艇的任务算作航空打击覆盖。无法确认分配或目标列表时停止规划 |
| `mission_catalog.py` | 只读蓝方任务名称、GUID 和类型，供不确定预警任务的安全恢复核对；读取失败时不解除阻断 |
| `patrol_areas.py` | 只读 `ScenArea` 的 405 空战巡逻区、407 预警区；按 `Mission` 当前区域 ID 过滤滞后的 `missionList`，向规划层提供真实多边形；任务详情读取失败时停止规划，已关联区域坐标异常时保留区域和任务关联并标记 `geometry_error` |
| `__init__.py` | 包标记 |

主要入口 `collect_live_situation(...)` 返回供规划层使用的 `side`、`scenario`、`units`、`contacts`、`mission_assignments`、`existing_areas` 等结构。`existing_areas` 的点统一为 `lat`/`lon`，含 `type_id`、当前关联任务 GUID；它还包含非本项目创建但仍被任务实际使用的区域。它是**只读采集**，不创建任务；`runtime/app/daemon.py` 在其结果上补入本项目已有任务、运行规则和精简态势记忆。敌方真实单元 ID 不能替代蓝方接触 GUID。换墨子地址在 `config/deployment.json` 设置。

`active_air_sea_target_guids` 是实时打击任务目标与本轮水面接触的交集，供对海分组规则避免跨轮重复派机。它不包含历史目标；已有任务仍有主攻飞机分配就视为已覆盖，不代表飞机已起飞或打击已成功。此读取不修改墨子想定。
