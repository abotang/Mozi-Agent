# 离线测试

这些测试用模拟态势、模拟模型或本地临时文件验证代码，默认不启动真实推演。修改对应模块后运行 `python -m unittest discover -s tests -q`。

| 测试文件 | 覆盖的代码/行为 |
| --- | --- |
| `test_agent_activation_joint_refresh.py` | `rules/agent_activation.py`、`joint_refresh.py`，联合/专业 Agent 跳过与缓存 |
| `test_agent_monitor.py` | `agents/monitor_log.py`、终端监控事件的输入输出和错误记录 |
| `test_area_cleanup.py` | `runtime/execution/area_cleanup.py`，原巡逻任务换区后即使 missionList 滞后也只清理任务详情证实不用的旧区域 |
| `test_auto_runner.py` | `runtime/app/daemon.py`，轮询、去重、并行、失败不确定态、态势记忆 |
| `test_awacs_area.py` | `rules/awacs_area.py`，机场/航母选择与跑马道几何边界 |
| `test_awacs_deployment.py` | `rules/awacs_deployment.py`，数量与最小间距 |
| `test_awacs_triggers.py` | `rules/awacs_triggers.py`，开局、敌情、回撤、换班 |
| `test_awacs_recovery.py` | 预警任务执行结果不确定时的现场任务核对、自动解锁与继续阻断条件 |
| `test_live_situation_roles.py` | `runtime/mozi/live_situation.py`，己方单位与接触分类 |
| `test_mission_assignments.py` | 墨子任务列表中飞机占用的平面/分组结构与读取失败处理 |
| `test_aircraft_occupancy.py` | 跨轮次已占用飞机不可新建任务；本项目已占用巡逻机可随完整编组更新原任务区域，执行前再次回读 |
| `test_side_doctrine.py` | 蓝方开局接战条令的触发时机与 PowerShell 执行结果校验 |
| `test_jammer_support.py` | 飞机打击任务触发、目标和空闲检查、单机派遣与后方伴随航点 |
| `test_jammer_boundary.py` | 空军 Agent 不见干扰机、LLM 护航字段和直接方案都不能分配干扰机；普通战斗机护航仍可用 |
| `test_jammer_powershell.py` | 固定干扰脚本对本地模拟 REST 的起飞、设备、航点和 UTF-8 请求 |
| `test_monitor_dashboard.py` | `monitor_web/server.py`，历史场次、分页与导出 |
| `test_plan_adapter.py` | `runtime/planning/plan_adapter.py`，字段及点列表转换 |
| `test_powershell_http_utf8.py` | PowerShell 脚本的 UTF-8 请求、任务规则与回读 |
| `test_prompt_loading.py` | 五份角色提示词的读取、隔离和长度约束 |
| `test_script_skill_runtime.py` | Skill 注册、参数校验、编译、执行适配与数据合同 |
| `test_situation_memory.py` | `rules/situation_memory.py`、`situation_refresh.py`，轨迹、上限与重评阈值 |
| `test_threat_axis.py` | 唯一当前敌方预警接触的疑似轴向推断；其他接触不虚构目标 |

测试所用 GUID 是示例，不属于当前墨子想定。真实 API 或模型连通性使用 `python run.py doctor` 做只读检查；离线测试通过不等于真实任务已创建成功。
