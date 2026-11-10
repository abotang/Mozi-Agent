# 墨子态势读取

| 代码 | 作用 |
| --- | --- |
| `live_situation.py` | 从墨子 WebSocket 读取态势帧，从 REST 读取想定时间与任务占用；筛出蓝方现有单位和当前接触 |
| `mission_assignments.py` | 只读 `MissionGant`，把已分配任务的蓝方飞机映射到任务 GUID；未知格式或读取失败时停止新任务规划 |
| `mission_catalog.py` | 只读蓝方任务名称、GUID 和类型，供不确定预警任务的安全恢复核对；读取失败时不解除阻断 |
| `__init__.py` | 包标记 |

主要入口 `collect_live_situation(...)` 返回供规划层使用的 `side`、`scenario`、`units`、`contacts`、`mission_assignments` 等结构。它是**只读采集**，不创建任务；`runtime/app/daemon.py` 在其结果上补入本项目已有任务、运行规则和精简态势记忆。敌方真实单元 ID 不能替代蓝方接触 GUID。换墨子地址在 `config/deployment.json` 设置。
