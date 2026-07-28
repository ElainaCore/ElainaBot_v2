# -*- coding: utf-8 -*-
"""异兽迷城 · 基于 ElainaBot_v2 的文字冒险游戏插件"""
import json
import os
import random

from aiohttp import web

from core.plugin.decorators import handler, on_load, on_unload
from core.plugin.web_pages import register_page, register_route, unregister_page

from .story import CHAPTER_EVENTS, CHAPTERS
from .game import (
    STATS, STAT_LABELS, DEFAULT_STATS, init_stats,
    alloc_luck_points, luck_step, check, fight, max_hp,
    get_config, set_config, reset_config, CONFIG_DEFAULTS, CONFIG_LABELS, CONFIG_KEYS_ORDER,
)
from .character import (
    CREATION_FREE_POINTS, WELCOME_MESSAGE, parse_allocation,
    apply_allocation, create_default_character, format_creation_result,
)
from .quest import (
    chapter_quest_id, start_quest, complete_quest,
    get_active_quests, format_quest_list,
)
from .exploration import explore, process_explore_choice, get_explore_pool
from .tutorial import get_tutorial_page, get_tutorial_index

__plugin_meta__ = {
    "name": "异兽迷城",
    "version": "0.1.0",
    "description": "基于《异兽迷城》小说的生存悬疑文字冒险游戏 · 第一部「十二生肖」",
    "author": "MonkeyCode AI",
}

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_PAGE_KEY = "beast-city-admin"
_API = "/api/ext/beast-city"

# ===================== 数据持久化 =====================
_DATA_DIR = os.path.join(_PLUGIN_DIR, "data")
os.makedirs(_DATA_DIR, exist_ok=True)


def _user_dir(uid):
    d = os.path.join(_DATA_DIR, uid)
    os.makedirs(d, exist_ok=True)
    return d


def _load_json(uid, filename):
    p = os.path.join(_user_dir(uid), filename)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _save_json(uid, filename, data):
    p = os.path.join(_user_dir(uid), filename)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def load_game(uid):
    data = _load_json(uid, "game.json")
    return _validate_game(data) if data else None


def save_game(uid, data):
    _save_json(uid, "game.json", data)


def delete_game(uid):
    p = os.path.join(_user_dir(uid), "game.json")
    if os.path.exists(p):
        os.remove(p)


def load_config():
    p = os.path.join(_DATA_DIR, "config.json")
    if not os.path.exists(p):
        return dict(CONFIG_DEFAULTS)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    return data


def save_config(data):
    p = os.path.join(_DATA_DIR, "config.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    set_config(data)


def _ensure_config():
    if os.path.exists(os.path.join(_DATA_DIR, "config.json")):
        set_config(load_config())
    else:
        save_config(dict(CONFIG_DEFAULTS))


# ===================== 剧情索引 =====================
def _find_event(event_id):
    """根据事件ID查找事件数据: (chapter_idx, event_idx, chapter, event)"""
    chapter_idx = event_id // 100
    event_idx = event_id % 100
    if chapter_idx < len(CHAPTER_EVENTS):
        ch = CHAPTER_EVENTS[chapter_idx]
        if event_idx < len(ch["events"]):
            return chapter_idx, event_idx, ch, ch["events"][event_idx]
    return None, None, None, None


def _advance_event(current_id):
    """推进到下一个事件ID。跨章节时自动跳到下一章"""
    ci = current_id // 100
    ei = current_id % 100
    ch = CHAPTER_EVENTS[ci]
    if ei + 1 < len(ch["events"]):
        return current_id + 1
    if ci + 1 < len(CHAPTER_EVENTS):
        return (ci + 1) * 100
    return None


def _current_event(game):
    """获取当前事件"""
    eid = game.get("event_id", 0)
    ci, ei, ch, ev = _find_event(eid)
    if ev is None:
        return None, None, None, None, None
    return ci, ei, ch, ev, eid


def _format_stats(stats):
    lines = []
    for k in STATS:
        v = stats.get(k, DEFAULT_STATS[k])
        lk = STAT_LABELS[k]
        bar = "▓" * max(0, v // 2) + "░" * max(0, 6 - v // 2)
        lines.append(f"{lk:>3}:{bar} {v}")
    lp = stats.get("luck_points", 0)
    hp = stats.get("hp", 10)
    mhp = max_hp(stats)
    lines.append(f" 生命: {'▓' * max(0, hp // 2)}{'░' * max(0, 6 - hp // 2)} {hp}/{mhp}")
    lines.append(f" 幸运点: {'★' * min(lp, 10)}{'☆' * max(0, 10 - min(lp, 10))} {lp}")
    return "\n".join(lines)


def _unpause(game, uid):
    if game.get("paused"):
        game["paused"] = False
        save_game(uid, game)
        return True
    return False


def _validate_game(game):
    """校验存档结构完整性，缺失字段补默认值"""
    if not isinstance(game, dict):
        return None
    if "stats" not in game:
        return None
    if not isinstance(game["stats"], dict):
        return None
    for k, v in DEFAULT_STATS.items():
        if k not in game["stats"]:
            game["stats"][k] = v
    game.setdefault("event_id", 0)
    game.setdefault("talents", ["幸运(199)"])
    game.setdefault("bag", {})
    game.setdefault("checkpoints", 3)
    game.setdefault("history", [])
    game.setdefault("quests", [])
    game.setdefault("explore_mode", False)
    game.setdefault("consecutive_choices", 0)
    return game


def _q(cmd, label=None):
    """QQ 回车指令链接"""
    if label is None:
        label = cmd
    return f"[{label}](mqqapi://aio/inlinecmd?command={cmd}&enter=true&reply=false)"


# ===================== 指令处理 =====================
@handler(r"^(开始异兽|异兽开始|开始兽迷城)\s*$", name="异兽开始", desc="开始新游戏", block=True, ignore_at_check=True)
async def cmd_start(event, match):
    uid = event.user_id
    existing = load_game(uid)
    if existing:
        await event.reply("你已经有一个存档了。使用「继续」继续冒险，或「异兽重置」重新开始。")
        return

    # 首次进入：展示角色创建引导
    save_game(uid, {"user_id": uid, "creating": True, "step": "welcome"})
    await event.reply(WELCOME_MESSAGE)


def _start_message(game):
    stats_txt = _format_stats(game["stats"])
    ev = CHAPTER_EVENTS[0]["events"][0]
    opts = ev["opts"]
    opt_lines = [f"  {i+1}. {label}" for i, (label, _) in enumerate(opts)]
    return (
        f"异兽迷城 · 第一部「十二生肖」\n"
        f"══════════\n"
        f"\n{ev['text']}\n\n"
        f"你的属性:\n```\n{stats_txt}\n```\n"
        f"\n" + "\n".join(opt_lines) +
        f"\n\n{_q('1','选1')} {_q('继续')} {_q('异兽教程','教程')}"
    )


@handler(r"^属性分配\s+(\S.*)$", name="属性分配", desc="创建角色时分配自由属性点", block=True, ignore_at_check=True)
async def cmd_alloc_creation(event, match):
    uid = event.user_id
    game = load_game(uid)
    if not game or not game.get("creating"):
        await event.reply("你不在角色创建流程中。使用「开始异兽」开始新游戏。")
        return

    args = match.group(1).strip()
    alloc, err = parse_allocation(args)
    if err:
        remaining = CREATION_FREE_POINTS
        await event.reply(f"{err}\n\n可分配点数: {CREATION_FREE_POINTS}")
        return

    stats = init_stats()
    apply_allocation(stats, alloc)

    # 创建正式存档
    game = {
        "user_id": uid,
        "event_id": 0,
        "stats": stats,
        "talents": ["幸运(199)"],
        "bag": {},
        "checkpoints": 3,
        "chapter_progress": 0,
        "history": [],
        "quests": [],
        "explore_mode": False,
    }
    game["stats"]["luck_points"] = 0
    save_game(uid, game)

    # 启动初始章节任务
    start_quest(game, "awakening_intro")
    save_game(uid, game)

    result = format_creation_result(stats, alloc)
    await event.reply(
        result + "\n\n" + _start_message(game) +
        "\n\n" + _q('1','选1') + " " + _q('继续')
    )


@handler(r"^(继续|异兽继续|兽迷城继续|继续冒险)\s*$", name="异兽继续", desc="继续当前游戏", block=True, ignore_at_check=True)
async def cmd_continue(event, match):
    uid = event.user_id
    game = load_game(uid)
    if not game:
        await event.reply("你还没有存档。使用「/开始异兽」开始冒险。")
        return

    _unpause(game, uid)

    # 如果在探索模式，退出回到剧情
    if game.get("explore_mode"):
        game["explore_mode"] = False
        game.pop("explore_event", None)

    # 重置连续选择计数和验证码
    game["consecutive_choices"] = 0
    game.pop("captcha_active", None)
    game.pop("captcha_answer", None)
    save_game(uid, game)

    ci, ei, ch, ev, eid = _current_event(game)
    if ev is None:
        await event.reply("剧情数据异常，请使用「/异兽重置」重新开始。")
        return

    stats_txt = _format_stats(game["stats"])
    opts = ev["opts"]
    opt_lines = [f"  {i+1}. {label}" for i, (label, _) in enumerate(opts)]

    chapter_name = CHAPTERS.get(ch["chapter"], {}).get("name", "未知")
    chapter_key = ch.get("chapter", "")
    quest_info = ""
    if chapter_key:
        qid = chapter_quest_id(chapter_key)
        if qid:
            start_quest(game, qid)
            save_game(uid, game)
        active = get_active_quests(game)
        if active:
            quest_info = f"\n📋 任务: {active[0]['name']} → {active[0]['goal']}"

    await event.reply(
        f"[{chapter_name}] #{eid}\n"
        f"══════════\n"
        f"{ev['text']}\n\n"
        f"```\n{stats_txt}\n```{quest_info}\n"
        + "\n".join(opt_lines) +
        f"\n\n{_q('1','选1')} {_q('继续')} {_q('异兽探索','探索')}"
    )


@handler(r"^异兽状态$", name="异兽状态", desc="查看角色状态", block=True, ignore_at_check=True)
async def cmd_status(event, match):
    uid = event.user_id
    game = load_game(uid)
    if not game:
        await event.reply("你还没有存档。使用「开始异兽」开始冒险。")
        return

    stats = game["stats"]
    stats_txt = _format_stats(stats)
    talents = ", ".join(game.get("talents", []))
    bag = game.get("bag", {})
    bag_txt = "、".join(f"{k}×{v}" for k, v in bag.items()) if bag else "空"
    ck = game.get("checkpoints", 0)
    eid = game.get("event_id", 0)
    lp = stats.get("luck_points", 0)

    # 章节进度
    ci = eid // 100
    ch_name = "未知"
    ch_total = 0
    if ci < len(CHAPTER_EVENTS):
        ch = CHAPTER_EVENTS[ci]
        ch_name = CHAPTERS.get(ch["chapter"], {}).get("name", "未知")
        ch_total = len(ch["events"])
    ei = eid % 100
    progress = f"{ei}/{ch_total}" if ch_total else "?"

    # 可加点属性 (紧凑格式适配手机)
    upgrade_lines = []
    for k in STATS:
        if k == "hp":
            continue
        v = stats.get(k, DEFAULT_STATS[k])
        upgrade_lines.append(f"  {STAT_LABELS[k]} {v:>2} +1")

    anti_spam = ""
    if game.get("captcha_active"):
        anti_spam = "\n验证码: 请回复数字验证"

    await event.reply(
        f"角色面板\n"
        f"══════════\n"
        f"[{ch_name}] #{eid} ({progress})\n"
        f"天赋: {talents}\n"
        f"回档: {ck}次\n"
        f"```\n{stats_txt}\n```\n"
        f"背包: {bag_txt}\n"
        f"加点(1幸运=1属性):\n" + "\n".join(upgrade_lines) +
        f"\n" + _q("加点 力量1","加点") + " " + _q("异兽菜单","菜单") + " " + _q("异兽探索","探索") +
        anti_spam
    )


@handler(r"^异兽菜单$", name="异兽菜单", desc="查看所有异世界指令", block=True, ignore_at_check=True)
async def cmd_menu(event, match):
    uid = event.user_id
    game = load_game(uid)
    has_save = bool(game)

    lines = [
        "异兽迷城 · 指令菜单",
        "══════════",
    ]

    if not has_save:
        lines += [
            _q("开始异兽", "开始异兽") + " · 创建角色",
            _q("异兽教程 1", "异兽教程") + " · 游戏教程",
        ]
    else:
        lines += [
            _q("继续", "继续") + " · 回到剧情",
            _q("1", "数字/选N") + " · 剧情选项",
            _q("异兽状态", "异兽状态") + " · 角色进度",
            _q("异兽探索", "异兽探索") + " · 打怪寻宝",
            _q("异兽任务", "异兽任务") + " · 任务追踪",
            _q("异兽教程 1", "异兽教程") + " · 玩法教程",
            _q("加点 力量1", "加点属性") + " · 幸运点提升",
            _q("异兽重置", "异兽重置") + " · 清除存档",
        ]
    lines.append(_q("回到主世界", "回到主世界") + " · 返回精灵")
    await event.reply("\n".join(lines))


@handler(r"^(\d+)$", name="快速选择", desc="输入数字快速选择剧情/探索选项", block=True, ignore_at_check=True)
async def cmd_num_choose(event, match):
    uid = event.user_id
    game = load_game(uid)
    if not game or game.get("paused"):
        return False
    n = int(match.group(1))

    # 验证码检查
    if game.get("captcha_active"):
        if n == game.get("captcha_answer"):
            game["captcha_active"] = False
            game.pop("captcha_answer", None)
            game["consecutive_choices"] = 0
            save_game(uid, game)
            await event.reply("验证通过。" + _q('1','请重新选择'))
            return True
        else:
            game["consecutive_choices"] = game.get("consecutive_choices", 0) + 1
            code = random.randint(1000, 9999)
            game["captcha_answer"] = code
            save_game(uid, game)
            await event.reply(
                f"验证码错误！请输入 {code} 以继续。\n"
                f"提示: 连续快速选择会触发验证，请阅读剧情内容后再做选择。"
            )
            return True

    # 探索模式
    if game.get("explore_mode"):
        ev = game.get("explore_event", {})
        opts = ev.get("opts", [])
        if n < 1 or n > len(opts):
            return False
        game["consecutive_choices"] = game.get("consecutive_choices", 0) + 1
        save_game(uid, game)
        await _handle_explore_choice(event, game, ev, n - 1)
        return

    # 剧情模式: 校验是否有活跃选项
    ci, ei, ch, ev, eid = _current_event(game)
    if ev is None:
        return False
    opts = ev.get("opts", [])
    if not opts or n < 1 or n > len(opts):
        return False

    # 连续选择计数 > 2 则触发验证码
    game["consecutive_choices"] = game.get("consecutive_choices", 0) + 1
    if game["consecutive_choices"] > 2:
        code = random.randint(1000, 9999)
        game["captcha_active"] = True
        game["captcha_answer"] = code
        save_game(uid, game)
        await event.reply(
            f"检测到连续快速选择。为防止刷屏，请输入验证码 {code} 后继续。"
        )
        return

    save_game(uid, game)
    await cmd_choose(event, match)


@handler(r"^选[择]?\s*(\d+)$", name="剧情选择", desc="选择剧情或探索选项", block=True, ignore_at_check=True)
async def cmd_choose(event, match):
    uid = event.user_id
    game = load_game(uid)
    if not game:
        await event.reply("你还没有存档。使用「/开始异兽」开始冒险。")
        return

    try:
        choice = int(match.group(1))
    except ValueError:
        await event.reply("无效选项。请回复数字选择。")
        return

    # 探索模式: 路由到探索处理
    if game.get("explore_mode"):
        ev = game.get("explore_event", {})
        opts = ev.get("opts", [])
        if choice < 1 or choice > len(opts):
            await event.reply(f"选项范围为 1-{len(opts)}。")
            return
        game["consecutive_choices"] = 0
        save_game(uid, game)
        await _handle_explore_choice(event, game, ev, choice - 1)
        return

    # 剧情模式
    game["consecutive_choices"] = 0
    game.pop("captcha_active", None)
    game.pop("captcha_answer", None)
    save_game(uid, game)

    ci, ei, ch, ev, eid = _current_event(game)
    if ev is None:
        await event.reply("剧情数据异常。使用「/异兽重置」重新开始。")
        return

    opts = ev["opts"]
    if choice < 1 or choice > len(opts):
        await event.reply(f"请输入 1-{len(opts)} 之间的数字。")
        return

    opt_label, opt_data = opts[choice - 1]
    stats = game["stats"]
    msgs = [f"你选择了【{opt_label}】"]

    # 处理检定
    for checker, checker_label in [
        ("spirit_check", "精神"), ("charisma_check", "魅力"),
        ("agility_check", "敏捷"), ("strength_check", "力量"),
        ("stamina_check", "耐力"),
    ]:
        if checker in opt_data:
            success, msg = check(stats, checker.replace("_check", ""), opt_data[checker])
            msgs.append(msg)
            handler_key = "success" if success else "fail"
            if handler_key in opt_data:
                hd = opt_data[handler_key]
                msgs.append(hd.get("text", ""))
                if "bonus" in hd:
                    for k, v in hd["bonus"].items():
                        stats[k] = stats.get(k, DEFAULT_STATS[k]) + v
                        msgs.append(f"({STAT_LABELS.get(k, k)}+{v})")
                if "damage" in hd:
                    stats["hp"] = max(0, stats.get("hp", 10) - hd["damage"])
                    msgs.append(f"(HP -{hd['damage']})")
                if "luck_points" in hd:
                    stats["luck_points"] = stats.get("luck_points", 0) + hd["luck_points"]
                    msgs.append(f"(幸运点+{hd['luck_points']})")
                if "item" in hd:
                    bag = game.setdefault("bag", {})
                    it, qty = hd["item"]
                    bag[it] = bag.get(it, 0) + qty
                    msgs.append(f"(获得: {it}×{qty})")
            break

    # 简单选项: 无检定
    if not any(k in opt_data for k in ["spirit_check", "charisma_check", "agility_check", "strength_check", "stamina_check", "fight"]):
        if "text" in opt_data:
            msgs.append(opt_data["text"])
        if "bonus" in opt_data:
            for k, v in opt_data["bonus"].items():
                stats[k] = stats.get(k, DEFAULT_STATS[k]) + v
                msgs.append(f"({STAT_LABELS.get(k, k)}+{v})")
        if "damage" in opt_data:
            stats["hp"] = max(0, stats.get("hp", 10) - opt_data["damage"])
            msgs.append(f"(HP -{opt_data['damage']})")
        if "luck_points" in opt_data:
            stats["luck_points"] = stats.get("luck_points", 0) + opt_data["luck_points"]
            msgs.append(f"(幸运点+{opt_data['luck_points']})")
        if "item" in opt_data:
            bag = game.setdefault("bag", {})
            it, qty = opt_data["item"]
            bag[it] = bag.get(it, 0) + qty
            msgs.append(f"(获得: {it}×{qty})")

    # 战斗
    if "fight" in opt_data:
        fb = opt_data["fight"]
        win, log, hp_after, drops = fight(
            opt_data.get("text", ""), stats, game.get("talents", []),
            fb["beast"], fb["power"], fb.get("type", "嗔兽")
        )
        msgs.append(log)
        stats["hp"] = max(0, hp_after)
        # 战斗道具掉落直接合并到 bag
        bag = game.setdefault("bag", {})
        for k, v in drops.items():
            bag[k] = bag.get(k, 0) + v
        hkey = "win" if win else "lose"
        if hkey in opt_data:
            hd = opt_data[hkey]
            msgs.append(hd.get("text", ""))
            if "bonus" in hd:
                for k, v in hd["bonus"].items():
                    stats[k] = stats.get(k, DEFAULT_STATS[k]) + v
                    msgs.append(f"({STAT_LABELS.get(k, k)}+{v})")
            if "damage" in hd:
                stats["hp"] = max(0, stats.get("hp", 10) - hd["damage"])
                msgs.append(f"(HP -{hd['damage']})")
            if "luck_points" in hd:
                stats["luck_points"] = stats.get("luck_points", 0) + hd["luck_points"]
                msgs.append(f"(幸运点+{hd['luck_points']})")
        if not win:
            game["checkpoints"] = max(0, game.get("checkpoints", 3) - 1)
            if game["checkpoints"] <= 0:
                delete_game(uid)
                await event.reply(
                    "\n".join(msgs) + "\n\n回档耗尽，游戏结束。\n" + _q("开始异兽","重新开始")
                )
                return
            msgs.append(f"损失1次回档 (剩余{game['checkpoints']})")
            await event.reply("\n".join(msgs) + "\n\n被击倒了..." + _q("继续","重新挑战"))
            return

    # 检查死亡
    if stats.get("hp", 10) <= 0:
        game["checkpoints"] = max(0, game.get("checkpoints", 3) - 1)
        if game["checkpoints"] <= 0:
            delete_game(uid)
            await event.reply(
                "\n".join(msgs) + "\n\n生命归零，回档耗尽。\n" + _q("开始异兽","重新开始")
            )
            return
        msgs.append(f"倒下! 剩余{game['checkpoints']}次回档 " + _q("继续","重新挑战"))
        await event.reply("\n".join(msgs))
        return

    # 推进到下一个事件
    next_id = None
    if "next" in opt_data:
        next_id = ci * 100 + opt_data["next"]
    else:
        next_id = _advance_event(eid)

    if next_id is None:
        await event.reply("\n".join(msgs) + "\n\n已完成当前版本！" + _q("继续","回看事件"))
        return

    _, _, next_ch, next_ev = _find_event(next_id)
    if next_ev is None:
        await event.reply("\n".join(msgs) + "\n\n已完成当前版本所有剧情！")
        return

    # 幸运点: 每完成1步剧情+1
    luck_step(stats)

    game["event_id"] = next_id
    game["history"].append(eid)

    # 任务检查: 章节通关时自动完成主线任务
    chapter_key = next_ch.get("chapter", "")
    qid = chapter_quest_id(chapter_key)
    if qid and next_id % 100 == 0:
        start_quest(game, qid)
    prev_chapter_key = ch.get("chapter", "")
    prev_qid = chapter_quest_id(prev_chapter_key)
    if prev_qid and next_id // 100 != eid // 100:
        reward = complete_quest(game, prev_qid)
        if reward:
            msgs.append(f"\n任务: {reward['name']} 完成")

    save_game(uid, game)

    # 渲染下一个事件
    stats_txt = _format_stats(stats)
    next_opts = next_ev["opts"]
    next_opt_lines = [f"  {i+1}. {label}" for i, (label, _) in enumerate(next_opts)]
    ch_name = CHAPTERS.get(next_ch["chapter"], {}).get("name", "未知")

    await event.reply(
        "\n".join(msgs) +
        f"\n\n[{ch_name}] #{next_id}\n\n{next_ev['text']}\n\n"
        f"```\n{stats_txt}\n```\n"
        + "\n".join(next_opt_lines) +
        f"\n\n{_q('1','选1')} {_q('继续')} {_q('异兽探索','探索')}"
    )


@handler(r"^加点\s+(\S.*)$", name="加点", desc="用幸运点提升属性，如「加点 力量2 敏捷1」", block=True, ignore_at_check=True)
async def cmd_alloc(event, match):
    uid = event.user_id
    game = load_game(uid)
    if not game:
        await event.reply("你还没有存档。使用「/开始异兽」开始冒险。")
        return

    args = match.group(1).strip()
    alloc = {}
    import re
    for m in re.finditer(r"(\S+?)(\d+)", args):
        k, v = m.group(1), int(m.group(2))
        label_map = {v: k for k, v in STAT_LABELS.items()}
        alloc[label_map.get(k, k)] = v

    if not alloc:
        await event.reply("格式: 「加点 力量2 敏捷1」\n属性: " + " ".join(STAT_LABELS.values()))
        return

    ok, msg = alloc_luck_points(game["stats"], alloc)
    if ok:
        save_game(uid, game)
        stats_txt = _format_stats(game["stats"])
        await event.reply(f"{msg}\n\n```\n{stats_txt}\n```")
    else:
        await event.reply(msg)


# ===================== 探索处理 =====================

async def _handle_explore_choice(event, game, ev, choice_idx):
    """处理探索事件中的选项"""
    uid = game["user_id"]
    opt_label = ev["opts"][choice_idx][0]

    msgs, _ = process_explore_choice(ev, choice_idx, game)
    msgs.insert(0, f"选择: {opt_label}")

    game["explore_mode"] = False
    game.pop("explore_event", None)
    save_game(uid, game)

    stats_txt = _format_stats(game["stats"])
    if game["stats"].get("hp", 0) <= 0:
        await event.reply(
            "\n".join(msgs) + "\n\n" +
            "失去意识...被青灵救回。使用「回档」回退。\n\n"
            f"```\n{stats_txt}\n```"
        )
        return

    await event.reply(
        "\n".join(msgs) + "\n\n" +
        f"```\n{stats_txt}\n```\n" +
        "探索结束。" + _q('继续','回主线') + " " + _q('异兽探索','再探索')
    )


@handler(r"^异兽探索$", name="异兽探索", desc="自由探索触发随机事件(战斗/检定/宝藏)", block=True, ignore_at_check=True)
async def cmd_explore(event, match):
    uid = event.user_id
    game = load_game(uid)
    if not game:
        await event.reply("你还没有存档。使用「开始异兽」开始冒险。")
        return

    eid = game.get("event_id", 0)
    chapter_key = None
    ci = eid // 100
    if ci < len(CHAPTER_EVENTS):
        chapter_key = CHAPTER_EVENTS[ci].get("chapter")

    ev = explore(chapter_key, game["stats"], game.get("talents", []))

    # 存储探索状态
    game["explore_mode"] = True
    game["explore_event"] = ev
    save_game(uid, game)

    opts = ev["opts"]
    opt_lines = [f"  {i+1}. {label}" for i, (label, _) in enumerate(opts)]
    await event.reply(
        "自由探索\n══════════\n"
        + ev["text"] + "\n\n"
        + "\n".join(opt_lines) +
        "\n\n" + _q('1','选1') + " " + _q('继续','返回剧情')
    )


@handler(r"^异兽任务$", name="异兽任务", desc="查看当前任务列表", block=True, ignore_at_check=True)
async def cmd_quests(event, match):
    uid = event.user_id
    game = load_game(uid)
    if not game:
        await event.reply("你还没有存档。使用「开始异兽」开始冒险。")
        return
    await event.reply(format_quest_list(game))


@handler(r"^异兽教程\s*(\d+)?$", name="异兽教程", desc="查看游戏教程, 如「异兽教程 1」", block=True, ignore_at_check=True)
async def cmd_tutorial(event, match):
    page = match.group(1)
    if page:
        await event.reply(get_tutorial_page(int(page)))
    else:
        await event.reply(get_tutorial_index())


@handler(r"^异兽重置$", name="异兽重置", desc="清除存档重新开始", block=True, ignore_at_check=True)
async def cmd_reset(event, match):
    uid = event.user_id
    game = load_game(uid)
    if not game:
        await event.reply("你还没有存档。使用「/开始异兽」开始冒险。")
        return
    delete_game(uid)
    await event.reply("存档已清除。使用「/开始异兽」重新开始冒险。")


@handler(r"^(回到主世界|返回精灵世界)$", name="返回主世界", desc="从异兽迷城返回精灵世界", block=True, ignore_at_check=True)
async def cmd_return(event, match):
    uid = event.user_id
    game = load_game(uid)
    if game:
        game["paused"] = True
        save_game(uid, game)
    await event.reply(
        "返回精灵世界。存档已保留。\n\n"
        "精灵世界指令:\n"
        "[精灵菜单](mqqapi://aio/inlinecmd?command=精灵菜单&enter=true&reply=false)\n"
        "[穿越异兽迷城](mqqapi://aio/inlinecmd?command=穿越异兽迷城&enter=true&reply=false)"
    )


# ===================== Web 面板 =====================
@register_route("GET", f"{_API}/users")
async def api_users(request):
    users = []
    for uid in os.listdir(_DATA_DIR):
        user_dir = os.path.join(_DATA_DIR, uid)
        if not os.path.isdir(user_dir):
            continue
        gf = os.path.join(user_dir, "game.json")
        if not os.path.exists(gf):
            continue
        game = load_game(uid)
        if not game:
            continue
        users.append({
            "user_id": uid,
            "event_id": game.get("event_id", 0),
            "stats": game.get("stats", {}),
            "checkpoints": game.get("checkpoints", 3),
        })
    return web.json_response(users)


@register_route("GET", f"{_API}/config")
async def api_config(request):
    cfg = load_config()
    return web.json_response({
        "values": cfg,
        "labels": CONFIG_LABELS,
        "keys_order": CONFIG_KEYS_ORDER,
    })


@register_route("POST", f"{_API}/config/save")
async def api_config_save(request):
    try:
        data = await request.json()
        save_config(data)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)


@register_route("GET", f"{_API}/story")
async def api_story(request):
    chapters = []
    for ci, ch in enumerate(CHAPTER_EVENTS):
        events_list = []
        for ei, ev in enumerate(ch["events"]):
            opts = [opt[0] for opt in ev.get("opts", [])]
            events_list.append({
                "id": ci * 100 + ei,
                "text": ev["text"][:100],
                "opts": opts,
            })
        chapters.append({
            "key": ch["chapter"],
            "name": CHAPTERS.get(ch["chapter"], {}).get("name", ch["name"]),
            "events": events_list,
        })
    return web.json_response(chapters)


# ===================== 面板页面 =====================

@on_load
def _on_load():
    _ensure_config()
    register_page(
        key=_PAGE_KEY,
        label="异兽迷城管理",
        source="plugin",
        source_name="beast-city",
        html_file=os.path.join(_PLUGIN_DIR, "panel.html"),
        icon='game',
    )


@on_unload
def _on_unload():
    unregister_page(_PAGE_KEY)
