# -*- coding: utf-8 -*-
"""异兽迷城 · 任务追踪系统"""

# ===================== 任务注册表 =====================
# 格式: {quest_id: {name, type, chapter, desc, goal, reward}}
QUESTS = {
    "awakening_intro": {
        "name": "序章：觉醒",
        "type": "主线",
        "chapter": "awakening",
        "desc": "晚自习放学回家，你被一个精神病患者撞倒...",
        "goal": "完成8个事件，通关序章",
        "reward": {"luck_points": 5, "desc": "幸运点+5，解锁天赋系统"},
    },
    "clues_investigate": {
        "name": "第一章：纸条之谜",
        "type": "主线",
        "chapter": "clues",
        "desc": "循着纸条线索，与青灵一起寻找真相",
        "goal": "完成4个事件，找到十二生肖组织",
        "reward": {"luck_points": 3, "desc": "幸运点+3"},
    },
    "trial_join": {
        "name": "第二章：天赋试炼",
        "type": "主线",
        "chapter": "trial",
        "desc": "参加十二生肖的入会测试",
        "goal": "完成9个事件，通过试炼",
        "reward": {"luck_points": 5, "desc": "幸运点+5，解锁第二天赋"},
    },
    "gujiacun_mission": {
        "name": "第三章：古家村事件",
        "type": "主线",
        "chapter": "gujiacun",
        "desc": "调查古家村142口人离奇死亡真相",
        "goal": "完成8个事件，击败槐仙",
        "reward": {"luck_points": 5, "desc": "幸运点+5，解锁火焰天赋"},
    },
    "dragon_village_world": {
        "name": "第四章：世界真相",
        "type": "主线",
        "chapter": "dragon_village",
        "desc": "了解十龙寨、符文回路、苍道等世界真相",
        "goal": "完成8个事件，加入十二生肖",
        "reward": {"luck_points": 5, "desc": "幸运点+5"},
    },
    "qingling_mystery": {
        "name": "第五章：青灵之死",
        "type": "主线",
        "chapter": "qingling_death",
        "desc": "妹妹是妄兽！内鬼潜伏，麒麟失踪",
        "goal": "完成8个事件，找到麒麟",
        "reward": {"luck_points": 5, "desc": "幸运点+5"},
    },
    # 支线任务
    "side_collect_cores": {
        "name": "兽核收集者",
        "type": "支线",
        "chapter": None,
        "desc": "在探索中收集5个兽核",
        "goal": "背包中兽核数量达到5个",
        "reward": {"luck_points": 3, "desc": "幸运点+3"},
    },
    "side_survivor": {
        "name": "生存达人",
        "type": "支线",
        "chapter": None,
        "desc": "在异兽迷城中存活10步剧情",
        "goal": "累计完成10步剧情",
        "reward": {"luck_points": 5, "desc": "幸运点+5"},
    },
    "side_first_fight": {
        "name": "初战告捷",
        "type": "支线",
        "chapter": None,
        "desc": "第一次亲手战胜一只兽",
        "goal": "在战斗中获胜",
        "reward": {"luck_points": 2, "desc": "幸运点+2"},
    },
}


def chapter_quest_id(chapter_key):
    """根据章节key返回对应的主线任务ID"""
    mapping = {
        "awakening": "awakening_intro",
        "clues": "clues_investigate",
        "trial": "trial_join",
        "gujiacun": "gujiacun_mission",
        "dragon_village": "dragon_village_world",
        "qingling_death": "qingling_mystery",
    }
    return mapping.get(chapter_key)


def get_quest(quest_id):
    """获取任务定义"""
    return QUESTS.get(quest_id)


def start_quest(game, quest_id):
    """开始一个新任务"""
    quests = game.setdefault("quests", [])
    for q in quests:
        if q["id"] == quest_id:
            return False
    qdef = get_quest(quest_id)
    if not qdef:
        return False
    quests.append({"id": quest_id, "status": "active"})
    return True


def complete_quest(game, quest_id):
    """完成一个任务"""
    quests = game.get("quests", [])
    for q in quests:
        if q["id"] == quest_id and q["status"] == "active":
            q["status"] = "completed"
            return get_quest(quest_id)
    return None


def get_active_quests(game):
    """获取所有活跃任务"""
    quests = game.get("quests", [])
    active = []
    for q in quests:
        if q["status"] == "active":
            qdef = get_quest(q["id"])
            if qdef:
                active.append(qdef)
    return active


def format_quest_list(game):
    """格式化任务列表"""
    active = get_active_quests(game)
    quests = game.get("quests", [])
    completed = [q["id"] for q in quests if q["status"] == "completed"]

    lines = ["任务面板", "══════════"]

    if active:
        lines.append("进行中:")
        for q in active:
            lines.append(f"  {q['name']}")
            lines.append(f"  └{q['goal']}")

    if completed:
        lines.append("已完成:")
        for qid in completed:
            qdef = get_quest(qid)
            if qdef:
                lines.append(f"  {qdef['name']}")

    if not active and not completed:
        lines.append("暂无任务。推进剧情自动接取。")

    return "\n".join(lines)
