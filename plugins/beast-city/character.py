# -*- coding: utf-8 -*-
"""异兽迷城 · 角色创建系统"""

from .game import STATS, STAT_LABELS, DEFAULT_STATS, init_stats

CREATION_FREE_POINTS = 20
CREATION_STEP_WELCOME = "welcome"
CREATION_STEP_ALLOC = "alloc"
CREATION_STEP_CONFIRM = "confirm"

WELCOME_MESSAGE = (
    "角色创建\n"
    "══════════\n"
    "扮演觉醒者「高阳」，天赋【幸运】#199。\n"
    "20点自由属性可分配:\n\n"
    "体力 — 生命值，归零死亡\n"
    "耐力 — 防御力，减少受伤\n"
    "力量 — 物理攻击力\n"
    "敏捷 — 先手/闪避/逃跑\n"
    "精神 — 侦查/识破伪装\n"
    "魅力 — 说服/情报/NPC\n"
    "运气 — 随机事件加成\n\n"
    "初始各10点（运气0点）。\n"
    "回复「属性分配 体力5 精神5...」\n"
    "每项最少0，总和不超过20。\n\n"
    "「继续」使用默认分配。"
)


def parse_allocation(args):
    """解析属性分配字符串，返回属性分配字典 或 错误信息"""
    import re
    label_map = {v: k for k, v in STAT_LABELS.items() if k != "hp"}
    alloc = {}

    for m in re.finditer(r"(\S+?)(\d+)", args):
        k, v = m.group(1), int(m.group(2))
        key = label_map.get(k, k)
        if key not in STATS or key == "hp":
            return None, f"无效属性: {k}。可用属性: " + "、".join(STAT_LABELS[k] for k in STATS if k != "hp")
        alloc[key] = v

    total = sum(alloc.values())
    if total > CREATION_FREE_POINTS:
        return None, f"分配点数({total})超过上限({CREATION_FREE_POINTS})，请重新分配。"
    if total <= 0:
        return None, "请至少分配1点属性。"
    return alloc, None


def apply_allocation(stats, alloc):
    """将分配字典应用到属性上"""
    for k, v in alloc.items():
        stats[k] = stats.get(k, DEFAULT_STATS[k]) + v
    return stats


def create_default_character():
    """默认角色创建：均匀分配"""
    stats = init_stats()
    default_alloc = {"stamina": 3, "strength": 3, "agility": 3, "spirit": 3, "charisma": 3, "luck": 5}
    return apply_allocation(stats, default_alloc)


def format_creation_result(stats, alloc):
    """格式化创建结果面板"""
    lines = []
    for k in STATS:
        if k == "hp":
            continue
        v = stats.get(k, DEFAULT_STATS[k])
        base = DEFAULT_STATS[k]
        bonus = alloc.get(k, 0)
        lk = STAT_LABELS[k]
        lines.append(f"  {lk}: {v} (基础{base} + 分配{bonus})")

    hp = stats.get("hp", 10)
    mhp = 10 + stats.get("stamina", 10) * 2
    lines.append(f"  体力: {hp}/{mhp}")
    lines.append(f"  幸运点: {stats.get('luck_points', 0)}")

    return "【角色创建完成】\n═══════════\n" + "\n".join(lines)
