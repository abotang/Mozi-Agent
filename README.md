# 重构版：LLM → 任务 Skill → 固定脚本 → 墨子 API

各源码目录都有 `README.md`，逐个列出代码文件职责、输入输出与修改位置。先看 [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) 的目录索引，再进入对应目录阅读；自动生成的 `runs/` 和随附的 PowerShell 工具也有上层说明。

本文件夹可单独放在桌面运行。需要 Python 3.10+；项目自带 PowerShell 7.6.9，默认使用 `tools/powershell7/pwsh.exe`，不使用 Windows PowerShell 5.1。实时读取墨子态势时安装 `requirements.txt` 中的依赖。模型服务配置参考 `config/llm.example.json`，将密钥保存在环境变量中。

直接在 VS Code 中运行 `run.py`（不带参数）启动自动推演：持续读取墨子态势，调用联合指挥、空军、预警机、海军、陆军 Agent，校验 Skill 参数并自动执行固定脚本。按 Ctrl+C 停止。当前轮处理耗时会打印在控制台；已创建任务记在 `runs/automatic_state.json`，避免重复创建。新电脑先修改 `config/deployment.json` 中的墨子主机、端口、API 地址和自动任务目标，再运行 `python -m pip install -r requirements.txt`。

## 监控所有 Agent 的输入输出

在**另一个终端**运行 `python monitor_agents.py`。程序先显示最近 10 条事件，再持续显示新事件；按 Ctrl+C 只停止监控，不停止自动推演。事件包括五个 Agent 发送给模型的完整 system prompt 与 context、模型原始回复与解析后的 JSON、耗时、token 用量和调用错误。联合指挥复用缓存、军种 Agent 被规则跳过时也会记录原因；这两类事件没有模型请求。

```powershell
python .\monitor_agents.py
python .\monitor_agents.py --summary              # 精简查看
python .\monitor_agents.py --agent component_awacs # 只看预警机
python .\monitor_agents.py --last 50 --once        # 查看历史后退出
```

记录写入 `runs/agent_monitor/events.jsonl`，每行一个 UTF-8 JSON 事件；`config/llm.json` 的 `log_dir` 可改保存目录。日志只在本机，已被 `.gitignore` 排除；其中包含完整态势、提示词和模型输出，但不包含 API 密钥或请求头。若要从启动时就看到输入，先开监控终端再启动 `run.py`。

### 独立网页监控前端

双击根目录的 `启动Agent监控前端.cmd`，或在另一个终端运行 `python monitor_dashboard.py`，浏览器会打开 `http://127.0.0.1:8765/`。网页可以按推演场次、Agent、状态和关键词筛选，查看完整输入、结构化输出、原始回复与错误，显示调用耗时，并导出**所选场次的全部事件**为 JSONL。网页每 2 秒读取新增记录；它只读取本地日志，不连接模型，也不创建或修改墨子任务。
若端口 8765 无法监听，启动程序会选择空闲端口，并在终端打印实际访问地址。

网页和推演可以分别启动、停止。推演结束后重新运行 `monitor_dashboard.py`，左侧仍能选历史场次；日志不会因关闭网页或 `run.py` 而删除。每次启动推演程序会产生新的场次 ID，并记录想定名称和轮次。将整个项目复制到另一台电脑时，把 `runs/agent_monitor/` 一并复制才会带上历史记录。默认仅监听本机 `127.0.0.1`，不会向局域网开放。

```powershell
python .\monitor_dashboard.py
python .\monitor_dashboard.py --port 8766 --no-browser
```

自动推演会先校验本轮全部任务，再按 `config/deployment.json` 的 `script_parallelism` 并行执行独立任务脚本，默认最多 **4** 个；设为 `1` 可恢复串行，允许范围为 1～4。每个任务内部仍按创建区域、创建/更新任务、分配单位、回读的顺序调用墨子 API。并行任务各自记录成功或失败；某项失败不抹掉其他已成功任务的状态，孤立区域清理在本批全部脚本结束后运行。手动/直接批量执行入口仍保持串行。

执行器要求脚本返回有效 JSON、任务 GUID 和报告文件。若执行阶段结果不确定，任务会记入 `runs/automatic_state.json` 的 `uncertain`，自动推演不会重试该同名任务；先在墨子中核对是否已创建，再处理该记录。任务脚本只由 PowerShell 7 执行。

自动推演按固定的想定起始时间识别同一想定，并从本项目的执行报告恢复仍存在的任务。每轮规划后会清理**由本项目创建且墨子确认没有任何任务关联**的区域及其专属参考点；仍被任务引用的区域和人工创建的区域不会删除。脚本在创建区域后立即写入 `*.area.json` 记录，以便后续清理执行中断留下的孤立区域。当前策略不会自动删除旧任务。

所有向墨子发送的 JSON 请求体都由脚本转换为 UTF-8 字节，并使用 `application/json; charset=utf-8`，确保中文推演方名称、任务名称和区域名称正确传输。

舰艇海上巡逻只允许在想定开局创建一次；后续轮次不再新增舰艇巡逻任务，发现水面接触时可创建舰艇对海攻击。飞机战斗巡逻和空中预警任务启用墨子界面的“1/3规则”；脚本写入后会回读确认战斗巡逻任务的规则状态。

飞机对空、对海、对地打击任务创建成功且回读到目标和主攻机后，独立规则立即为该任务派出一架空闲、具备电子干扰与雷达设备的飞机。升空后固定脚本开启干扰和雷达，并持续更新主攻机群后方的伴随航点。干扰机不作为主攻机分配，也不依赖墨子护航标记。配置在 `config/deployment.json` 的 `jammer_support`；触发与回读细节见 [JAMMER_SUPPORT.md](docs/JAMMER_SUPPORT.md)。

空中预警只分配真正的预警机，每个任务一架。独立预警机 Agent 根据态势填写进攻轴向和向前延伸距离；`rules/awacs_area.py` 以离预警机最近的蓝方机场或航母为起飞平台，生成整体长 60 km、宽 30 km（直线段 `L=30 km`、半圆半径 `r=15 km`）的跑马道区域，长轴垂直进攻轴向，所有点距平台不超过 300 km。前推最多 284 km；无可信轴向时区域留在平台附近，长轴东西向。任务专用硬规则集中在 `rules/`，说明见 [rules/README.md](rules/README.md)。同一轮若 Agent 重复分配单位，程序会指出冲突的两个任务并立即重规划一次。

新建飞机任务前，程序从墨子只读任务列表回读当前蓝方飞机的任务归属：已分配给任务的飞机不会进入空军 Agent 的候选列表，统一校验也会拒绝把它分配给另一新任务；预警机更新原任务时允许继续使用原飞机，换班备用机必须空闲。脚本执行前再次回读，防止规划期间被其他任务占用。接口读取失败或返回无法识别的数据时停止新任务创建，不把未知当空闲。离线 `--situation` 示例只有在提供 `mission_assignments` 时才能核验历史占用；实际执行前仍会查询墨子。

预警机 Agent 默认只安排一架；是否需要第二架由它按战场态势判断并写明依据。程序检查本项目已有预警任务，最多允许两架，依据战场跨度要求两处区域中心保持间距，阻止预警区堆叠；已有覆盖充分时不应重复创建。详细规则见 [awacs_deployment.py](rules/awacs_deployment.py)。

`air.early_warning` 的 `agent_owner` 为 `awacs`，空军战斗机 Agent 无权调用。预警机 Agent 只在 `rules/awacs_triggers.py` 判定的事件上调用：开局创建；平台附近任务满 20 分钟且出现敌机后更新原任务；在空预警机 300 km 内有敌机时把原区域向平台、必要时向平台后方回撤；已观察到在空、之后返航入库时用备用机接替。无敌情时维持原区域。更新复用原任务 GUID，换班也复用原区域 GUID。触发规则与任务参数校验都在 `rules/`，LLM 只提供任务意图和参数。

如需人工操作菜单，显式运行 `python run.py interactive`；如需只读连接检查，运行 `python run.py doctor`。默认自动推演会把当前态势发送给 `config/llm.json` 配置的模型服务。

五个 Agent 的提示词分别放在根目录 `prompts/`，Agent 角色和模型客户端放在 `agents/`，规划编排在 `runtime/planning/`。实际模型配置在 `config/llm.json`。运行 `python run.py agent-status` 可查看模型、Agent 和密钥环境变量是否已配置。目录职责详见 [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)。

专业 Agent 调用前先由 `rules/agent_activation.py` 检查本军种单元、当前可用 Skill 和打击任务所需的实时接触；无可执行任务则直接待命，不请求模型。巡逻等无需敌方接触的 Skill 不因无接触而跳过。联合指挥方案在进程内缓存，`config/llm.json` 的 `joint_interval_sec` 当前为 900 秒（现实时间）；新想定、蓝方兵力变化、敌方威胁类别出现/消失或威胁距离段跨越会立即刷新，其余规划轮次沿用方案。重启程序会重新调用联合指挥。终端打印刷新原因和跳过的 Agent。

### 跨轮态势记忆

`rules/situation_memory.py` 每 10 秒轮询时用确定性代码更新目标位置采样、接近/远离蓝方的趋势、距离段变化、最近已校验方案和任务脚本下发结果，保存在 `runs/automatic_state.json`；重启 `run.py` 后继续使用同一想定的历史。送给联合指挥和专业 Agent 的是有固定上限的精简视图，不是完整监控日志，也不需要额外模型调用。想定时间倒退时会清除上一局轨迹及联合方案缓存。历史目标只供分析，打击任务仍必须使用本轮实时 `contacts`。历史方案只是意图；脚本创建成功/失败也不等于战果或目标毁伤。

`rules/situation_refresh.py` 让同 GUID 敌方接触移动至少 40 km 且距上次规划至少 30 秒现实时间，或跨越 300/150/75 km 威胁距离段时提前重评。联合指挥只在战略距离段变化等关键事件上立即刷新；普通位移由相应专业 Agent 使用当前态势和历史趋势评估。阈值、采样上限及规则注释集中在 `rules/`。

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

联合指挥和各任务 Agent 每次调用本身是独立推理；跨轮连续性由 `runs/automatic_state.json` 中的精简态势记忆及任务状态、墨子中仍存在的任务和执行报告提供。程序重启后会按想定身份恢复本项目创建且仍存在的任务，避免把 LLM 对话历史无限塞入上下文。

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
- 所有调用先完成本地规则校验，再对所有脚本运行 `-ValidateOnly`；全部通过后，自动推演按配置并行执行独立任务，手动批量入口保持串行。
- PowerShell 使用参数数组调用，不使用 `shell=True`，LLM 文本不能变成命令行代码。
- 每次运行保存独立配置、脚本输出和 `audit.json`。

详细设计见 [ARCHITECTURE.md](docs/ARCHITECTURE.md)，格式转换规则见 [PLAN_ADAPTER.md](docs/PLAN_ADAPTER.md)，增加新任务见 [ADD_TASK_SKILL.md](docs/ADD_TASK_SKILL.md)。
