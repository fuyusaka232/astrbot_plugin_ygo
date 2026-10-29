from __future__ import annotations

import datetime as dt
import math
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.filter.command import GreedyStr

from .ai_ruling import AiError, ask_astrbot, ask_openai_compatible, build_prompt
from .api_mc import McApiError, McClient
from .api_ygocdb import YgoApiError, YgoClient
from .formatter import (
    format_card,
    format_card_candidates,
    format_mc_link,
    format_mc_matches,
    format_mc_page,
    format_mc_stats,
)
from .state_store import StateStore

PLUGIN_NAME = "astrbot_plugin_ygo"


@register(
    PLUGIN_NAME,
    "DSH",
    "游戏王卡牌查询、MC 前 100 卡组、每日一卡与裁定问答。",
    "0.1.0",
)
class YgoPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.name = PLUGIN_NAME

        timeout = self._float_conf("api.timeout_seconds", 10.0)
        retries = self._int_conf("api.retries", 2)
        self.ygo = YgoClient(
            self._str_conf("api.ygocdb_base", "https://ygocdb.com/api/v0"),
            timeout=timeout,
            retries=retries,
        )
        self.mc = McClient(
            self._str_conf("api.mc_base", "https://ygo.xyk.one/mc/api"),
            timeout=timeout,
            retries=retries,
        )

        data_dir = Path(__file__).resolve().parent.parents[1] / "plugin_data" / PLUGIN_NAME
        self.state = StateStore(data_dir / "state.json")
        self._seed_targets_from_config()

        self._card_cache: dict[str, tuple[float, Any]] = {}
        self._mc_players_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._mc_stats_cache: tuple[float, dict[str, Any]] | None = None
        self._daily_cache: dict[str, dict[str, Any]] = {}
        self._cooldowns: dict[tuple[str, str], float] = {}

    async def terminate(self) -> None:
        logger.info("游戏王助手插件已停止。")

    # ---------- 配置 ----------

    def _conf(self, path: str, default: Any = None) -> Any:
        current: Any = self.config
        for part in path.split("."):
            if current is None:
                return default
            if isinstance(current, dict):
                current = current.get(part)
                continue
            getter = getattr(current, "get", None)
            if callable(getter):
                current = getter(part)
                continue
            return default
        return default if current is None else current

    def _str_conf(self, path: str, default: str) -> str:
        value = self._conf(path, default)
        return str(value).strip() if value is not None else default

    def _int_conf(self, path: str, default: int) -> int:
        try:
            return int(self._conf(path, default))
        except (TypeError, ValueError):
            return default

    def _float_conf(self, path: str, default: float) -> float:
        try:
            return float(self._conf(path, default))
        except (TypeError, ValueError):
            return default

    def _seed_targets_from_config(self) -> None:
        targets = self._conf("daily.targets", [])
        if not isinstance(targets, list):
            return
        existing = set(self.state.targets())
        for target in targets:
            target = str(target or "").strip()
            if target and target not in existing:
                self.state.add_target(target)
                existing.add(target)

    # ---------- 入口 ----------

    @filter.command_group(
        "ygo",
        alias={"游戏王"},
        desc="游戏王卡查、MC 前 100 卡组、每日一卡与裁定问答。",
    )
    def ygo_group(self):
        pass

    @ygo_group.command(
        "card",
        alias={"查卡", "卡"},
        desc="按卡名或卡号查询卡片效果。",
    )
    async def ygo_card(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._safe(
            event, self._cmd_card(event, str(args or "").strip())
        ):
            yield result

    @ygo_group.command(
        "mc",
        alias={"卡组", "环境"},
        desc="查询 MC 前 100 卡组、类型分布或卡表链接。",
    )
    async def ygo_mc(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._safe(
            event, self._cmd_mc(event, str(args or "").strip())
        ):
            yield result

    @ygo_group.command(
        "daily",
        alias={"每日", "每日一卡"},
        desc="查看或手动推送每日一卡。",
    )
    async def ygo_daily(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._safe(
            event, self._cmd_daily(event, str(args or "").strip())
        ):
            yield result

    @ygo_group.command(
        "ruling",
        alias={"裁定", "规则"},
        desc="AI 卡片裁定问答，可用「卡名 :: 问题」附带卡文。",
    )
    async def ygo_ruling(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._safe(
            event, self._cmd_ruling(event, str(args or "").strip())
        ):
            yield result

    @ygo_group.command(
        "help",
        alias={"帮助", "用法"},
        desc="查看游戏王助手用法。",
    )
    async def ygo_help(self, event: AstrMessageEvent):
        yield event.plain_result(self._usage())

    async def _safe(self, event: AstrMessageEvent, generator):
        try:
            async for result in generator:
                yield result
        except Exception as exc:
            logger.exception("游戏王插件处理失败")
            yield event.plain_result(f"处理失败：{exc}")

    @staticmethod
    def _usage() -> str:
        return (
            "游戏王助手\n"
            "/ygo card <卡名或卡号>  查卡\n"
            "/ygo mc [页码|关键词|stats|link <排名>]  MC 前 100 卡组\n"
            "/ygo daily [now|target|target clear|targets|push]  每日一卡\n"
            "/ygo ruling [卡名 :: ]<问题>  卡片裁定问答\n"
            "/ygo help  帮助\n"
            "别名：/游戏王\n"
            "示例：/ygo card 青眼白龙；/ygo card 89631139；/ygo mc 城之内；/ygo ruling 青眼白龙 :: 这张卡被效果破坏时能发动吗"
        )

    # ---------- 工具 ----------

    def _cooldown(self, event: AstrMessageEvent, key: str, seconds: int) -> str | None:
        seconds = max(0, int(seconds))
        if seconds <= 0:
            return None
        uid = str(event.get_sender_id() or event.unified_msg_origin)
        now = time.monotonic()
        last = self._cooldowns.get((key, uid), 0.0)
        if now - last < seconds:
            wait = max(1, math.ceil(seconds - (now - last)))
            return f"操作太快，请 {wait} 秒后再试。"
        self._cooldowns[(key, uid)] = now
        return None

    def _cache_get(self, key: str, ttl: int) -> Any | None:
        item = self._card_cache.get(key)
        if not item:
            return None
        timestamp, value = item
        if time.time() - timestamp > max(1, int(ttl)):
            self._card_cache.pop(key, None)
            return None
        return value

    def _cache_set(self, key: str, value: Any) -> None:
        if len(self._card_cache) > 300:
            self._card_cache.clear()
        self._card_cache[key] = (time.time(), value)

    async def _get_mc_players(self) -> list[dict[str, Any]]:
        ttl = self._int_conf("api.mc_cache_ttl_seconds", 600)
        if self._mc_players_cache and time.time() - self._mc_players_cache[0] <= ttl:
            return self._mc_players_cache[1]
        players = await self.mc.all_players()
        if not players:
            raise McApiError("MC 没有返回卡组数据。")
        self._mc_players_cache = (time.time(), players)
        return players

    async def _get_mc_stats(self) -> dict[str, Any]:
        ttl = self._int_conf("api.mc_cache_ttl_seconds", 600)
        if self._mc_stats_cache and time.time() - self._mc_stats_cache[0] <= ttl:
            return self._mc_stats_cache[1]
        payload = await self.mc.stats()
        self._mc_stats_cache = (time.time(), payload)
        return payload

    async def _daily_card(self) -> dict[str, Any]:
        timezone_name = self._str_conf("daily.timezone", "Asia/Shanghai")
        try:
            timezone = ZoneInfo(timezone_name)
        except Exception:
            timezone = dt.timezone.utc
        today = dt.datetime.now(timezone).date().isoformat()
        if today in self._daily_cache:
            return self._daily_cache[today]
        seed = int(today.replace("-", ""))
        avoid_days = self._int_conf("daily.avoid_recent_days", 7)
        card = await self.ygo.random_card(
            seed=seed,
            recent_ids=self.state.recent_ids(avoid_days),
        )
        if not card:
            raise YgoApiError("今天没有抽到可用的卡牌，请稍后重试。")
        self._daily_cache[today] = card
        self.state.record_daily(card["id"], card["name"], today)
        return card

    async def _send_to(self, umo: str, text: str) -> tuple[bool, str]:
        try:
            chain = MessageChain().message(text)
            ok = await self.context.send_message(umo, chain)
        except Exception as exc:
            logger.warning("发送到 %s 失败: %s", umo, exc)
            return False, str(exc)
        if not ok:
            return False, "没有找到匹配的平台会话"
        return True, ""

    # ---------- 卡查 ----------

    async def _cmd_card(self, event: AstrMessageEvent, query: str):
        if not query:
            yield event.plain_result("用法：/ygo card <卡名或卡号>")
            return
        if len(query) > 100:
            yield event.plain_result("查询内容太长，请控制在 100 字符以内。")
            return
        wait = self._cooldown(event, "card", self._int_conf("limits.cooldown_seconds", 1))
        if wait:
            yield event.plain_result(wait)
            return

        cache_key = f"card:{query.casefold()}"
        cached = self._cache_get(cache_key, self._int_conf("api.cache_ttl_seconds", 21600))
        if cached is not None:
            if isinstance(cached, list):
                yield event.plain_result(format_card_candidates(cached, query))
            else:
                yield event.plain_result(
                    format_card(cached, max_chars=self._int_conf("daily.max_chars", 1200))
                )
            return

        try:
            if query.isdigit() and len(query) <= 10:
                card = await self.ygo.card(int(query))
                if not card:
                    yield event.plain_result(f"没有找到卡号 {query}，请确认卡号。")
                    return
                self._cache_set(cache_key, card)
                yield event.plain_result(
                    format_card(card, max_chars=self._int_conf("daily.max_chars", 1200))
                )
                return

            limit = max(1, self._int_conf("limits.max_search_results", 5))
            cards = await self.ygo.search(query, limit=limit)
            if not cards:
                yield event.plain_result(
                    f"没有找到「{query}」。试试日文名、英文名或卡号。"
                )
                return
            self._cache_set(cache_key, cards)
            exact = next(
                (
                    card
                    for card in cards
                    if card["name"].casefold() == query.casefold()
                    or str(card.get("jp_name", "")).casefold() == query.casefold()
                ),
                None,
            )
            if exact:
                yield event.plain_result(
                    format_card(exact, max_chars=self._int_conf("daily.max_chars", 1200))
                )
            else:
                yield event.plain_result(format_card_candidates(cards, query))
        except YgoApiError as exc:
            logger.warning("卡查失败: %s", exc)
            yield event.plain_result("卡牌数据库暂时不可用，请稍后再试。")

    # ---------- MC ----------

    async def _cmd_mc(self, event: AstrMessageEvent, rest: str):
        wait = self._cooldown(event, "mc", self._int_conf("limits.cooldown_seconds", 1))
        if wait:
            yield event.plain_result(wait)
            return
        keyword = rest.strip()
        try:
            if keyword.lower() in {"stats", "统计", "类型"}:
                payload = await self._get_mc_stats()
                yield event.plain_result(format_mc_stats(payload))
                return

            if keyword.lower().startswith("link "):
                parts = keyword.split(maxsplit=1)
                raw_rank = parts[1].strip().lstrip("#") if len(parts) > 1 else ""
                if not raw_rank.isdigit():
                    yield event.plain_result("用法：/ygo mc link <排名>")
                    return
                rank = int(raw_rank)
                players = await self._get_mc_players()
                player = next((item for item in players if item["rank"] == rank), None)
                yield event.plain_result(format_mc_link(player, rank))
                return

            if keyword.isdigit():
                page = int(keyword)
                if page < 1 or page > 10:
                    yield event.plain_result("页码只能是 1-10。")
                    return
                players = await self._get_mc_players()
                page_players = [
                    item for item in players if (page - 1) * 10 < item["rank"] <= page * 10
                ]
                yield event.plain_result(format_mc_page(page_players, page))
                return

            players = await self._get_mc_players()
            if not keyword:
                yield event.plain_result(format_mc_page(players, 1))
                return
            if len(keyword) > 50:
                yield event.plain_result("关键词太长，请控制在 50 字符以内。")
                return
            lowered = keyword.casefold()
            matches = [
                item
                for item in players
                if lowered in str(item.get("deck_name") or "").casefold()
                or any(lowered in str(label).casefold() for label in item.get("categories") or [])
            ]
            yield event.plain_result(format_mc_matches(matches, keyword))
        except McApiError as exc:
            logger.warning("MC 查询失败: %s", exc)
            yield event.plain_result("MC 卡组数据暂时不可用，请稍后再试。")

    # ---------- 每日一卡 ----------

    async def _cmd_daily(self, event: AstrMessageEvent, rest: str):
        sub = rest.strip().lower()
        if sub in {"", "show", "查看"}:
            try:
                card = await self._daily_card()
            except YgoApiError as exc:
                logger.warning("每日一卡失败: %s", exc)
                yield event.plain_result("今天没有抽到卡牌，请稍后再试。")
                return
            yield event.plain_result(
                format_card(card, max_chars=self._int_conf("daily.max_chars", 1200), title="每日一卡")
            )
            return

        if sub in {"now", "立即", "推送"}:
            wait = self._cooldown(event, "daily_now", 3)
            if wait:
                yield event.plain_result(wait)
                return
            try:
                card = await self._daily_card()
            except YgoApiError as exc:
                logger.warning("每日一卡失败: %s", exc)
                yield event.plain_result("今天没有抽到卡牌，请稍后再试。")
                return
            text = format_card(
                card, max_chars=self._int_conf("daily.max_chars", 1200), title="每日一卡"
            )
            ok, error = await self._send_to(event.unified_msg_origin, text)
            yield event.plain_result("已推送今天的每日一卡。" if ok else f"推送失败：{error}")
            return

        if sub in {"target", "设置目标"}:
            if not event.is_admin():
                yield event.plain_result("只有管理员可以设置每日推送目标。")
                return
            added = self.state.add_target(event.unified_msg_origin)
            if added:
                yield event.plain_result(f"已把当前会话设为每日推送目标：{event.unified_msg_origin}")
            else:
                yield event.plain_result("当前会话已经在推送目标里。")
            return

        if sub in {"target clear", "清除目标", "clear"}:
            if not event.is_admin():
                yield event.plain_result("只有管理员可以清除每日推送目标。")
                return
            self.state.clear_targets()
            yield event.plain_result("已清空每日推送目标。")
            return

        if sub in {"targets", "目标"}:
            targets = self.state.targets()
            if not targets:
                yield event.plain_result("还没有设置每日推送目标。用 /ygo daily target 添加当前会话。")
            else:
                yield event.plain_result("每日推送目标：\n" + "\n".join(targets))
            return

        if sub in {"push", "推送目标"}:
            if not event.is_admin():
                yield event.plain_result("只有管理员可以推送到目标会话。")
                return
            targets = self.state.targets()
            if not targets:
                yield event.plain_result("还没有设置每日推送目标。用 /ygo daily target 添加。")
                return
            try:
                card = await self._daily_card()
            except YgoApiError as exc:
                logger.warning("每日一卡失败: %s", exc)
                yield event.plain_result("今天没有抽到卡牌，请稍后再试。")
                return
            text = format_card(
                card, max_chars=self._int_conf("daily.max_chars", 1200), title="每日一卡"
            )
            success = 0
            failed = []
            for target in targets:
                ok, error = await self._send_to(target, text)
                if ok:
                    success += 1
                else:
                    failed.append(f"{target}（{error}）")
            message = f"已推送到 {success}/{len(targets)} 个目标。"
            if failed:
                message += "\n失败：\n" + "\n".join(failed)
            yield event.plain_result(message)
            return

        yield event.plain_result(
            "用法：/ygo daily [now|target|target clear|targets|push]"
        )

    # ---------- 裁定 ----------

    async def _cmd_ruling(self, event: AstrMessageEvent, rest: str):
        if not rest:
            yield event.plain_result(
                "用法：/ygo ruling [卡名 :: ]<问题>\n"
                "示例：/ygo ruling 青眼白龙 :: 这张卡被效果破坏时能发动吗"
            )
            return
        wait = self._cooldown(
            event, "ruling", self._int_conf("limits.ruling_cooldown_seconds", 10)
        )
        if wait:
            yield event.plain_result(wait)
            return

        card = None
        card_query = ""
        question = rest.strip()
        if "::" in rest:
            card_query, question = rest.split("::", 1)
            card_query = card_query.strip()
            question = question.strip()

        max_question = self._int_conf("ai.max_question_chars", 2000)
        if not question:
            yield event.plain_result("问题不能为空。")
            return
        if len(question) > max_question:
            yield event.plain_result(f"问题太长，请控制在 {max_question} 字符以内。")
            return

        if card_query:
            try:
                card = await self.ygo.card(int(card_query)) if card_query.isdigit() else None
                if card is None:
                    cards = await self.ygo.search(card_query, limit=1)
                    card = cards[0] if cards else None
            except YgoApiError:
                card = None

        card_text = ""
        if card:
            card_text = format_card(
                card,
                max_chars=min(900, self._int_conf("daily.max_chars", 1200)),
                title="卡片信息",
            )
            prompt = build_prompt(question, card_text)
        else:
            prompt = build_prompt(question)
            if card_query:
                prompt += f"\n\n（没有找到「{card_query}」的卡文，请按通用裁定回答并注明不确定。）"

        mode = self._str_conf("ai.mode", "astrbot").lower()
        try:
            if mode == "openai":
                answer = await ask_openai_compatible(
                    base_url=self._str_conf("ai.base_url", "https://api.deepseek.com/v1"),
                    api_key=self._str_conf("ai.api_key", ""),
                    model=self._str_conf("ai.model", "deepseek-chat"),
                    prompt=prompt,
                    timeout=self._float_conf("ai.timeout_seconds", 60.0),
                )
            else:
                answer = await ask_astrbot(self.context, event.unified_msg_origin, prompt)
        except AiError as exc:
            yield event.plain_result(str(exc))
            return

        prefix = "【裁定】"
        if card:
            prefix += f"（参考卡：{card['name']}）"
        yield event.plain_result(f"{prefix}\n{answer}")
