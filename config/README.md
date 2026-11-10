# 本机部署配置

这里放连接与运行参数，不放 Agent 规则或任务脚本。改完配置后重启 `run.py`。

| 文件 | 作用 | 常改字段 |
| --- | --- | --- |
| `deployment.json` | 墨子地址、自动推演目标、轮询/重规划间隔、脚本并发数 | `mozi_host`、`mozi_ws_port`、`mozi_api_base`、`auto_instruction`、`poll_interval_sec`、`replan_interval_sec`、`script_parallelism` |
| `deployment.json` → `jammer_support` | 飞机打击任务的干扰机伴随控制 | `enabled`、`trail_km`、`follow_interval_sec`、`launch_retry_sec`、`max_wait_sim_sec` |
| `llm.json` | 当前本机模型连接与 Agent 调用预算 | `base_url`、`model`、`timeout_sec`、`joint_interval_sec`、`component_parallelism`、`log_dir` |
| `llm.example.json` | 新电脑的配置参考 | 复制后填写适合本机的值 |
| `*.key.local` | 本机模型密钥文件，若存在 | 不提交到 GitHub，不写入 README 或日志 |

`deployment.json` 中的 `llm_config` 相对本目录解析。`llm.json` 中的 `api_key_file`、`log_dir` 也相对本目录解析。密钥优先从 `api_key_env` 指定的环境变量读取。用 `python run.py doctor` 做只读连接检查；用 `python run.py agent-status` 查看 Agent 与模型配置。`component_max_tokens` 默认 8192，限制空军、预警机、海军、陆军各次模型调用的输出，不限制态势输入；联合指挥仍为 4096。硬规则和阈值在 `rules/`，不靠改提示词实现。
