# 项目目录与修改入口

运行入口是根目录 `run.py`。直接运行它会启动自动推演；程序读取 `config/` 配置和墨子态势，经 Agent 决策、规则校验后调用固定脚本。

自动推演的脚本并发数在 `config/deployment.json` 的 `script_parallelism` 设置；执行代码位于 `runtime/app/daemon.py`。任务在并行下发前全部通过脚本预校验，运行状态只由主线程写入。

```text
重构版_脚本Skill任务调度架构/
├─ run.py                 启动入口
├─ monitor_agents.py      独立终端实时监控 Agent 输入输出
├─ monitor_dashboard.py   独立本地网页监控入口
├─ 启动Agent监控前端.cmd   Windows 双击启动网页监控
├─ monitor_web/           网页、样式、交互及只读 HTTP 服务
├─ prompts/               五个 Agent 的可编辑提示词
│  ├─ joint.md            联合指挥
│  ├─ air.md              空军
│  ├─ awacs.md            预警机
│  ├─ navy.md             海军
│  └─ army.md             陆军
├─ agents/                Agent 角色、提示词加载、模型通信
├─ rules/                 任务专用确定性规则：预警区、海军开局巡逻等
├─ skills/                按军种和任务分类的 Skill 合同与说明
│  ├─ air/                空军五类任务
│  ├─ navy/               海军两类任务
│  └─ army/               预留陆军任务
├─ runtime/
│  ├─ app/                命令行、自动推演主循环、外部调用入口
│  ├─ mozi/               墨子实时态势读取
│  ├─ planning/           联合与军种规划、格式转换、规则校验
│  ├─ skills/             Skill 注册与脚本参数编译
│  ├─ execution/          PowerShell 7 执行与孤立区域清理
│  └─ contracts.py        各层共享的数据合同
├─ scripts/powershell/    七类固定任务脚本和任务删除脚本
├─ config/                模型与墨子连接配置；密钥文件仅保存在本机
├─ tools/powershell7/     项目自带的 PowerShell 7
├─ runs/                  自动推演状态、任务报告和错误日志
├─ tests/                 离线测试
├─ examples/              示例态势与调用参数
└─ docs/                  架构与扩展说明
```

## 目录 README 索引

每个项目源码目录都在本目录下放置了 `README.md`，说明所含代码的职责、主要输入输出和修改入口。

| 层次 | 说明入口 |
| --- | --- |
| Agent 与提示词 | [agents/README.md](agents/README.md)、[prompts/README.md](prompts/README.md) |
| 确定性规则与任务 Skill | [rules/README.md](rules/README.md)、[skills/README.md](skills/README.md)；`skills/air/`、`skills/navy/` 下七个任务目录各有 README |
| 运行时编排 | [runtime/README.md](runtime/README.md)；其 `app/`、`mozi/`、`planning/`、`skills/`、`execution/` 子目录各有 README |
| 固定脚本 | [scripts/README.md](scripts/README.md)、[scripts/powershell/README.md](scripts/powershell/README.md) |
| 配置与调用示例 | [config/README.md](config/README.md)、[examples/README.md](examples/README.md) |
| 监控前端与运行记录 | [monitor_web/README.md](monitor_web/README.md)、[runs/README.md](runs/README.md) |
| 测试、文档与随附工具 | [tests/README.md](tests/README.md)、[docs/README.md](docs/README.md)、[tools/README.md](tools/README.md) |

根目录文件：`run.py` 是自动推演和命令行入口；`monitor_agents.py` 是终端监控入口；`monitor_dashboard.py` 是网页监控入口；`启动Agent监控前端.cmd` 用于双击启动网页；`requirements.txt` 是 Python 依赖清单；`.gitignore` 排除密钥与运行数据；`README.md` 是使用说明；本文件是目录导航。

## 常改的地方

| 要调整什么 | 文件或目录 |
| --- | --- |
| 联合指挥、空军、预警机、海军、陆军的判断要求 | `prompts/joint.md`、`air.md`、`awacs.md`、`navy.md`、`army.md` |
| 墨子连接地址、自动推演间隔 | `config/deployment.json` |
| 模型、接口与密钥读取方式 | `config/llm.json`、`agents/model_client.py` |
| Agent 输入输出监控 | `monitor_agents.py`、`runtime/app/agent_monitor.py`、`agents/monitor_log.py` |
| 历史推演网页监控 | `monitor_dashboard.py`、`monitor_web/`；日志位于 `runs/agent_monitor/` |
| Skill 的规划 Agent 归属 | `skill.json` 中的 `agent_owner`；省略时归所属军种 Agent |
| 某类任务的适用条件和参数 | `skills/<军种>/<任务>/skill.json` 与 `SKILL.md` |
| 任务创建时调用墨子 API 的步骤 | `scripts/powershell/Invoke-MoziBlueMissionPlan.ps1` |
| Agent 输出的格式容错与硬规则 | `runtime/planning/plan_adapter.py`、`validation.py` |
| 预警机跑马道生成与 300 km 边界 | `rules/awacs_area.py` |
| 预警机数量、已有任务与站位间距 | `rules/awacs_deployment.py` |
| 预警机 Agent 的开局、20 分钟、300 km 威胁及换班触发 | `rules/awacs_triggers.py` |
| 专业 Agent 是否有可执行任务 | `rules/agent_activation.py` |
| 联合指挥方案何时刷新 | `rules/joint_refresh.py`；缓存位于运行中的规划器 |
| 关键态势历史及提前重评 | `rules/situation_memory.py`、`rules/situation_refresh.py`；压缩历史保存在 `runs/automatic_state.json` |
| 海军仅开局创建巡逻任务 | `rules/navy_patrol.py` |
| 打击任务触发电子干扰机伴随 | `rules/jammer_support.py`、`runtime/execution/jammer_controller.py`、`scripts/powershell/09-电子干扰伴随.ps1`；说明见 [JAMMER_SUPPORT.md](docs/JAMMER_SUPPORT.md) |
| 自动推演轮次与去重 | `runtime/app/daemon.py` |
| 孤立区域清理 | `runtime/execution/area_cleanup.py` |

修改提示词后重新启动 `run.py`，新进程会读取最新文件。修改 Skill 合同或脚本后，运行 `python -m unittest discover -s tests` 验证。任务报告和配置中的实际密钥不属于源码层，不应放入 `prompts/`。
