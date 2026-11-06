"""供上层 Agent 系统一行调用的稳定入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.skills.compiler import PlanCompiler
from runtime.execution.executor import PowerShellMissionExecutor
from runtime.execution.orchestrator import ScriptSkillOrchestrator
from runtime.planning.planner import HierarchicalSkillPlanner
from runtime.skills.registry import SkillRegistry


ROOT = Path(__file__).resolve().parents[2]


def _orchestrator(*, planner=None, run_root: str | Path | None = None) -> ScriptSkillOrchestrator:
    registry = SkillRegistry(ROOT / "skills").load()
    compiler = PlanCompiler(
        registry,
        ROOT / "scripts" / "powershell",
        Path(run_root).resolve() if run_root else ROOT / "runs",
    )
    return ScriptSkillOrchestrator(
        registry,
        compiler,
        PowerShellMissionExecutor(),
        planner=planner,
    )


def dispatch(plan: dict[str, Any], situation: dict[str, Any], *,
             execute: bool = False, run_id: str | None = None,
             run_root: str | Path | None = None) -> dict[str, Any]:
    """执行已有结构化方案；execute=False 时只做全量脚本参数校验。"""
    return _orchestrator(run_root=run_root).execute_plan(
        plan, situation, dry_run=not execute, run_id=run_id
    )


def plan_and_dispatch(situation: dict[str, Any], instruction: str, llm_config: str | Path, *,
                      execute: bool = False, run_id: str | None = None,
                      run_root: str | Path | None = None) -> dict[str, Any]:
    """联合/军种 LLM 选 Skill 并填参数，然后调用固定脚本。"""
    registry = SkillRegistry(ROOT / "skills").load()
    planner = HierarchicalSkillPlanner.from_config(llm_config, registry)
    compiler = PlanCompiler(
        registry,
        ROOT / "scripts" / "powershell",
        Path(run_root).resolve() if run_root else ROOT / "runs",
    )
    orchestrator = ScriptSkillOrchestrator(
        registry, compiler, PowerShellMissionExecutor(), planner=planner
    )
    return orchestrator.plan_and_execute(
        situation, instruction, dry_run=not execute, run_id=run_id
    )
