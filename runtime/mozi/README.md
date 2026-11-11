# 墨子态势读取

| 代码 | 作用 |
| --- | --- |
| `live_situation.py` | 从墨子 WebSocket 读取态势帧，从 REST 读取想定时间、任务占用和现行航空巡逻/预警区；筛出蓝方现有单位和当前接触 |
| `mission_assignments.py` | 只读 `MissionGant`，缺少 `assignUnit` 时回读 `Mission` 详情的 `assignedCombatUnit`；把已分配任务的蓝方飞机映射到任务 GUID，仍无法确认时停止新任务规划 |
| `mission_catalog.py` | 只读蓝方任务名称、GUID 和类型，供不确定预警任务的安全恢复核对；读取失败时不解除阻断 |
| `patrol_areas.py` | 只读 `ScenArea` 的 405 空战巡逻区、407 预警区；按 `Mission` 当前区域 ID 过滤滞后的 `missionList`，向规划层提供真实多边形；任务详情读取失败时停止规划，已关联区域坐标异常时保留区域和任务关联并标记 `geometry_error` |
| `__init__.py` | 包标记 |

主要入口 `collect_live_situation(...)` 返回供规划层使用的 `side`、`scenario`、`units`、`contacts`、`mission_assignments`、`existing_areas` 等结构。`existing_areas` 的点统一为 `lat`/`lon`，含 `type_id`、当前关联任务 GUID；它还包含非本项目创建但仍被任务实际使用的区域。它是**只读采集**，不创建任务；`runtime/app/daemon.py` 在其结果上补入本项目已有任务、运行规则和精简态势记忆。敌方真实单元 ID 不能替代蓝方接触 GUID。换墨子地址在 `config/deployment.json` 设置。
