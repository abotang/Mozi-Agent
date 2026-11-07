# 重构版：LLM → 任务 Skill → 固定脚本 → 墨子 API

本文件夹可单独放在桌面运行。需要 Python 3.10+；项目自带 PowerShell 7.6.9，默认使用 `tools/powershell7/pwsh.exe`，不使用 Windows PowerShell 5.1。实时读取墨子态势时安装 `requirements.txt` 中的依赖。模型服务配置参考 `config/llm.example.json`，将密钥保存在环境变量中。

直接在 VS Code 中运行 `run.py`（不带参数）启动自动推演：持续读取墨子态势，调用联合指挥、空军、预警机、海军、陆军 Agent，校验 Skill 参数并自动执行固定脚本。按 Ctrl+C 停止。当前轮处理耗时会打印在控制台；已创建任务记在 `runs/automatic_state.json`，避免重复创建。新电脑先修改 `config/deployment.json` 中的墨子主机、端口、API 地址和自动任务目标，再运行 `python -m pip install -r requirements.txt`。

执行器要求脚本返回有效 JSON、任务 GUID 和报告文件。若执行阶段结果不确定，任务会记入 `runs/automatic_state.json` 的 `uncertain`，自动推演不会重试该同名任务；先在墨子中核对是否已创建，再处理该记录。任务脚本只由 PowerShell 7 执行。

自动推演按固定的想定起始时间识别同一想定，并从本项目的执行报告恢复仍存在的任务。每轮规划后会清理**由本项目创建且墨子确认没有任何任务关联**的区域及其专属参考点；仍被任务引用的区域和人工创建的区域不会删除。脚本在创建区域后立即写入 `*.area.json` 记录，以便后续清理执行中断留下的孤立区域。当前策略不会自动删除旧任务。

所有向墨子发送的 JSON 请求体都由脚本转换为 UTF-8 字节，并使用 `application/json; charset=utf-8`，确保中文推演方名称、任务名称和区域名称正确传输。

舰艇海上巡逻只允许在想定开局创建一次；后续轮次不再新增舰艇巡逻任务，发现水面接触时可创建舰艇对海攻击。飞机战斗巡逻和空中预警任务启用墨子界面的“1/3规则”；脚本写入后会回读确认战斗巡逻任务的规则状态。

空中预警只分配真正的预警机，每个任务一架。独立预警机 Agent 根据态势填写进攻轴向和向前延伸距离；`rules/awacs_area.py` 以离预警机最近的蓝方机场或航母为起飞平台，生成整体长 60 km、宽 30 km（直线段 `L=30 km`、半圆半径 `r=15 km`）的跑马道区域，长轴垂直进攻轴向，所有点距平台不超过 300 km。前推最多 284 km；无可信轴向时区域留在平台附近，长轴东西向。任务专用硬规则集中在 `rules/`，说明见 [rules/README.md](rules/README.md)。同一轮若 Agent 重复分配单位，程序会指出冲突的两个任务并立即重规划一次。

预警机 Agent 默认只安排一架；是否需要第二架由它按战场态势判断并写明依据。程序检查本项目已有预警任务，最多允许两架，依据战场跨度要求两处区域中心保持间距，阻止预警区堆叠；已有覆盖充分时不应重复创建。详细规则见 [awacs_deployment.py](rules/awacs_deployment.py)。

`air.early_warning` 的 `agent_owner` 为 `awacs`，空军战斗机 Agent 无权调用。预警机 Agent 只在 `rules/awacs_triggers.py` 判定的事件上调用：开局创建；平台附近任务满 20 分钟且出现敌机后更新原任务；在空预警机 300 km 内有敌机时把原区域向平台、必要时向平台后方回撤；已观察到在空、之后返航入库时用备用机接替。无敌情时维持原区域。更新复用原任务 GUID，换班也复用原区域 GUID。触发规则与任务参数校验都在 `rules/`，LLM 只提供任务意图和参数。

如需人工操作菜单，显式运行 `python run.py interactive`；如需只读连接检查，运行 `python run.py doctor`。默认自动推演会把当前态势发送给 `config/llm.json` 配置的模型服务。

五个 Agent 的提示词分别放在根目录 `prompts/`，Agent 角色和模型客户端放在 `agents/`，规划编排在 `runtime/planning/`。实际模型配置在 `config/llm.json`。运行 `python run.py agent-status` 可查看模型、Agent 和密钥环境变量是否已配置。目录职责详见 [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)。

专业 Agent 调用前先由 `rules/agent_activation.py` 检查本军种单元、当前可用 Skill 和打击任务所需的实时接触；无可执行任务则直接待命，不请求模型。巡逻等无需敌方接触的 Skill 不因无接触而跳过。联合指挥方案在进程内缓存，`config/llm.json` 的 `joint_interval_sec` 当前为 900 秒（现实时间）；新想定、蓝方兵力变化、敌方威胁类别首次出现或完全消失会立即刷新，其余规划轮次沿用方案。重启程序会重新调用联合指挥。终端打印刷新原因和跳过的 Agent。

## 新电脑首次部署

```powershell
git clone https://github.com/abotang/Mozi-Agent.git
Set-Location .\Mozi-Agent
python -m pip install -r .\requirements.txt
```

项目不提交模型密钥。选择一种方式配置：

```powershell
# 方式一：只对当前 PowerShell 会话生效
$env:MOZI_QWEN_API_KEY = '你的模型服务密钥'

# 方式二：写入本机文件；该文件已被 .gitignore 排除
Set-Content .\config\qwen.key.local '你的模型服务密钥'
```

修改 `config/deployment.json` 中的墨子主机、WebSocket 端口和 REST API 地址；需要更换模型时修改 `config/llm.json`。先做只读检查：

```powershell
python .\run.py doctor
python .\run.py agent-status
```

确认输出中的 `mozi_rest_ready`、`mozi_ws_ready`、`llm_key_ready` 和最终 `ready` 都为 `true` 后，直接运行：

```powershell
python .\run.py
```

程序会进入持续自动推演循环；按 `Ctrl+C` 停止。运行产生的配置、报告、状态和错误日志保存在 `runs/`，不会上传到 GitHub。

## 目标

本目录是原“顶层 LLM + 底层行为树”项目的并行重构版，旧项目保持不变。重构只替换**创建墨子任务的执行层**：

```text
当前态势
  → 联合指挥 LLM（总体方案，不选具体单位）
  → 空军/预警机/海军/陆军 LLM（选择本军种任务 Skill 并填写参数）
  → 方案格式转换层（只修正含义明确的字段写法）
  → 本地规则校验（军种归属、区域、目标、GUID、重复分配）
  → 固定脚本调用（Agent 不生成代码）
  → 墨子 API 创建区域/任务、选择目标、分配单元
  → 回读验证与审计报告
```

Agent 的输出只是 `skill_calls`。它不写 PowerShell、不拼 API、不实现任务流程。相同任务创建多个实例时，输出多条不同参数记录即可。

各层的输入输出如下：

| 层 | 主要输入 | 主要输出 |
| --- | --- | --- |
| 态势采集 | 墨子 WebSocket 与 REST API | 蓝方单位、敌方接触、想定时间、已有任务 |
| 联合指挥 Agent | 完整蓝方态势、可用 Skill 摘要、历史任务状态 | 分军种的任务意图，不选择具体单位 |
| 军种/预警机 Agent | 本军种单位、当前接触、联合意图、本 Agent 可调用的 Skill | 带真实 GUID 和任务参数的 `skill_calls` |
| 格式转换与规则层 | LLM 的结构化输出、当前实时态势 | 统一字段、区域点、通过硬规则校验的调用 |
| 固定脚本执行层 | 已校验的 Skill 调用 | 墨子区域、任务、目标和单位分配 |
| 回读与状态层 | 墨子执行响应与任务详情 | 任务 GUID、审计报告、下一轮去重和触发状态 |

联合指挥和各任务 Agent 每次调用本身是独立推理；跨轮连续性由 `runs/automatic_state.json`、墨子中仍存在的任务以及执行报告提供。程序重启后会按想定身份恢复本项目创建且仍存在的任务，避免把 LLM 对话历史无限塞入上下文。

程序内部接入时，上层只需调用稳定入口：

```python
from runtime import dispatch

result = dispatch(plan, situation, execute=True)
```

这里的 `plan` 是 Agent 输出的结构化参数，不是临时生成的脚本代码。

## 当前七个任务 Skill

| 军种 | Skill ID | 固定脚本 | 关键规则 |
| --- | --- | --- | --- |
| 空军 | `air.combat_patrol` | `01-飞机战斗巡逻.ps1` | 必须有巡逻区；启用1/3规则 |
| 空军 | `air.air_strike` | `02-飞机对空打击.ps1` | 必须选择当前空中接触 |
| 空军 | `air.sea_strike` | `03-飞机对海打击.ps1` | 必须选择当前水面接触 |
| 空军 | `air.land_strike` | `04-飞机对地打击.ps1` | 必须选择当前地面接触 |
| 空军 | `air.early_warning` | `05-空中预警.ps1` | Agent 选轴向/前推距离，规则生成机场或航母起飞平台 300 km 内跑马道；启用1/3规则 |
| 海军 | `navy.sea_patrol` | `06-舰艇海上巡逻.ps1` | 只在想定开局创建；必须有巡逻区；航线可选 |
| 海军 | `navy.sea_strike` | `07-舰艇对海攻击.ps1` | 必须选择当前水面接触 |

飞机对地打击由飞机执行，因此属于空军。当前七个脚本没有由地面单位直接执行的任务；陆军 Skill 目录和 Agent 接口已保留，后续增加地面脚本即可自动注册。

军种归属是硬规则：空军只控制 `Aircraft`，海军只控制水面 `Ship`，陆军只控制 `Facility`。蓝方 `Submarine` 单独列在态势中，当前没有潜艇 Agent 或 Skill，不会交给海军创建任务；以后接入潜艇任务时单独扩展。

## 快速使用

在项目根目录运行：

```powershell
python .\run.py list-skills
```

先验证示例方案的本地规则：

```powershell
python .\run.py validate `
  --situation '.\examples\situation.example.json' `
  --plan '.\examples\plan.multiple.example.json'
```

调用七类脚本的 `-ValidateOnly`，但不修改想定：

```powershell
python .\run.py execute `
  --situation '.\examples\situation.example.json' `
  --plan '.\examples\plan.multiple.example.json' `
  --dry-run
```

让 LLM 只形成方案：

```powershell
python .\run.py plan `
  --situation '.\当前态势.json' `
  --llm-config '.\config\llm.json' `
  --instruction '按当前态势建立蓝方海空任务' `
  --output '.\方案.json'
```

从当前墨子态势规划并真正创建任务：

```powershell
python .\run.py live-orchestrate `
  --llm-config '.\config\llm.json' `
  --instruction '按当前态势建立蓝方海空任务' `
  --execute
```

不加 `--execute` 时只执行脚本参数校验，不创建任务。

## 关键安全与稳定机制

- 联合指挥 Agent 不得选单位、坐标或直接调用脚本。
- 每个军种 Agent 只看到并调用自己的 Skill 目录。
- 打击目标必须存在于蓝方当前 contacts，且目标类别匹配。
- 巡逻/预警任务无区域直接拒绝；打击任务不能拿区域代替目标。
- 同一批方案中一个单位只能出现一次，护航也计入占用。
- 所有调用先完成本地规则校验，再对所有脚本运行 `-ValidateOnly`；全部通过后才顺序执行。
- PowerShell 使用参数数组调用，不使用 `shell=True`，LLM 文本不能变成命令行代码。
- 每次运行保存独立配置、脚本输出和 `audit.json`。

详细设计见 [ARCHITECTURE.md](docs/ARCHITECTURE.md)，格式转换规则见 [PLAN_ADAPTER.md](docs/PLAN_ADAPTER.md)，增加新任务见 [ADD_TASK_SKILL.md](docs/ADD_TASK_SKILL.md)。
