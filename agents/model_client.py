"""外部服务与本地部署共用的 Chat Completions HTTP 适配器。"""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from agents.monitor_log import AgentTraceWriter


class ModelError(RuntimeError):
    """模型不可用或返回的内容不符合决策协议。"""


def parse_object(content: str) -> dict:
    if not isinstance(content, str) or len(content) > 262144:
        raise ModelError("模型决策必须为文本且不超过 256 KiB")
    text = content.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    elif text.startswith("```\n") and text.endswith("```"):
        text = text[4:-3].strip()
    def reject_constant(value):
        raise ValueError(f"JSON 不允许 {value}")
    try:
        value = json.loads(text, parse_constant=reject_constant)
    except (ValueError, RecursionError) as exc:
        raise ModelError("模型没有返回有效的 JSON 对象") from exc
    if not isinstance(value, dict):
        raise ModelError("模型决策必须为 JSON 对象")
    return value


@dataclass(frozen=True)
class AgentConfig:
    base_url: str
    model: str
    objective: str
    api_key_env: str = "MOZI_LLM_API_KEY"
    timeout_sec: float = 30.0
    # 关键事件之间的防抖冷却；自动推演轮询由运行层负责。
    event_cooldown_sec: float = 15.0
    max_age_sec: float = 90.0
    api_key_file: str | None = None
    thinking: str | None = None
    enable_thinking: bool | None = None
    joint_model: str | None = None
    component_model: str | None = None
    joint_max_tokens: int = 4096
    component_max_tokens: int = 8192
    joint_interval_sec: float = 300.0
    component_parallelism: int = 3
    log_dir: str | None = None

    def __post_init__(self):
        if not isinstance(self.base_url, str):
            raise ValueError("base_url 必须为 HTTP 地址")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url 必须为不含凭据、查询和片段的 HTTP 地址")
        for name in ("model", "objective", "api_key_env"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} 必须为非空字符串")
        for name in ("timeout_sec", "event_cooldown_sec", "max_age_sec"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} 必须为有限正数")
        if self.api_key_file is not None and (not isinstance(self.api_key_file, str) or not self.api_key_file.strip()):
            raise ValueError("api_key_file 必须为非空文件路径")
        if self.thinking is not None and self.thinking not in ("enabled", "disabled"):
            raise ValueError("thinking 只能为 enabled 或 disabled")
        if self.enable_thinking is not None and type(self.enable_thinking) is not bool:
            raise ValueError("enable_thinking 必须为布尔值")
        for name in ("joint_model", "component_model"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} 必须为非空模型名")
        for name in ("joint_max_tokens", "component_max_tokens"):
            value = getattr(self, name)
            if type(value) is not int or not 128 <= value <= 32768:
                raise ValueError(f"{name} 须在 128～32768")
        if (isinstance(self.joint_interval_sec, bool)
                or not isinstance(self.joint_interval_sec, (int, float))
                or not math.isfinite(self.joint_interval_sec)
                or self.joint_interval_sec <= 0):
            raise ValueError("joint_interval_sec 必须为有限正数")
        if type(self.component_parallelism) is not int or not 1 <= self.component_parallelism <= 4:
            raise ValueError("component_parallelism 须在 1～4")
        if self.log_dir is not None and (not isinstance(self.log_dir, str) or not self.log_dir.strip()):
            raise ValueError("log_dir 必须为非空目录路径")

    @classmethod
    def load(cls, path: str | Path):
        config_path = Path(path).resolve()
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Agent config必须是对象")
        # 兼容旧配置；interval_sec 从未再作为周期触发器，只映射为事件防抖冷却。
        if "interval_sec" in data:
            if "event_cooldown_sec" in data:
                raise ValueError("Agent config不能同时包含 interval_sec 和 event_cooldown_sec")
            data["event_cooldown_sec"] = data.pop("interval_sec")
        if isinstance(data.get("api_key_file"), str):
            data["api_key_file"] = str((config_path.parent / data["api_key_file"]).resolve())
        if isinstance(data.get("log_dir"), str):
            data["log_dir"] = str((config_path.parent / data["log_dir"]).resolve())
        try:
            return cls(**data)
        except TypeError as exc:
            raise ValueError("Agent config字段缺失或含未知字段") from exc


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ChatCompletionClient:
    def __init__(self, config: AgentConfig):
        self.config = config
        self._usage_lock = threading.RLock()
        self._usage = {}
        self._diagnostic_lock = threading.RLock()
        self._diagnostics = {}
        self._trace = AgentTraceWriter(config.log_dir)

    def trace_event(self, role: str, event: str, **details):
        """记录规则跳过与联合方案缓存等未发起模型请求的决策。"""
        self._trace.write({"agent": role, "event": event, **details})

    def set_trace_scope(self, **values):
        """在自动推演中标记想定、轮次；新进程自动获得新的监控场次。"""
        self._trace.set_scope(**values)

    def clear_diagnostics(self):
        """开始新一轮前清空模型痕迹，避免把上一轮输出误记到本轮。"""
        with self._diagnostic_lock:
            self._diagnostics = {}

    def diagnostics(self):
        """返回可审计的提示词指纹与模型 JSON；绝不包含密钥或请求头。"""
        with self._diagnostic_lock:
            return deepcopy(self._diagnostics)

    def load_api_key(self) -> str | None:
        """环境变量优先；本机文件只保存一行密钥，不进入提示词或运行状态。"""
        key = os.environ.get(self.config.api_key_env)
        if not key and self.config.api_key_file:
            try:
                key = Path(self.config.api_key_file).read_text(encoding="utf-8-sig").strip()
            except (OSError, UnicodeError):
                raise ModelError("无法读取本机模型密钥文件") from None
            if not key:
                raise ModelError("本机模型密钥文件为空")
        if key and (not key.strip() or any(char.isspace() for char in key)):
            raise ModelError("模型密钥含空白字符，无法使用")
        return key

    def plan(self, role: str, system: str, context: dict) -> dict:
        """多层指挥 Agent 的结构化调用；role 同时隔离用量与缓存统计。"""
        allowed = {"joint", "component_air", "component_awacs", "component_navy", "component_army",
                   "memory_compressor"}
        if role not in allowed:
            raise ValueError("未知的多层指挥 Agent 角色")
        joint = role == "joint"
        model = (self.config.joint_model if joint else self.config.component_model)
        selected_model = model or self.config.model
        call_id = uuid4().hex
        started = time.monotonic()
        self.trace_event(role, "input", call_id=call_id, model=selected_model,
                         input={"system": system,
                                "context": deepcopy(self._cache_ordered_context(context))})
        raw = None
        try:
            raw = self._complete(
                system, context, json_output=True,
                model=selected_model,
                max_tokens=(self.config.joint_max_tokens if joint
                            else self.config.component_max_tokens),
                usage_key=role)
            result = parse_object(raw)
        except Exception as exc:
            self.trace_event(role, "error", call_id=call_id, model=selected_model,
                             duration_sec=round(time.monotonic() - started, 3),
                             raw_output=raw,
                             error={"type": type(exc).__name__, "message": str(exc)})
            raise
        with self._usage_lock:
            usage = deepcopy(self._usage.get(role))
        self.trace_event(role, "output", call_id=call_id, model=selected_model,
                         duration_sec=round(time.monotonic() - started, 3),
                         raw_output=raw, output=deepcopy(result), usage=usage)
        serialized_context = json.dumps(self._cache_ordered_context(context), ensure_ascii=False,
                                        allow_nan=False, separators=(",", ":"), sort_keys=True)
        context_key_chars = {
            key: len(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                separators=(",", ":"), sort_keys=True))
            for key, value in self._cache_ordered_context(context).items()
        }
        with self._diagnostic_lock:
            self._diagnostics[role] = {
                "model": selected_model,
                "prompt_sha256": hashlib.sha256(system.encode("utf-8")).hexdigest(),
                "prompt_chars": len(system),
                "context_sha256": hashlib.sha256(serialized_context.encode("utf-8")).hexdigest(),
                "context_chars": len(serialized_context),
                "context_key_chars": context_key_chars,
                "output": deepcopy(result),
            }
        return result

    def pop_usage(self, key: str):
        with self._usage_lock:
            return deepcopy(self._usage.pop(key, None))

    @staticmethod
    def _normalized_usage(envelope):
        if not isinstance(envelope, dict):
            return None
        usage = envelope.get("usage")
        if not isinstance(usage, dict):
            return None
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        if not isinstance(details, dict):
            details = {}
        result = {
            "prompt_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
            "completion_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
            "total_tokens": usage.get("total_tokens"),
            "cached_tokens": details.get("cached_tokens", details.get("cache_read_input_tokens", 0)),
        }
        return {key: value for key, value in result.items()
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0}

    @staticmethod
    def _cache_ordered_context(context):
        """固定资料在前、动态态势在后，使公共前缀更长且序列化稳定。"""
        static_keys = ("objective", "task_skills", "available_task_skills",
                       "profiles", "task_catalog", "validation_constraints", "allocation_policy")
        ordered = {key: context[key] for key in static_keys if key in context}
        ordered.update({key: value for key, value in context.items() if key not in ordered})
        return ordered

    def _complete(self, system: str, context: dict, *, json_output: bool, model: str,
                  max_tokens: int, usage_key: str) -> str:
        config = self.config
        headers = {"Content-Type": "application/json"}
        key = self.load_api_key()
        if key:
            headers["Authorization"] = "Bearer " + key
        payload = {"model": model, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(self._cache_ordered_context(context),
                                                     ensure_ascii=False, allow_nan=False,
                                                     separators=(",", ":"))},
        ], "max_tokens": max_tokens}
        if json_output:
            payload["response_format"] = {"type": "json_object"}
        if config.thinking is not None:
            payload["thinking"] = {"type": config.thinking}
        if config.enable_thinking is not None:
            payload["enable_thinking"] = config.enable_thinking
        request = Request(config.base_url.rstrip("/") + "/chat/completions",
                          data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers=headers, method="POST")
        try:
            with build_opener(_NoRedirect()).open(request, timeout=config.timeout_sec) as response:
                body = response.read(1048577)
                if len(body) > 1048576:
                    raise ModelError("模型 HTTP 应答超过 1 MiB")
            envelope = json.loads(body)
            if not isinstance(envelope, dict):
                raise ModelError("模型应答顶层必须是 JSON 对象")
            usage = self._normalized_usage(envelope)
            if usage is not None:
                with self._usage_lock:
                    self._usage[usage_key] = usage
            choices = envelope.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ModelError("模型应答 choices 必须是非空对象数组")
            choice = choices[0]
            if choice.get("finish_reason") not in {None, "stop"}:
                raise ModelError("模型决策未完整生成")
            message = choice.get("message")
            if not isinstance(message, dict):
                raise ModelError("模型应答 message 必须是对象")
            content = message.get("content")
            if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > 262144:
                raise ModelError("模型output必须为非空文本且不超过 256 KiB")
            return content.strip()
        except HTTPError as exc:
            # 不把服务应答、请求头或 API key 带入运行日志。
            raise ModelError(f"模型 HTTP 请求失败，状态码 {exc.code}") from None
        except (URLError, TimeoutError, OSError) as exc:
            raise ModelError("模型连接失败或超时") from None
        except (KeyError, IndexError, TypeError, ValueError, UnicodeError) as exc:
            raise ModelError("模型应答不符合 Chat Completions 协议") from None
