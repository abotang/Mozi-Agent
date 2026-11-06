"""Load human-editable Agent prompts from the project-root prompts directory."""

from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
ROLES = {"joint", "air", "awacs", "navy", "army"}


def load_prompt(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"Unknown Agent prompt: {role}")
    prompt = (PROMPTS / f"{role}.md").read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"Empty Agent prompt: {role}")
    return prompt
