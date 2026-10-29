from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp


class AiError(Exception):
    """AI 裁定请求失败。"""


SYSTEM_PROMPT = (
    "你是游戏王 OCG/TCG 裁定助手。回答时要区分："
    "1) 有明确规则、卡文或官方 FAQ 依据的结论；"
    "2) 需要结合具体场面、连锁或判例才能确定的推测。"
    "不要编造 FAQ 编号、判例编号或不存在的规则。"
    "不确定时直接说“不确定”，并给出应该查证的方向（卡文、官方数据库、裁判）。"
    "回答用中文，简洁但把关键步骤说清楚。"
)


def build_prompt(question: str, card_text: str = "") -> str:
    parts = []
    if card_text.strip():
        parts.append("相关卡片信息（来自 ygocdb，仅供理解卡文）：\n" + card_text.strip())
    parts.append("问题：\n" + question.strip())
    return "\n\n".join(parts)


async def ask_astrbot(context: Any, umo: str, prompt: str) -> str:
    provider = context.get_using_provider(umo=umo)
    if provider is None:
        raise AiError("当前会话没有可用的 AstrBot 模型，请先在 AstrBot 配置模型 Provider。")
    try:
        response = await provider.text_chat(
            prompt=prompt,
            contexts=[],
            system_prompt=SYSTEM_PROMPT,
        )
    except Exception as exc:
        raise AiError(f"AstrBot 模型调用失败：{exc}") from exc
    text = getattr(response, "completion_text", None)
    if not text:
        text = getattr(response, "text", None)
    if not text:
        text = str(response or "")
    text = str(text).strip()
    if not text:
        raise AiError("模型返回了空内容。")
    return text


async def ask_openai_compatible(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: float = 60.0,
) -> str:
    if not api_key.strip():
        raise AiError("未配置 AI API Key。请在插件配置的 ai.api_key 中填写，或把 ai.mode 改成 astrbot。")
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 900,
    }
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }
    try:
        timeout_obj = aiohttp.ClientTimeout(total=max(5.0, float(timeout)))
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.post(url, json=payload, headers=headers) as response:
                body = await response.text()
                if response.status >= 400:
                    raise AiError(f"AI 接口 HTTP {response.status}：{body[:200]}")
                data = json.loads(body)
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
        raise AiError(f"AI 接口请求失败：{exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AiError("AI 接口返回格式异常。") from exc
    content = str(content or "").strip()
    if not content:
        raise AiError("AI 返回了空内容。")
    return content
