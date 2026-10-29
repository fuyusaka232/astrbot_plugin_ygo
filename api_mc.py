from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp


class McApiError(Exception):
    """MC 卡组数据请求失败。"""


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_player(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    deck = item.get("deck")
    if not isinstance(deck, dict):
        deck = {}
    categories = deck.get("categories")
    labels: list[str] = []
    if isinstance(categories, list):
        for category in categories:
            if isinstance(category, dict):
                label = category.get("label")
                if label:
                    labels.append(str(label))
    links = deck.get("links")
    if not isinstance(links, list):
        links = []
    return {
        "rank": _as_int(item.get("rank")),
        "player": str(item.get("player") or ""),
        "deck_name": str(deck.get("name") or ""),
        "categories": labels,
        "links": [str(link) for link in links if link],
        "score": item.get("score"),
        "month_games": item.get("month_games"),
        "month_win_rate": item.get("month_win_rate"),
    }


class McClient:
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

    async def _get_json(self, url: str) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=self.timeout)
                headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
                async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                    async with session.get(url) as response:
                        body = await response.text()
                        if response.status >= 400:
                            raise McApiError(f"HTTP {response.status}")
                        return json.loads(body)
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.retries:
                    await asyncio.sleep(0.4 * (attempt + 1))
        raise McApiError(f"请求 MC 数据失败：{last_error}")

    async def leaderboard(self, page: int) -> dict[str, Any]:
        page = max(1, min(10, int(page)))
        payload = await self._get_json(f"{self.base_url}/leaderboard?page={page}")
        if not isinstance(payload, dict):
            raise McApiError("MC 返回格式异常")
        return payload

    async def all_players(self) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(5)

        async def fetch(page: int) -> dict[str, Any]:
            async with semaphore:
                return await self.leaderboard(page)

        results = await asyncio.gather(
            *(fetch(page) for page in range(1, 11)),
            return_exceptions=True,
        )
        players: list[dict[str, Any]] = []
        errors: list[Exception] = []
        for result in results:
            if isinstance(result, Exception):
                errors.append(result)
                continue
            raw_players = result.get("players")
            if not isinstance(raw_players, list):
                continue
            for item in raw_players:
                player = normalize_player(item)
                if player and player["rank"] > 0:
                    players.append(player)
        if errors and not players:
            raise McApiError(f"MC 数据请求失败：{errors[0]}")
        deduped: dict[int, dict[str, Any]] = {}
        for player in players:
            deduped.setdefault(player["rank"], player)
        return [deduped[rank] for rank in sorted(deduped)]

    async def stats(self) -> dict[str, Any]:
        payload = await self._get_json(f"{self.base_url}/deck-stats")
        if not isinstance(payload, dict):
            raise McApiError("MC 类型统计返回格式异常")
        return payload
