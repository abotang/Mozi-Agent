"""LLM 方案与确定性脚本执行之间的总编排。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.skills.compiler import PlanCompiler
from runtime.execution.executor import PowerShellMissionExecutor, write_execution_audit
from runtime.skills.registry import SkillRegistry
from runtime.planning.validation import validate_plan


class ScriptSkillOrchestrator:
    def __init__(self, registry: SkillRegistry, compiler: PlanCompiler,
                 executor: PowerShellMissionExecutor, planner=None):
        self.registry = registry
        self.compiler = compiler
        self.executor = executor
        self.planner = planner

    def prepare(self, plan: dict[str, Any], situation: dict[str, Any], run_id: str | None = None):
        calls = validate_plan(plan, situation, self.registry)
        return self.compiler.compile(calls, situation, run_id=run_id)

    def execute_plan(self, plan: dict[str, Any], situation: dict[str, Any], *,
                     dry_run: bool = False, run_id: str | None = None) -> dict[str, Any]:
        selected_run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        invocations = self.prepare(plan, situation, selected_run_id)
        results = self.executor.execute(invocations, dry_run=dry_run)
        run_dir = (invocations[0].config_path.parents[1] if invocations
                   else self.compiler.run_root / selected_run_id)
        audit_path = run_dir / "audit.json"
        write_execution_audit(audit_path, plan=plan, results=results)
        return {
            "run_id": selected_run_id,
            "dry_run": dry_run,
            "skill_call_count": len(invocations),
            "audit_path": str(audit_path),
            "results": [item.to_dict() for item in results],
        }

    def plan_and_execute(self, situation: dict[str, Any], instruction: str, *,
                         dry_run: bool = True, run_id: str | None = None) -> dict[str, Any]:
        if self.planner is None:
            raise RuntimeError("当前 Orchestrator 未配置 LLM planner")
        plan = self.planner.plan(situation, instruction)
        result = self.execute_plan(plan, situation, dry_run=dry_run, run_id=run_id)
        result["plan"] = plan
        return result
