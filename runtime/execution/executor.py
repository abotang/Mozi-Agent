"""非 shell 拼接的 PowerShell 执行器；先全量校验，再顺序执行。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable

from runtime.contracts import ExecutionResult, MissionInvocation
from runtime.skills.compiler import PlanCompiler


class ScriptExecutionError(RuntimeError):
    def __init__(self, message: str, results: list[ExecutionResult]):
        super().__init__(message)
        self.results = results


def find_powershell(explicit: str | None = None) -> str | None:
    if explicit:
        candidate = shutil.which(explicit) or (explicit if Path(explicit).is_file() else None)
        if not candidate:
            raise RuntimeError(f"配置的 PowerShell 不存在：{explicit}")
        if Path(candidate).name.lower() not in {"pwsh", "pwsh.exe"}:
            raise RuntimeError("任务脚本只支持 PowerShell 7（pwsh）")
        return candidate
    configured = os.environ.get("MOZI_POWERSHELL_PATH")
    if configured:
        return find_powershell(configured)
    bundled = Path(__file__).resolve().parents[2] / "tools" / "powershell7" / "pwsh.exe"
    if bundled.is_file():
        return str(bundled)
    preferred = shutil.which("pwsh")
    if preferred:
        return preferred
    if os.name == "nt":
        for root in (os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles"),
                     r"C:\Program Files"):
            if root:
                candidate = Path(root) / "PowerShell" / "7" / "pwsh.exe"
                if candidate.is_file():
                    return str(candidate)
    return None


class PowerShellMissionExecutor:
    def __init__(self, executable: str | None = None, timeout_sec: int = 180,
                 runner: Callable[..., subprocess.CompletedProcess] | None = None):
        self.executable = find_powershell(executable)
        if not self.executable:
            raise RuntimeError("找不到 PowerShell 7（pwsh）；请安装 PowerShell 7 或配置 MOZI_POWERSHELL_PATH")
        self.timeout_sec = timeout_sec
        self.runner = runner or subprocess.run

    def _run(self, item: MissionInvocation, validate_only: bool) -> ExecutionResult:
        command = [
            self.executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", str(item.script_path),
            "-ConfigPath", str(item.config_path),
        ]
        if validate_only:
            command.append("-ValidateOnly")
        completed = self.runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_sec,
            check=False,
            shell=False,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        try:
            payload = json.loads(stdout.lstrip("\ufeff"))
        except (ValueError, TypeError):
            payload = None
        if validate_only:
            protocol_ok = isinstance(payload, dict) and payload.get("valid") is True
        else:
            tasks = payload.get("tasks") if isinstance(payload, dict) else None
            protocol_ok = (isinstance(payload, dict)
                           and payload.get("success") is True
                           and isinstance(tasks, list) and bool(tasks)
                           and all(isinstance(task, dict) and task.get("missionGuid")
                                   for task in tasks)
                           and item.report_path.is_file())
        return ExecutionResult(
            call_id=item.call.call_id,
            skill_id=item.call.skill_id,
            phase="validate" if validate_only else "execute",
            success=completed.returncode == 0 and not stderr and protocol_ok,
            return_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            config_path=str(item.config_path),
            report_path=str(item.report_path),
        )

    def execute(self, invocations: list[MissionInvocation], *, dry_run: bool = False) -> list[ExecutionResult]:
        PlanCompiler.materialize(invocations)
        results: list[ExecutionResult] = []

        # 两阶段提交：任何配置校验失败时，一个任务也不创建。
        for item in invocations:
            result = self._run(item, validate_only=True)
            results.append(result)
            if not result.success:
                detail = (result.stderr or result.stdout or "脚本未返回有效的校验结果")[:1200]
                raise ScriptExecutionError(f"任务 {result.call_id} 脚本参数校验失败：{detail}", results)
        if dry_run:
            return results

        # 墨子任务接口存在顺序依赖，执行阶段故意串行。
        for item in invocations:
            result = self._run(item, validate_only=False)
            results.append(result)
            if not result.success:
                detail = (result.stderr or result.stdout or "脚本未返回任务 GUID 和执行报告")[:1200]
                raise ScriptExecutionError(
                    f"任务 {result.call_id} 执行失败；后续任务未执行：{detail}", results)
        return results


def write_execution_audit(path: str | Path, *, plan: dict, results: list[ExecutionResult]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "plan": plan,
        "results": [item.to_dict() for item in results],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
