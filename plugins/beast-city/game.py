# -*- coding: utf-8 -*-
"""异兽迷城 · 游戏核心逻辑
属性系统 / 幸运点加点 / 战斗引擎 / 属性检定"""

import random

# ===================== 属性常量 =====================
STATS = ["hp", "stamina", "strength", "agility", "spirit", "charisma", "luck"]
STAT_LABELS = {
    "hp": "体力", "stamina": "耐力", "strength": "力量",
    "agility": "敏捷", "spirit": "精神", "charisma": "魅力", "luck": "运气"
}
STAT_DESC = {
    "hp": "生命值，归零死亡",
    "stamina": "防御力，减少受伤",
    "strength": "物理攻击力",
    "agility": "先手/闪避/逃跑成功率",
    "spirit": "侦查/识破兽的伪装",
    "charisma": "说服/获取情报/NPC好感",
    "luck": "随机事件好结果概率加成",
}

# 初始属性
DEFAULT_STATS = {
    "hp": 10, "max_hp": 10,
    "stamina": 10, "strength": 10, "agility": 10,
    "spirit": 10, "charisma": 10, "luck": 0,
}

# 属性加点消耗（每次1幸运点+1属性）
STAT_COST = 1  # 1幸运点 = +1属性值

# ===================== 游戏配置 =====================
CONFIG_DEFAULTS = {
    "LUCK_PER_STEP": 1,       # 每完成1步剧情获得幸运点
    "MAX_HP_BASE": 10,         # 基础最大HP
    "HP_PER_STAMINA": 2,       # 每点耐力增加的HP上限
    "BEAST_POWER_BASE": 10,    # 兽的基础战力
    "CHECK_DIFFICULTY_BASE": 10,  # 检定基准难度
    "CRITICAL_RATE": 10,       # 暴击率%
    "FLEE_RATE_BASE": 30,      # 基础逃跑成功率%
    "MAX_CHECKPOINTS": 3,      # 最大回档次数
}

CONFIG_LABELS = {
    "LUCK_PER_STEP": "每步幸运点",
    "MAX_HP_BASE": "基础HP上限",
    "HP_PER_STAMINA": "每耐力HP加成",
    "BEAST_POWER_BASE": "兽基础战力",
    "CHECK_DIFFICULTY_BASE": "检定基准难度",
    "CRITICAL_RATE": "暴击率(%)",
    "FLEE_RATE_BASE": "逃跑率(%)",
    "MAX_CHECKPOINTS": "最大回档次数",
}

CONFIG_KEYS_ORDER = list(CONFIG_DEFAULTS.keys())

_config = dict(CONFIG_DEFAULTS)


def get_config():
    return _config


def set_config(data):
    _config.update(data)


def reset_config():
    _config.clear()
    _config.update(CONFIG_DEFAULTS)


# ===================== 属性操作 =====================
def init_stats():
    return dict(DEFAULT_STATS)


def max_hp(stats):
    return stats.get("max_hp", 10) + stats.get("stamina", 10) * _config["HP_PER_STAMINA"]


def stat_add(stats, key, amount):
    """给某项属性加点，固定1幸运点=1属性"""
    stats[key] = stats.get(key, 0) + amount
    if key == "hp":
        stats["max_hp"] = stats.get("max_hp", 10) + amount
    return stats


def alloc_luck_points(stats, alloc_dict):
    """分配幸运点: alloc_dict = {"strength": 2, "agility": 1}"""
    used = sum(alloc_dict.values())
    points = stats.get("luck_points", 0)
    if used > points:
        return False, f"幸运点不足！需要{used}，剩余{points}"
    for key, amount in alloc_dict.items():
        if key not in STATS:
            return False, f"未知属性: {key}"
        stats[key] = stats.get(key, DEFAULT_STATS[key]) + amount
    stats["luck_points"] = points - used
    return True, f"成功分配{used}幸运点"


def luck_step(stats, steps=1):
    """剧情推进获得幸运点"""
    stats["luck_points"] = stats.get("luck_points", 0) + steps * _config["LUCK_PER_STEP"]


# ===================== 属性检定 =====================
def check(stats, stat_key, difficulty=None):
    """
    属性检定: 掷1d20 + 属性值 vs 难度
    返回 (是否成功, 检定描述)
    自然20 = 大成功, 自然1 = 大失败
    """
    if difficulty is None:
        difficulty = _config["CHECK_DIFFICULTY_BASE"]
    roll = random.randint(1, 20)
    bonus = stats.get(stat_key, DEFAULT_STATS[stat_key])
    total = roll + bonus

    if roll == 20:
        return True, f"🎯 大成功！(自然20 + {bonus} = {total} vs 难度{difficulty})"
    if roll == 1:
        return False, f"💀 大失败！(自然1 + {bonus} = {total} vs 难度{difficulty})"

    success = total >= difficulty
    tag = "✅" if success else "❌"
    return success, f"{tag} 检定: 1d20({roll}) + {bonus}({STAT_LABELS[stat_key]}) = {total} vs 难度{difficulty}"


# ===================== 战斗引擎 =====================
def fight_calc(player_stats, player_talents, beast_name, beast_power, beast_type="嗔兽"):
    """
    简化战斗: 觉醒者 vs 兽
    返回 (胜利/失败, 战斗日志, 受伤程度)
    """
    log = []
    power = player_stats.get("strength", 10) + player_stats.get("agility", 10)
    hp = max_hp(player_stats)
    current_hp = player_stats.get("hp", hp)

    # 敏捷检定: 决定先手
    agi_ok, agi_msg = check(player_stats, "agility", 12)
    log.append(agi_msg)

    if agi_ok:
        log.append("你抢得先手，率先攻击！")
        first_strike = True
    else:
        log.append(f"{beast_name}速度惊人，先发制人！")
        first_strike = False

    # 战斗轮(最多3回合)
    for turn in range(1, 4):
        log.append(f"--- 第{turn}回合 ---")

        if first_strike or turn > 1:
            # 玩家攻击
            str_ok, str_msg = check(player_stats, "strength", beast_power // 2 + 5)
            log.append(f"攻击: {str_msg}")
            if str_ok:
                damage = random.randint(power // 2, power)
                beast_power -= damage
                log.append(f"造成 {damage} 点伤害！兽剩余战力: {max(0, beast_power)}")
            else:
                log.append("攻击落空！")

        if beast_power <= 0:
            log.append(f"击败了【{beast_name}】！")
            return True, log, 0

        # 兽反击
        beast_roll = random.randint(1, 20) + beast_power // 5
        def_ok, def_msg = check(player_stats, "stamina", beast_roll)
        log.append(f"防御: {def_msg}")
        if not def_ok:
            injury = random.randint(3, 8)
            current_hp -= injury
            log.append(f"受到 {injury} 点伤害！剩余HP: {max(0, current_hp)}/{hp}")
        else:
            minor = random.randint(0, 2)
            if minor:
                current_hp -= minor
                log.append(f"擦伤 {minor} 点（剩余HP: {current_hp}/{hp}）")

        if current_hp <= 0:
            log.append("你倒下了...")
            return False, log, hp

    # 3回合未分胜负, 判定: 按剩余HP/战力比
    ratio = current_hp / max(hp, 1) - beast_power / max(power * 2, 1)
    luck_bonus = player_stats.get("luck", 0) * 2
    final = random.randint(1, 100) + int(ratio * 50) + luck_bonus
    if final >= 50:
        log.append(f"鏖战之后，你艰难取胜！")
        return True, log, hp - current_hp
    else:
        log.append(f"力竭不敌，{beast_name}占据上风...")
        return False, log, hp


def fight(event_text, player_stats, player_talents, beast_name, beast_power, beast_type="嗔兽"):
    """战斗入口: 返回 (胜利?, 完整日志文本, 最终HP, 道具掉落)"""
    win, log, injury = fight_calc(player_stats, player_talents, beast_name, beast_power, beast_type)
    full_log = f"\n⚔️ 【战斗: {beast_name}】{event_text}\n" + "\n".join(log)

    drops = {}
    if win:
        drops["兽核"] = random.randint(1, 3)
        full_log += f"\n\n🎁 掉落: 兽核×{drops['兽核']}"
    return win, full_log, player_stats.get("hp", 10) - injury, drops


# ===================== 逃跑 =====================
def flee(stats):
    """逃跑检定: 敏捷 + 运气加成"""
    base = _config["FLEE_RATE_BASE"] + stats.get("agility", 10) * 2 + stats.get("luck", 0)
    rate = min(95, max(5, base))
    success = random.randint(1, 100) <= rate
    return success, f"逃跑成功率: {rate}%"
