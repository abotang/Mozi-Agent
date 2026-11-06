# LLM Agent

本目录有五个角色：联合指挥、空军、预警机、海军、陆军。联合指挥读全局态势；预警机 Agent 只接收预警机和预警 Skill，空军 Agent 不再处理预警任务。陆军目前没有地面单元执行的固定脚本，因此只形成方案和能力缺口。

`roles.py` 定义五个角色，`prompt_loader.py` 读取根目录 `prompts/` 中各角色的提示词，`model_client.py` 调用兼容 Chat Completions 的模型服务。`runtime/planning/planner.py` 组织五个 Agent 的调用。模型地址见 `config/llm.json`，密钥优先从 `MOZI_QWEN_API_KEY` 环境变量读取，也可使用本项目 `config/qwen.key.local`。

`python run.py agent-status` 查看注册和配置，不发起模型请求；`plan` 和 `live-orchestrate` 才调用 LLM。
