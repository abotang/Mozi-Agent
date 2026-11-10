# 陆军任务 Skill

当前首批七个固定脚本中，没有由蓝方地面单元直接执行的任务：飞机对地打击属于空军。因此陆军 Agent 当前只参与总体方案和跨军种需求判断，不产生 `skill_calls`，也不会临时编写代码。

本目录目前只有这个 README，没有 `skill.json`、`SKILL.md` 或地面任务代码；`runtime/skills/registry.py` 因而不会注册陆军 Skill，`rules/agent_activation.py` 会跳过无任务可做的陆军 Agent。

后续新增地面防空、对地攻击、陆海巡逻、地面警戒等固定脚本时，在本目录按“一任务一 Skill”增加子目录、`skill.json`、`SKILL.md` 和子目录 README，运行时会自动发现；同时要提供脚本和离线测试。飞机对地打击仍属于空军任务。
