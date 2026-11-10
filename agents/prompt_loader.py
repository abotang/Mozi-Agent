"""Load human-editable Agent prompts from the project-root prompts directory."""

from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
ROLES = {"joint", "air", "awacs", "navy", "army"}
SERVICE_PROMPT_CHAR_LIMIT = 2000  # 空、海、陆三个军种；独立预警机不在此限制内。


def load_prompt(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"Unknown Agent prompt: {role}")
    prompt = (PROMPTS / f"{role}.md").read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"Empty Agent prompt: {role}")
    if role in {"air", "navy", "army"} and len(prompt) > SERVICE_PROMPT_CHAR_LIMIT:
        raise ValueError(f"{role} Agent 提示词超过 {SERVICE_PROMPT_CHAR_LIMIT} 字符")
    return prompt
