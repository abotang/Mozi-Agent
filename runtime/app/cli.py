"""脚本 Skill 架构命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.request import urlopen

from runtime.skills.compiler import PlanCompiler
from runtime.execution.executor import PowerShellMissionExecutor, ScriptExecutionError, find_powershell
from runtime.mozi.live_situation import collect_live_situation
from runtime.execution.orchestrator import ScriptSkillOrchestrator
from runtime.planning.planner import HierarchicalSkillPlanner
from runtime.skills.registry import SkillRegistry
from runtime.planning.validation import validate_plan
from agents.model_client import AgentConfig
from runtime.contracts import AGENT_OWNERS
from runtime.app.daemon import AutomaticMissionRunner
from runtime.execution.area_cleanup import OwnedAreaGarbageCollector
from runtime.execution.jammer_controller import JammerEscortController
from runtime.execution.side_doctrine import set_opening_engagement


ROOT = Path(__file__).resolve().parents[2]


def _json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _runtime(planner=None) -> ScriptSkillOrchestrator:
    registry = SkillRegistry(ROOT / "skills").load()
    compiler = PlanCompiler(registry, ROOT / "scripts" / "powershell", ROOT / "runs")
    executor = PowerShellMissionExecutor()
    return ScriptSkillOrchestrator(registry, compiler, executor, planner=planner)


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _deployment(path: str) -> dict:
    data = _json(path)
    if not isinstance(data, dict):
        raise ValueError("部署配置必须是 JSON 对象")
    for key in ("mozi_host", "mozi_ws_port", "mozi_api_base", "llm_config"):
        if not data.get(key):
            raise ValueError(f"部署配置缺少 {key}")
    if type(data.get("phase_control_enabled", True)) is not bool:
        raise ValueError("phase_control_enabled 必须是布尔值")
    parallelism = data.get("script_parallelism", 2)
    if type(parallelism) is not int or not 1 <= parallelism <= 4:
        raise ValueError("script_parallelism 必须是 1 到 4 的整数")
    llm = Path(data["llm_config"])
    data["llm_config"] = str(llm if llm.is_absolute() else Path(path).resolve().parent / llm)
    if data.get("powershell_executable"):
        executable = Path(data["powershell_executable"])
        if executable.parent != Path(".") and not executable.is_absolute():
            data["powershell_executable"] = str((Path(path).resolve().parent / executable).resolve())
    return data


def _doctor(deployment_path: str, registry: SkillRegistry) -> int:
    deployment = _deployment(deployment_path)
    model = AgentConfig.load(deployment["llm_config"])
    result = {
        "project": str(ROOT), "python": sys.version.split()[0],
        "powershell": find_powershell(deployment.get("powershell_executable")),
        "agents": ["联合指挥", "空军", "预警机", "海军", "陆军"],
        "skills": {service: len(registry.for_service(service)) for service in ("air", "navy", "army")},
        "agent_skills": {owner: len(registry.for_agent(owner)) for owner in AGENT_OWNERS},
        "llm_model": model.model,
        "llm_key_ready": bool(os.environ.get(model.api_key_env))
                         or bool(model.api_key_file and Path(model.api_key_file).is_file()),
        "mozi_api_base": deployment["mozi_api_base"],
        "mozi_rest_ready": False, "mozi_ws_ready": False,
        "llm_online_verified": False,
    }
    try:
        with urlopen(deployment["mozi_api_base"].rstrip("/") + "/status", timeout=4) as response:
            payload = json.load(response)
        result["mozi_rest_ready"] = payload.get("code") == 0
    except Exception as exc:
        result["mozi_rest_error"] = str(exc)
    try:
        situation = collect_live_situation(
            host=deployment["mozi_host"], ws_port=int(deployment["mozi_ws_port"]),
            api_base=deployment["mozi_api_base"])
        result["mozi_ws_ready"] = True
        result["blue_units"] = {key: len(rows) for key, rows in situation["units"].items()}
        result["blue_contacts"] = {key: len(rows) for key, rows in situation["contacts"].items()}
    except Exception as exc:
        result["mozi_ws_error"] = str(exc)
    result["ready"] = all((result["powershell"], result["llm_key_ready"],
                           result["mozi_rest_ready"], result["mozi_ws_ready"]))
    _print(result)
    return 0 if result["ready"] else 1


def _interactive(registry: SkillRegistry, deployment_path: str) -> int:
    deployment = _deployment(deployment_path)
    print("墨子 LLM + Skill 任务调度")
    print("输入任务要求后，当前墨子态势会发送至 config/llm.json 配置的模型服务。")
    print("五个 Agent 规划方案；创建任务前会再次确认。")
    while True:
        print("\n[1] 查看当前态势  [2] 发送态势并规划方案  [3] 发送态势并创建任务  [q] 退出")
        try:
            choice = input("选择操作：").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出")
            return 0
        if choice in {"q", "quit", "exit", "0"}:
            return 0
        if choice == "1":
            _doctor(deployment_path, registry)
            continue
        if choice not in {"2", "3"}:
            print("请输入 1、2、3 或 q")
            continue
        try:
            instruction = input("请输入蓝方任务要求：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消本轮")
            continue
        if not instruction:
            print("任务要求不能为空")
            continue
        try:
            situation = collect_live_situation(
                host=deployment["mozi_host"],
                ws_port=int(deployment["mozi_ws_port"]),
                api_base=deployment["mozi_api_base"],
            )
            config = AgentConfig.load(deployment["llm_config"])
            client = HierarchicalSkillPlanner(config, registry)
            print("正在调用联合指挥与空/海/陆军种 Agent，请稍候…", flush=True)
            plan = client.plan(situation, instruction)
            calls = validate_plan(plan, situation, registry)
            print("\nAgent 方案：", plan.get("summary", ""))
            if not calls:
                print("本轮没有可创建的已注册任务。")
                continue
            for number, call in enumerate(calls, 1):
                print(f"{number}. {call.mission_name} [{call.skill_id}] "
                      f"单位 {len(call.unit_guids)}，目标 {len(call.target_contact_guids)}，"
                      f"区域 {'有' if call.area else '无'}")
            if choice == "2":
                print("只展示方案，未创建任务。")
                continue
            answer = input("确认在墨子中创建以上任务？输入 YES 执行：").strip()
            if answer != "YES":
                print("已取消，墨子想定未修改。")
                continue
            result = _runtime().execute_plan(plan, situation, dry_run=False)
            _print(result)
        except ScriptExecutionError as exc:
            _print({"success": False, "error": str(exc),
                    "results": [item.to_dict() for item in exc.results]})
        except Exception as exc:
            _print({"success": False, "error": str(exc)})


def _auto_run(registry: SkillRegistry, deployment_path: str,
              instruction: str | None, max_cycles: int | None) -> int:
    deployment = _deployment(deployment_path)
    objective = instruction or str(deployment.get("auto_instruction", "")).strip()
    if not objective:
        raise ValueError("部署配置缺少 auto_instruction")
    config = AgentConfig.load(deployment["llm_config"])
    planner = HierarchicalSkillPlanner(config, registry)
    jammer_settings = deployment.get("jammer_support") or {}
    jammer_controller = None
    if jammer_settings.get("enabled", False):
        jammer_controller = JammerEscortController(
            script_path=ROOT / "scripts" / "powershell" / "09-电子干扰伴随.ps1",
            run_root=ROOT / "runs" / "jammer_support",
            powershell=deployment.get("powershell_executable"),
            trail_km=float(jammer_settings.get("trail_km", 20)),
            follow_interval_sec=float(jammer_settings.get("follow_interval_sec", 30)),
            launch_retry_sec=float(jammer_settings.get("launch_retry_sec", 30)),
            max_wait_sim_sec=float(jammer_settings.get("max_wait_sim_sec", 1800)),
        )
    runner = AutomaticMissionRunner(
        collector=lambda: collect_live_situation(
            host=deployment["mozi_host"],
            ws_port=int(deployment["mozi_ws_port"]),
            api_base=deployment["mozi_api_base"]),
        planner=planner,
        registry=registry,
        compiler=PlanCompiler(registry, ROOT / "scripts" / "powershell", ROOT / "runs"),
        executor=PowerShellMissionExecutor(executable=deployment.get("powershell_executable")),
        state_path=ROOT / "runs" / "automatic_state.json",
        area_cleaner=OwnedAreaGarbageCollector(ROOT / "runs"),
        poll_interval_sec=float(deployment.get("poll_interval_sec", 10)),
        replan_interval_sec=float(deployment.get("replan_interval_sec", 300)),
        script_parallelism=deployment.get("script_parallelism", 2),
        phase_control_enabled=deployment.get("phase_control_enabled", True),
        jammer_controller=jammer_controller,
        opening_doctrine=lambda situation: set_opening_engagement(
            situation, ROOT / "scripts" / "powershell" / "10-开局蓝方接战规则.ps1",
            deployment.get("powershell_executable")),
    )
    print("当前墨子态势将发送至模型服务：", config.base_url, flush=True)
    try:
        runner.run(objective, max_cycles=max_cycles)
    except KeyboardInterrupt:
        print("\n自动推演已停止。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="墨子 LLM → 任务 Skill → 固定脚本调度")
    sub = parser.add_subparsers(dest="command")

    auto = sub.add_parser("auto-run", help="自动持续读取态势、调用 Agent 和任务脚本")
    auto.add_argument("--deployment-config", default=str(ROOT / "config" / "deployment.json"))
    auto.add_argument("--instruction", help="覆盖部署配置的自动任务目标")
    auto.add_argument("--max-cycles", type=int, help="测试用；默认一直运行")

    interactive = sub.add_parser("interactive", help="交互式规划和创建墨子任务")
    interactive.add_argument("--deployment-config", default=str(ROOT / "config" / "deployment.json"))

    doctor = sub.add_parser("doctor", help="检查本机部署与墨子连接，不调用 LLM 或创建任务")
    doctor.add_argument("--deployment-config", default=str(ROOT / "config" / "deployment.json"))

    sub.add_parser("list-skills", help="列出海陆空当前已注册任务 Skill")
    status = sub.add_parser("agent-status", help="查看五个 Agent 和模型配置，不发起模型请求")
    status.add_argument("--llm-config", default=str(ROOT / "config" / "llm.json"))

    validate = sub.add_parser("validate", help="只做本地规则校验，不调用脚本")
    validate.add_argument("--situation", required=True)
    validate.add_argument("--plan", required=True)

    execute = sub.add_parser("execute", help="执行已有结构化方案")
    execute.add_argument("--situation", required=True)
    execute.add_argument("--plan", required=True)
    execute.add_argument("--dry-run", action="store_true", help="调用脚本 -ValidateOnly，不创建任务")
    execute.add_argument("--run-id")

    plan = sub.add_parser("plan", help="让联合/军种 LLM 形成 Skill 调用方案，不执行")
    plan.add_argument("--situation", required=True)
    plan.add_argument("--llm-config", required=True)
    plan.add_argument("--instruction", required=True)
    plan.add_argument("--output")

    orchestrate = sub.add_parser("orchestrate", help="LLM 规划后校验并调用固定脚本")
    orchestrate.add_argument("--situation", required=True)
    orchestrate.add_argument("--llm-config", required=True)
    orchestrate.add_argument("--instruction", required=True)
    orchestrate.add_argument("--execute", action="store_true", help="省略时只做脚本参数校验")
    orchestrate.add_argument("--run-id")

    live = sub.add_parser("live-orchestrate", help="从当前墨子态势规划并调用固定脚本")
    live.add_argument("--llm-config")
    live.add_argument("--deployment-config", default=str(ROOT / "config" / "deployment.json"))
    live.add_argument("--instruction", required=True)
    live.add_argument("--execute", action="store_true", help="省略时只做脚本参数校验")
    live.add_argument("--host")
    live.add_argument("--rest-port", type=int, default=8082)
    live.add_argument("--ws-port", type=int)
    live.add_argument("--api-base")
    live.add_argument("--run-id")
    return parser


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    arguments = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(arguments if arguments else ["auto-run"])
    registry = SkillRegistry(ROOT / "skills").load()
    try:
        if args.command == "auto-run":
            return _auto_run(registry, args.deployment_config,
                             args.instruction, args.max_cycles)
        if args.command == "interactive":
            return _interactive(registry, args.deployment_config)
        if args.command == "doctor":
            return _doctor(args.deployment_config, registry)
        if args.command == "list-skills":
            _print({owner: registry.public_catalog_for_agent(owner) for owner in AGENT_OWNERS})
            return 0
        if args.command == "agent-status":
            config = AgentConfig.load(args.llm_config)
            _print({
                "agents": ["JointCommanderAgent", "AirCommanderAgent",
                           "AwacsCommanderAgent", "NavyCommanderAgent", "ArmyCommanderAgent"],
                "base_url": config.base_url,
                "joint_model": config.joint_model or config.model,
                "component_model": config.component_model or config.model,
                "api_key_env": config.api_key_env,
                "api_key_present": bool(os.environ.get(config.api_key_env))
                                   or bool(config.api_key_file and
                                           Path(config.api_key_file).is_file()),
                "skills": {service: len(registry.for_service(service))
                           for service in ("air", "navy", "army")},
                "agent_skills": {owner: len(registry.for_agent(owner)) for owner in AGENT_OWNERS},
            })
            return 0
        if args.command == "validate":
            calls = validate_plan(_json(args.plan), _json(args.situation), registry)
            _print({"valid": True, "skill_calls": [item.to_dict() for item in calls]})
            return 0
        if args.command == "execute":
            result = _runtime().execute_plan(_json(args.plan), _json(args.situation),
                                             dry_run=args.dry_run, run_id=args.run_id)
            _print(result)
            return 0
        deployment = _deployment(args.deployment_config) if args.command == "live-orchestrate" else None
        llm_config = args.llm_config or (deployment["llm_config"] if deployment else None)
        planner = HierarchicalSkillPlanner.from_config(llm_config, registry)
        situation = (_json(args.situation) if args.command != "live-orchestrate" else
                     collect_live_situation(
                         host=args.host or deployment["mozi_host"],
                         ws_port=args.ws_port or int(deployment["mozi_ws_port"]),
                         api_base=args.api_base or deployment["mozi_api_base"]))
        if args.command == "plan":
            result = planner.plan(situation, args.instruction)
            validate_plan(result, situation, registry)
            if args.output:
                Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            _print(result)
            return 0
        result = _runtime(planner).plan_and_execute(
            situation, args.instruction, dry_run=not args.execute, run_id=args.run_id
        )
        _print(result)
        return 0
    except ScriptExecutionError as exc:
        _print({"success": False, "error": str(exc),
                "results": [item.to_dict() for item in exc.results]})
        return 2
    except Exception as exc:
        _print({"success": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
