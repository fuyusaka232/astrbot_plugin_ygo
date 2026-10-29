from __future__ import annotations

from typing import Any


def format_card(card: dict[str, Any], *, max_chars: int = 1200, title: str | None = None) -> str:
    name = str(card.get("name") or "未知卡片")
    card_id = card.get("id")
    alias_parts = []
    for key in ("jp_name", "en_name"):
        value = str(card.get(key) or "").strip()
        if value and value != name:
            alias_parts.append(value)

    lines = [f"【{title}】{name}" if title else f"【{name}】"]
    if alias_parts:
        lines.append(" / ".join(alias_parts))
    types = str(card.get("types") or "").strip()
    if types:
        lines.append(types)
    desc = str(card.get("desc") or "").strip()
    if desc:
        lines.append(desc)

    footer = []
    if card_id:
        footer.append(f"卡号：{card_id}")
        footer.append(f"https://ygocdb.com/card/{card_id}")
    text = "\n".join(lines + footer)
    if len(text) > max_chars:
        keep = max(100, max_chars - len("\n".join(lines[:2] + footer)) - 4)
        desc = desc[:keep].rstrip() + "…"
        lines = [f"【{title}】{name}" if title else f"【{name}】"]
        if alias_parts:
            lines.append(" / ".join(alias_parts))
        if types:
            lines.append(types)
        if desc:
            lines.append(desc)
        text = "\n".join(lines + footer)
    return text


def format_card_candidates(cards: list[dict[str, Any]], query: str) -> str:
    lines = [f"找到 {len(cards)} 张与「{query}」相关的卡："]
    for card in cards:
        types = str(card.get("types") or "").split("\n")[0]
        suffix = f"｜{types}" if types else ""
        lines.append(f"{card.get('id')} · {card.get('name')}{suffix}")
    lines.append("用 /ygo card <卡号> 查看完整效果。")
    return "\n".join(lines)


def format_mc_page(players: list[dict[str, Any]], page: int) -> str:
    page = max(1, min(10, page))
    start = (page - 1) * 10 + 1
    end = start + 9
    lines = [f"MC 前 100 · 第 {page} 页（{start}-{end}）"]
    for player in players[:10]:
        categories = " / ".join(player.get("categories") or [])
        line = f"#{player.get('rank')} {player.get('player') or '?'} · {player.get('deck_name') or '未知卡组'}"
        if categories:
            line += f"｜{categories}"
        lines.append(line)
    lines.append("查卡表链接：/ygo mc link <排名>；按类型筛选：/ygo mc <关键词>")
    return "\n".join(lines)


def format_mc_matches(matches: list[dict[str, Any]], keyword: str) -> str:
    if not matches:
        return f"前 100 里没有匹配「{keyword}」的卡组。"
    lines = [f"前 100 中匹配「{keyword}」的卡组：{len(matches)} 个"]
    for player in matches[:5]:
        categories = " / ".join(player.get("categories") or [])
        line = f"#{player.get('rank')} {player.get('player') or '?'} · {player.get('deck_name') or '未知卡组'}"
        if categories:
            line += f"｜{categories}"
        lines.append(line)
        links = player.get("links") or []
        if links:
            lines.append(str(links[0]))
    return "\n".join(lines)


def format_mc_link(player: dict[str, Any] | None, rank: int) -> str:
    if not player:
        return f"前 100 里没有排名 #{rank} 的卡组。"
    links = player.get("links") or []
    if not links:
        return f"#{rank} {player.get('player') or '?'} · {player.get('deck_name') or '未知卡组'}\n没有可用的卡表链接。"
    return (
        f"#{rank} {player.get('player') or '?'} · {player.get('deck_name') or '未知卡组'}\n"
        f"{links[0]}"
    )


def format_mc_stats(payload: dict[str, Any]) -> str:
    series = payload.get("series")
    if not isinstance(series, list) or not series:
        return "MC 类型统计暂时为空。"
    lines = ["MC 前 100 类型分布"]
    for item in series[:10]:
        if not isinstance(item, dict):
            continue
        label = item.get("label") or "未知"
        count = item.get("count") or 0
        highest = item.get("highest_rank")
        suffix = f"，最高 #{highest}" if highest else ""
        lines.append(f"{label}：{count} 套{suffix}")
    updated = payload.get("updated_at")
    if updated:
        lines.append(f"更新时间：{updated}")
    return "\n".join(lines)
