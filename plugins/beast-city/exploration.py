# -*- coding: utf-8 -*-
"""异兽迷城 · 自由探索系统 · 打怪/移动/检定"""
import random

from .game import check, fight, fight_calc, STAT_LABELS, DEFAULT_STATS


# ===================== 可移动地点 =====================
LOCATIONS = {
    "awakening": [
        {"id": "home", "name": "回家", "desc": "高阳的家，安全的地方。"},
        {"id": "school", "name": "学校", "desc": "放学后的教室空无一人。"},
        {"id": "street", "name": "街边小巷", "desc": "回家的路上，一条昏暗的巷子。"},
        {"id": "convenience", "name": "便利店", "desc": "24小时营业的便利店。"},
    ],
    "clues": [
        {"id": "bridge", "name": "青扬大桥", "desc": "跨江大桥，桥下暗流涌动。"},
        {"id": "factory", "name": "废弃工厂", "desc": "12号工厂，兽的痕迹随处可见。"},
        {"id": "headquarters", "name": "觉醒者总部", "desc": "麒麟公会的地下基地。"},
    ],
    "trial": [
        {"id": "market", "name": "十龙寨跳蚤市场", "desc": "卖各种觉醒者用品。"},
        {"id": "arena", "name": "训练场", "desc": "十龙寨的训练场地，可以切磋。"},
        {"id": "dojo", "name": "天狗的道场", "desc": "天狗的私人训练场。"},
    ],
    "gujiacun": [
        {"id": "village", "name": "古家村", "desc": "被槐仙诅咒的村庄。"},
        {"id": "mountain", "name": "后山", "desc": "村后荒山，慎入。"},
        {"id": "temple", "name": "破庙", "desc": "一座废弃的庙宇。"},
    ],
    "dragon_village": [
        {"id": "tenlong_street", "name": "十龙寨主街", "desc": "十二生肖总部所在地。"},
        {"id": "dark_alley", "name": "暗巷", "desc": "情报贩子出没的地方。"},
        {"id": "underground_b1", "name": "地下城B1", "desc": "地下城第一层，迷途者游荡。"},
    ],
    "qingling_death": [
        {"id": "underground_b3", "name": "地下城B3", "desc": "第三层，更危险的区域。"},
        {"id": "underground_b6", "name": "地下城B6", "desc": "第六层废墟。青灵在这里..."},
        {"id": "infirmary", "name": "医疗站", "desc": "负1层临时医疗点。"},
    ],
}

DEFAULT_LOCATIONS = [
    {"id": "wander", "name": "闲逛", "desc": "没有明确目的地，到处走走。"},
]


def get_locations(chapter_key):
    return LOCATIONS.get(chapter_key, DEFAULT_LOCATIONS)


# ===================== 探索事件池 =====================
# 按章节分组，每章含对话事件 + 战斗事件 + 寻宝事件

EXPLORE_EVENTS = {
    "awakening": [
        # 对话事件
        {
            "text": "你在放学路上发现一条陌生小巷。巷子里有微弱的能量波动——是兽留下的痕迹。",
            "opts": [
                ("仔细调查·可能发现线索", {
                    "spirit_check": 11,
                    "success": {"text": "墙角发现一块黑色鳞片，绝不是普通动物的。\n（精神+1，获得黑色鳞片）", "bonus": {"spirit": 1}, "item": ("黑色鳞片", 1)},
                    "fail": {"text": "你翻找了一阵，什么都没找到。也许只是错觉。"},
                }),
                ("快速离开", {"safe": True, "text": "这地方让你不安。你快步离开小巷。安全第一。"}),
            ],
        },
        {
            "text": "便利店门口，穿风衣的男人凑上来：「小兄弟，最近晚上少出门。不太平。」",
            "opts": [
                ("追问详情", {
                    "charisma_check": 12,
                    "success": {"text": "「几个夜跑的失踪了，警察说是野兽袭击。」他递给你一瓶水。\n（魅力+1）", "bonus": {"charisma": 1}},
                    "fail": {"text": "男人摆摆手，不愿多说，匆匆离开了。"},
                }),
                ("道谢后离开", {"safe": True, "text": "你说声谢谢，拿着水离开。今天又多了一个提醒你小心的人。"}),
            ],
        },
        # 战斗事件
        {
            "text": "夜风吹过操场，你听到身后有沉重的脚步声——不是人的节奏。你猛地回头，一只黑影从树丛中走出：它通体漆黑，眼睛发着红光！",
            "opts": [
                ("应战！", {
                    "fight": {"beast": "嗔兽·幼体", "power": 8, "type": "嗔兽"},
                    "win": {"text": "你挥拳击退幼兽！它嚎叫着逃进黑暗中。\n（获得兽核×1）", "item": ("兽核", 1)},
                    "lose": {"text": "幼兽抓了你一爪，但听到远处汽车声后溜了。\n（HP -3）", "damage": 3},
                }),
                ("快跑！", {
                    "agility_check": 13,
                    "success": {"text": "你拔腿狂奔，冲进有灯的便利店才敢停下喘气。\n（敏捷+1）", "bonus": {"agility": 1}},
                    "fail": {"text": "你慢了半步，被它在腿上抓了一道。还好不算深。\n（HP -2）", "damage": 2},
                }),
            ],
        },
        {
            "text": "你路过垃圾桶时听到奇怪的声音。一个佝偻的身影在翻垃圾——突然抬头，狰狞的面孔和尖锐的牙齿直扑而来！",
            "opts": [
                ("格挡反击", {
                    "stamina_check": 10,
                    "success": {"text": "你用垃圾桶盖挡住攻击，反手一拳把它打翻。仔细一看——只是一只吓人的野猫。虚惊一场。\n（耐力+1）", "bonus": {"stamina": 1}},
                    "fail": {"text": "你被扑倒在地！挣扎之际发现只是一只受惊的猫。邻居王阿姨笑呵呵地问「没事吧小伙子？」\n（HP -1）", "damage": 1},
                }),
                ("闪避侧移", {
                    "agility_check": 10,
                    "success": {"text": "你一个侧身躲过，抄起路边木棍准备反击——发现是只野猫叼着鱼骨头。\n（敏捷+1）", "bonus": {"agility": 1}},
                    "fail": {"text": "踩到香蕉皮滑倒了...野猫也被吓跑了。\n（HP -1）", "damage": 1},
                }),
            ],
        },
    ],
    "clues": [
        {
            "text": "青扬大桥下，防水袋卡在石缝里。里面有揉皱的纸条：「12号工厂」「午夜」「别信任何人」。",
            "opts": [
                ("打捞纸条", {
                    "spirit_check": 13,
                    "success": {"text": "纸条上的文字让你脊背发凉。谁在跟踪谁？\n（精神+1，线索+1）", "bonus": {"spirit": 1}},
                    "fail": {"text": "袋子泡太久了，纸片随江水漂走。"},
                }),
                ("拍照发给青灵", {"text": "青灵秒回：「别动！等我过来。」十分钟后用金属天赋捞了上来。", "luck_points": 1}),
            ],
        },
        {
            "text": "工厂墙角的流浪汉浑身发抖。他脖子后方有黑色纹路——被他咬过的地方像个黑洞。",
            "opts": [
                ("上前询问", {
                    "charisma_check": 11,
                    "success": {"text": "「它们...在地下三层...」流浪汉说完疯了般跑了。\n（魅力+1）", "bonus": {"charisma": 1}},
                    "fail": {"text": "流浪汉被你吓到，抓起酒瓶砸过来。赶紧躲开。"},
                }),
                ("制服他·检查伤口", {
                    "fight": {"beast": "寄生体·流浪汉", "power": 10, "type": "寄生体"},
                    "win": {"text": "你制服了他，发现他后颈的寄生体已死亡。他慢慢恢复了神智。\n（兽核×1）", "item": ("兽核", 1)},
                    "lose": {"text": "他暴起挣脱，一溜烟跑没影了。看来被寄生者力量远超常人。\n（HP -2）", "damage": 2},
                }),
            ],
        },
        {
            "text": "夜巡工厂区时墙角传来异响。你躲到集装箱后，看到两个穿黑风衣的人在低声交谈。",
            "opts": [
                ("偷听对话", {
                    "spirit_check": 14,
                    "success": {"text": "「...下周交货。给长老会的实验品都准备好了。」「那个高中生怎么办？」「先观察。」\n你屏住呼吸。他们在说你！\n（精神+1，情报+1）", "bonus": {"spirit": 1}},
                    "fail": {"text": "你碰倒了一个空罐。两人瞬间消失。该死！被发现了。"},
                }),
                ("悄悄离开", {"text": "太冒险了。你记下时间和地点，回去找青灵商量。", "luck_points": 1}),
            ],
        },
        # 工厂区战斗
        {
            "text": "12号工厂后门，三只嗤兽幼体围住了你的去路。它们龇牙咧嘴，口水滴答！",
            "opts": [
                ("冲上去打！", {
                    "fight": {"beast": "嗤兽×3", "power": 13, "type": "嗔兽"},
                    "win": {"text": "你拳拳到肉清理了这群畜牲！从其中一只体内掉出闪着光的矿物。\n（兽核×2）", "item": ("兽核", 2)},
                    "lose": {"text": "数量太多了。你且战且退，身上多了几道抓痕。\n（HP -5）", "damage": 5},
                }),
                ("找掩护·绕路", {
                    "agility_check": 14,
                    "success": {"text": "你借掩护无声通过，没有被发现。\n（敏捷+1）", "bonus": {"agility": 1}},
                    "fail": {"text": "不小心踢到铁块——三只嗤兽全冲过来了！\n（HP -2）", "damage": 2},
                }),
            ],
        },
    ],
    "trial": [
        {
            "text": "十龙寨跳蚤市场，蓝色液体在小瓶子里发光。摊贩喊：「天赋碎片！一颗3000灵石！」",
            "opts": [
                ("上前问价", {
                    "charisma_check": 14,
                    "success": {"text": "「那不是天赋碎片，是假的。」天狗从你身后走上来：「想变强就用这个。」扔给你一颗体质增强丹。\n（魅力+1，获得体质丹）", "bonus": {"charisma": 1}, "item": ("体质丹", 1)},
                    "fail": {"text": "摊贩要价3000灵石。你摸了摸口袋——空的。"},
                }),
                ("无视·继续逛", {"text": "这种来路不明的东西少碰——青灵教过你的。"}),
            ],
        },
        {
            "text": "训练场上天狗摆下擂台：「新人来跟我过过招！放心，不会打死你的。」",
            "opts": [
                ("接受挑战", {
                    "fight": {"beast": "天狗·训练模式", "power": 15, "type": "觉醒者"},
                    "win": {"text": "你居然赢了！天狗被摔倒后反而大笑：「有两下子！小子，我认可你了。」\n（兽核×2）", "item": ("兽核", 2)},
                    "lose": {"text": "天狗五招就把你放倒。他拉你起来：「不错，撑了五招，比那些一招躺的新人强多了。」\n（HP -2）", "damage": 2},
                }),
                ("「我才不跟你打！」", {"text": "「胆小鬼！」天狗撇嘴，转头找人过招去了。安全第一。", "luck_points": 1}),
            ],
        },
        # 训练场野怪
        {
            "text": "训练场角落的沙袋后面传来低吼。一只被捕获的贪兽挣脱了束缚，朝你扑来！",
            "opts": [
                ("迎战！", {
                    "fight": {"beast": "贪兽·逃脱体", "power": 16, "type": "贪兽"},
                    "win": {"text": "你临危不乱把它打趴，守场守卫连连道谢：「小兄弟厉害！这袋灵石请笑纳。」\n（兽核×2）", "item": ("兽核", 2)},
                    "lose": {"text": "守卫们及时赶到制服了它。你只是擦破了皮。\n（HP -2）", "damage": 2},
                }),
                ("呼叫守卫", {
                    "charisma_check": 11,
                    "success": {"text": "守卫们闻声赶到，三下五除二制住了它。队长拍拍你肩膀：「眼力不错。」\n（魅力+1）", "bonus": {"charisma": 1}},
                    "fail": {"text": "守卫来得慢了一步，好在贪兽没造成杀伤。"},
                }),
            ],
        },
    ],
    "gujiacun": [
        {
            "text": "山路旁的老木桩上刻满符文。青灵说过，槐仙的遗迹不要乱碰。",
            "opts": [
                ("研究符文", {
                    "spirit_check": 15,
                    "success": {"text": "你认出这是镇邪符文——与槐仙的寄生回路原理相通。\n（精神+2）", "bonus": {"spirit": 2}},
                    "fail": {"text": "符文太老了，模糊不清。你拍了照留着研究。"},
                }),
                ("绕路走", {"text": "不碰为上。你用布包手绕过木桩。", "luck_points": 1}),
            ],
        },
        {
            "text": "古家村后山夜雾弥漫。前方树影晃动——三个人形生物摇摇晃晃地朝你走来。是被槐仙寄生的村民！",
            "opts": [
                ("战斗·解救他们！", {
                    "fight": {"beast": "寄生村民×3", "power": 18, "type": "寄生体"},
                    "win": {"text": "你击碎了寄生在他们后颈的槐仙根须。三人瘫倒在地恢复意识：「谢...谢谢...」\n（兽核×3）", "item": ("兽核", 3)},
                    "lose": {"text": "数量太多了！你且战且退到村口，槐仙的根须够不到这里。\n（HP -6）", "damage": 6},
                }),
                ("撤退·回村求援", {
                    "agility_check": 14,
                    "success": {"text": "你一路狂奔回村，叫上青灵带人回去救援。\n（敏捷+1）", "bonus": {"agility": 1}},
                    "fail": {"text": "雾太浓了迷了路...绕了半小时才摸回村里。\n（HP -1）", "damage": 1},
                }),
            ],
        },
    ],
    "dragon_village": [
        {
            "text": "暗巷里蒙面人压低声音：「想买情报么？关于妄兽的。」",
            "opts": [
                ("交易·「说」", {
                    "charisma_check": 13,
                    "success": {"text": "「妄兽保留生前记忆和情感，但身体完全兽化。能伪装成人类毫无破绽。」蒙面人收钱就消失了。\n（魅力+1）", "bonus": {"charisma": 1}},
                    "fail": {"text": "蒙面人伸手：「先付钱。」你用柳轻盈的名头吓跑了他。"},
                }),
                ("拒绝", {"text": "在这种地方买情报等于告诉所有人你在查什么——太危险了。"}),
            ],
        },
        {
            "text": "地下城B1走廊尽头，两只迷途者正在啃食一只贪兽的残骸。它们转过头，空洞的眼眶对上了你的视线。",
            "opts": [
                ("先发制人！", {
                    "fight": {"beast": "迷途者×2", "power": 17, "type": "迷途者"},
                    "win": {"text": "你利索地放倒两只迷途者。贪兽残骸旁居然有一颗完好的兽核。\n（兽核×2）", "item": ("兽核", 2)},
                    "lose": {"text": "迷途者不怕痛的特性让你吃尽苦头。青灵及时赶到一刀解决。\n（HP -4）", "damage": 4},
                }),
                ("悄悄绕过", {
                    "agility_check": 15,
                    "success": {"text": "你无声无息地溜过走廊。\n（敏捷+1）", "bonus": {"agility": 1}},
                    "fail": {"text": "踩到碎玻璃——它们全冲过来了！\n（HP -2）", "damage": 2},
                }),
            ],
        },
        # 地下城深处
        {
            "text": "B1深处传来重物拖地的声音。你看到一只巨型贪兽拖着半截迷途者的尸体在觅食。它比普通贪兽大了三倍！",
            "opts": [
                ("大胆战斗！", {
                    "fight": {"beast": "巨型贪兽", "power": 22, "type": "贪兽"},
                    "win": {"text": "硬碰硬拿下！这只贪兽吞了不少同类，兽核比普通的大。\n（兽核×5）", "item": ("兽核", 5)},
                    "lose": {"text": "它一掌把你拍飞撞墙。青灵把你从碎石堆里拉出来。\n（HP -8）", "damage": 8},
                }),
                ("暗中观察·记录习性", {
                    "spirit_check": 14,
                    "success": {"text": "你记下贪兽的领地行为和进食模式。这些数据对觉醒者公会很有价值。\n（精神+1，情报+1）", "bonus": {"spirit": 1}},
                    "fail": {"text": "光线太暗记录不清。下次带个手电筒来。"},
                }),
            ],
        },
    ],
    "qingling_death": [
        {
            "text": "B6废墟深处，翻倒货架堵住的小房间。里面隐约有呼吸声。",
            "opts": [
                ("破门而入", {
                    "fight": {"beast": "迷途者·残魂", "power": 12, "type": "迷途者"},
                    "win": {"text": "轻松解决残魂。房间里有半瓶水和压缩饼干。\n（获得压缩饼干×2）", "item": ("压缩饼干", 2)},
                    "lose": {"text": "被残魂偷袭，青灵一刀解决。\n（HP -2）", "damage": 2},
                }),
                ("悄悄查看", {
                    "spirit_check": 12,
                    "success": {"text": "透过缝隙看到幸存的觉醒者！你们把他救走了。\n（精神+1，幸运点+1）", "bonus": {"spirit": 1}, "luck_points": 1},
                    "fail": {"text": "太黑了看不清。算了，继续前进。"},
                }),
            ],
        },
        # 地下城精英怪
        {
            "text": "B6地下通道突然裂开，一只触须从裂缝中伸出——是槐仙留在深处的根须！它缠绕成一个人形，向你走来。",
            "opts": [
                ("全力迎战！", {
                    "fight": {"beast": "槐仙·深根", "power": 24, "type": "寄生体"},
                    "win": {"text": "你斩断了这一段根须！整条通道的槐仙活性都弱了几分。\n（兽核×5，幸运点+2）", "item": ("兽核", 5), "luck_points": 2},
                    "lose": {"text": "根须太粗了！青灵用金属刀斩断触须拉你回来。\n（HP -10）", "damage": 10},
                }),
                ("切根逃路", {
                    "agility_check": 16,
                    "success": {"text": "你手刀利落切断了冒头的根须，趁槐仙来不及再伸出更多，快速冲过裂缝区域。\n（敏捷+2）", "bonus": {"agility": 2}},
                    "fail": {"text": "根须缠住了脚踝！青灵一刀斩断才救出你。\n（HP -3）", "damage": 3},
                }),
            ],
        },
        {
            "text": "你找不到青灵了。她明明刚才还在身后——突然前方黑暗中走出一个人影。是青灵，但眼神不对。妄兽伪装的！",
            "opts": [
                ("识破后攻击！", {
                    "fight": {"beast": "妄兽·伪青灵", "power": 20, "type": "妄兽"},
                    "win": {"text": "你没有被它骗到！妄兽化回原形逃跑。你在地上发现了真正青灵挣脱后留下的印记——她还活着！\n（兽核×3，幸运点+1）", "item": ("兽核", 3), "luck_points": 1},
                    "lose": {"text": "你犹豫了一秒，被妄兽抓中了肩膀。但它似乎...没下死手？\n（HP -3）", "damage": 3},
                }),
                ("喊话试探·「青灵最讨厌什么」", {
                    "spirit_check": 16,
                    "success": {"text": "「青灵最讨厌洋葱！」你喊道。妄兽愣了——它复制了青灵的记忆，但没复制她的味觉。你趁机一刀。\n（精神+2）", "bonus": {"spirit": 2}},
                    "fail": {"text": "妄兽毫不犹豫地答对了。但太对了——真正的青灵不会记得这么清楚。你还是发现了破绽。\n（精神+1）", "bonus": {"spirit": 1}},
                }),
            ],
        },
    ],
}

DEFAULT_EXPLORE = [
    {
        "text": "你漫无目的地闲逛，微风吹过。今天似乎格外平静。",
        "opts": [
            ("继续散步", {"text": "你多走了几条街。一切正常——在这个世界「正常」本身就是最大的幸运。", "luck_points": 1}),
            ("早点回去", {"text": "今天到此为止。保存体力，明天还有更重要的事。", "luck_points": 1}),
        ],
    },
]


def get_explore_pool(chapter_key):
    return EXPLORE_EVENTS.get(chapter_key, DEFAULT_EXPLORE)


def explore(chapter_key, stats, talents):
    pool = get_explore_pool(chapter_key)
    event = random.choice(pool)
    return event


def process_explore_choice(event, choice_idx, game):
    """处理探索选项: 检定/战斗/奖励。返回 (消息列表, 事件是否已结束)"""
    opts = event["opts"]
    if choice_idx < 0 or choice_idx >= len(opts):
        return ["无效选项。"], True

    _, opt_data = opts[choice_idx]
    stats = game["stats"]
    bag = game.setdefault("bag", {})
    msgs = []

    # 安全选项: 直接返回文本
    if opt_data.get("safe"):
        msgs.append(opt_data.get("text", ""))
        if "luck_points" in opt_data:
            stats["luck_points"] = stats.get("luck_points", 0) + opt_data["luck_points"]
        return msgs, True

    # 检定类: 精神/魅力/敏捷/力量/耐力
    for checker in ["spirit_check", "charisma_check", "agility_check", "strength_check", "stamina_check"]:
        if checker in opt_data:
            stat_key = checker.replace("_check", "")
            dc = opt_data[checker]
            success, msg = check(stats, stat_key, dc)
            msgs.append(msg)
            handler_key = "success" if success else "fail"
            if handler_key in opt_data:
                hd = opt_data[handler_key]
                msgs.append(hd.get("text", ""))
                if "bonus" in hd:
                    for k, v in hd["bonus"].items():
                        stats[k] = stats.get(k, DEFAULT_STATS[k]) + v
                if "damage" in hd:
                    stats["hp"] = max(0, stats.get("hp", 10) - hd["damage"])
                if "luck_points" in hd:
                    stats["luck_points"] = stats.get("luck_points", 0) + hd["luck_points"]
                if "item" in hd:
                    it, qty = hd["item"]
                    bag[it] = bag.get(it, 0) + qty
                    msgs.append(f"（获得: {it}×{qty}）")
            if "luck_points" in opt_data:
                stats["luck_points"] = stats.get("luck_points", 0) + opt_data["luck_points"]
            return msgs, True

    # 战斗类
    if "fight" in opt_data:
        fd = opt_data["fight"]
        win, log, remaining_hp, drops = fight("", stats, game.get("talents", []),
                                              fd["beast"], fd["power"], fd.get("type", "嗔兽"))
        msgs.append(log)
        stats["hp"] = remaining_hp

        if win:
            if "win" in opt_data:
                wd = opt_data["win"]
                msgs.append(wd.get("text", ""))
                if "item" in wd:
                    it, qty = wd["item"]
                    bag[it] = bag.get(it, 0) + qty
                if "luck_points" in wd:
                    stats["luck_points"] = stats.get("luck_points", 0) + wd["luck_points"]
            for k, v in drops.items():
                bag[k] = bag.get(k, 0) + v
        else:
            if "lose" in opt_data:
                ld = opt_data["lose"]
                msgs.append(ld.get("text", ""))
                if "damage" in ld:
                    stats["hp"] = max(0, stats.get("hp", 0) - ld["damage"])
            else:
                msgs.append("你逃回了安全地带。")
        return msgs, True

    # 纯文本选项
    if "text" in opt_data:
        msgs.append(opt_data["text"])
    if "luck_points" in opt_data:
        stats["luck_points"] = stats.get("luck_points", 0) + opt_data["luck_points"]
    return msgs, True
