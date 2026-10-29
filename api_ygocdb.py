from __future__ import annotations

import asyncio
import json
import random
from typing import Any
from urllib.parse import quote

import aiohttp


class YgoApiError(Exception):
    """ygocdb 请求失败。"""


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def normalize_card(payload: Any, fallback_id: int | None = None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    text = payload.get("text")
    if not isinstance(text, dict):
        text = {}
    raw_id = payload.get("id", fallback_id)
    try:
        card_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    name = (
        payload.get("cn_name")
        or text.get("cn_name")
        or text.get("name")
        or payload.get("sc_name")
        or text.get("sc_name")
        or payload.get("md_name")
        or ""
    )
    if not name:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    return {
        "id": card_id,
        "name": str(name).strip(),
        "types": _clean_text(text.get("types")),
        "desc": _clean_text(text.get("desc") or payload.get("desc")),
        "jp_name": _clean_text(
            text.get("jp_name") or payload.get("jp_name")
        ),
        "en_name": _clean_text(
            text.get("en_name") or payload.get("en_name")
        ),
        "atk": data.get("atk"),
        "def": data.get("def"),
        "level": data.get("level"),
        "race": data.get("race"),
        "attribute": data.get("attribute"),
        "raw": payload,
    }


class YgoClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        retries: int = 2,
        user_agent: str = "AstrBot-YgoPlugin/0.1",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = max(1.0, float(timeout))
        self.retries = max(0, int(retries))
        self.user_agent = user_agent

    async def _get_json(self, url: str, *, retries: int | None = None) -> Any | None:
        attempts = self.retries if retries is None else max(0, int(retries))
        last_error: Exception | None = None
        for attempt in range(attempts + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=self.timeout)
                headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
                async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                    async with session.get(url) as response:
                        if response.status == 404:
                            return None
                        body = await response.text()
                        if response.status >= 400:
                            raise YgoApiError(f"HTTP {response.status}")
                        return json.loads(body)
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < attempts:
                    await asyncio.sleep(0.4 * (attempt + 1))
        raise YgoApiError(f"请求 ygocdb 失败：{last_error}")

    async def card(self, card_id: int, *, retries: int | None = None) -> dict[str, Any] | None:
        payload = await self._get_json(
            f"{self.base_url}/card/{int(card_id)}",
            retries=retries,
        )
        return normalize_card(payload, fallback_id=int(card_id))

    async def search(self, query: str, *, limit: int = 5, retries: int | None = None) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        payload = await self._get_json(
            f"{self.base_url}/?search={quote(query, safe='')}",
            retries=retries,
        )
        if not isinstance(payload, dict):
            return []
        results = payload.get("result")
        if not isinstance(results, list):
            return []
        cards: list[dict[str, Any]] = []
        for item in results[: max(1, int(limit))]:
            card = normalize_card(item)
            if card:
                cards.append(card)
        return cards

    async def random_card(
        self,
        *,
        seed: int,
        recent_ids: set[int] | None = None,
        fallback_terms: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """按日期种子取一张卡；ygocdb 没有随机接口，用卡号范围 + 重试实现。"""
        recent = recent_ids or set()
        rng = random.Random(seed)
        for _ in range(8):
            card_id = rng.randint(1, 20000)
            if card_id in recent:
                continue
            card = await self.card(card_id, retries=0)
            if card and card["id"] not in recent:
                return card

        terms = fallback_terms or [
            "青眼白龙",
            "黑魔导",
            "真红眼黑龙",
            "英雄",
            "龙",
            "魔法师",
        ]
        rng.shuffle(terms)
        for term in terms:
            try:
                cards = await self.search(term, limit=10, retries=1)
            except YgoApiError:
                continue
            cards = [c for c in cards if c["id"] not in recent]
            if cards:
                return rng.choice(cards)
        return None
