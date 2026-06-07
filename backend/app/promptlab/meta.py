"""Built-in meta-prompt for prompt reverse-inference.

Not exposed to the user — maintained here. Given example input→output pairs (plus
optional free-text requirements and structured constraints), it asks a model to
emit a single system prompt that would reproduce that input→output mapping.
"""
from __future__ import annotations

from app.models_layer.types import ChatMessage

META_SYSTEM = (
    "You are an expert prompt engineer. You are given example input→output pairs "
    "that demonstrate a task an assistant should perform. Infer ONE high-quality "
    "SYSTEM PROMPT that, when given to an assistant, would make it transform such "
    "inputs into outputs like the examples.\n\n"
    "Carefully analyze the hidden task, the assistant's role/domain, the output "
    "style, structure, and any implicit rules shown by the examples. Incorporate "
    "the user's additional requirements and the explicit constraints listed.\n\n"
    "If an example INPUT includes image(s), infer a prompt for a VISION assistant "
    "that produces such outputs when shown such images.\n\n"
    "Output ONLY the system prompt text itself. Do NOT include explanations, "
    "analysis, markdown code fences, headings like 'System prompt:', or any "
    "preamble or sign-off. Write it in second person addressing the assistant "
    "(e.g. 'You are ...')."
)

# Token budget guards (chars, not exact tokens — generous but bounded).
MAX_EXAMPLES = 20
MAX_TOTAL_CHARS = 24000


def build_user_message(examples: list[dict], requirements: str,
                       constraints: list[str]) -> str:
    parts: list[str] = ["Here are example input → output pairs:\n"]
    for i, ex in enumerate(examples, 1):
        inp = str(ex.get("input", "")).strip()
        out = str(ex.get("output", "")).strip()
        parts.append(f"### Example {i}\nINPUT:\n{inp}\n\nOUTPUT:\n{out}\n")
    if constraints:
        parts.append("Constraints the system prompt MUST enforce:")
        parts.extend(f"- {c}" for c in constraints)
        parts.append("")
    req = (requirements or "").strip()
    if req:
        parts.append(f"Additional requirements from the user:\n{req}\n")
    parts.append(
        "Now write the single system prompt that would reproduce this mapping. "
        "Output only the system prompt."
    )
    return "\n".join(parts)


def _example_parts(examples: list[dict]) -> list[dict]:
    """Multimodal user content: interleave text + image_url parts so the model
    can actually see example input images alongside the expected outputs."""
    parts: list[dict] = [{"type": "text", "text": "Here are example input → output pairs:\n"}]
    for i, ex in enumerate(examples, 1):
        inp = str(ex.get("input", "")).strip()
        out = str(ex.get("output", "")).strip()
        imgs = ex.get("images") or []
        if imgs:
            parts.append({"type": "text", "text": f"### Example {i}\nINPUT (image + text):"})
            for url in imgs:
                parts.append({"type": "image_url", "image_url": {"url": url}})
            parts.append({"type": "text", "text": f"{inp}\n\nOUTPUT:\n{out}\n"})
        else:
            parts.append({"type": "text", "text": f"### Example {i}\nINPUT:\n{inp}\n\nOUTPUT:\n{out}\n"})
    return parts


def build_messages(examples: list[dict], requirements: str,
                   constraints: list[str]) -> list[ChatMessage]:
    sys_msg = ChatMessage(role="system", content=META_SYSTEM)
    # Text-only fast path keeps compatibility with non-vision models.
    if not any(ex.get("images") for ex in examples):
        return [sys_msg, ChatMessage(role="user",
                                     content=build_user_message(examples, requirements, constraints))]
    parts = _example_parts(examples)
    if constraints:
        parts.append({"type": "text", "text": "Constraints the system prompt MUST enforce:\n"
                      + "\n".join(f"- {c}" for c in constraints)})
    if (requirements or "").strip():
        parts.append({"type": "text", "text": f"Additional requirements from the user:\n{requirements.strip()}"})
    parts.append({"type": "text", "text": "Now write the single system prompt that would "
                  "reproduce this mapping. Output only the system prompt."})
    return [sys_msg, ChatMessage(role="user", content=parts)]
