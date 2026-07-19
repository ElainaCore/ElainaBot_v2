"""DAU 统计图片渲染 (PIL)"""

import os
from io import BytesIO

# ==================== 配色 ====================
_BG_TOP = (11, 92, 255)       # 头图渐变起
_BG_BOTTOM = (77, 141, 255)   # 头图渐变止
_PAGE_BG = (243, 245, 249)
_CARD = (255, 255, 255)
_TEXT = (31, 35, 41)
_TEXT_SECONDARY = (130, 138, 153)
_TEXT_TERTIARY = (166, 173, 184)
_UP = (227, 77, 89)
_UP_BG = (253, 236, 238)
_DOWN = (0, 168, 112)
_DOWN_BG = (227, 249, 240)
_FLAT = (130, 138, 153)
_FLAT_BG = (240, 241, 245)
_BAR_BG = (237, 241, 247)
_SHADOW = (23, 43, 99)

_ACCENTS = (
    ((11, 92, 255), (232, 240, 255)),    # 蓝
    ((0, 168, 112), (227, 249, 240)),    # 绿
    ((250, 140, 22), (255, 243, 230)),   # 橙
    ((114, 46, 209), (243, 235, 255)),   # 紫
)
_RANK_COLORS = ((255, 172, 20), (160, 174, 192), (219, 154, 108))  # 金银铜

# 自定义字体目录: 放置任意 ttf/ttc/otf 中文字体即可优先使用 (bold 文件名含 bold/bd 时作为粗体)
_CUSTOM_FONT_DIR = os.path.join(os.path.dirname(__file__), 'fonts')

_FONT_PATHS = [
    '/usr/share/fonts/truetype/msyh.ttc',
    'C:/Windows/Fonts/msyh.ttc',
    'C:/Windows/Fonts/msyh.ttf',
    'C:/Windows/Fonts/simhei.ttf',
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
    '/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc',
    '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
    '/System/Library/Fonts/PingFang.ttc',
]
_FONT_BOLD_PATHS = [
    '/usr/share/fonts/truetype/msyhbd.ttc',
    'C:/Windows/Fonts/msyhbd.ttc',
    'C:/Windows/Fonts/msyhbd.ttf',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc',
]

# 文件名含这些关键字的字体视为中文字体
_CJK_NAME_HINTS = (
    'cjk', 'wqy', 'msyh', 'yahei', 'simhei', 'simsun', 'pingfang',
    'sourcehan', 'source-han', 'notosanssc', 'notoserifsc', 'sarasa',
    'harmonyos', 'alibaba', 'fallback', 'uming', 'ukai', 'zenhei',
)

_font_file = None
_font_bold_file = None


def _scan_font_dirs():
    """递归扫描字体目录, 返回第一个疑似中文字体"""
    dirs = [
        _CUSTOM_FONT_DIR,
        '/usr/share/fonts',
        '/usr/local/share/fonts',
        os.path.expanduser('~/.fonts'),
        os.path.expanduser('~/.local/share/fonts'),
    ]
    for base in dirs:
        if not os.path.isdir(base):
            continue
        is_custom = base == _CUSTOM_FONT_DIR
        for root, _dirs, files in os.walk(base):
            for name in sorted(files):
                low = name.lower()
                if not low.endswith(('.ttf', '.ttc', '.otf')):
                    continue
                if is_custom or any(h in low for h in _CJK_NAME_HINTS):
                    return os.path.join(root, name)
    return ''


def _find_font():
    """定位中文字体; 找不到返回 '' (调用方应回退文本)"""
    global _font_file, _font_bold_file
    if _font_file is not None:
        return _font_file
    found = next((p for p in _FONT_PATHS if os.path.isfile(p)), '') or _scan_font_dirs()
    _font_file = found
    bold = next((p for p in _FONT_BOLD_PATHS if os.path.isfile(p)), '')
    if not bold and found:
        d, base = os.path.split(found)
        low = base.lower()
        for cand in os.listdir(d):
            cl = cand.lower()
            if cl != low and ('bold' in cl or 'bd' in cl) and cl.endswith(('.ttf', '.ttc', '.otf')):
                bold = os.path.join(d, cand)
                break
    _font_bold_file = bold or found
    return _font_file


_font_cache = {}


def _font(size, bold=False):
    from PIL import ImageFont

    key = (size, bold)
    f = _font_cache.get(key)
    if f is None:
        f = ImageFont.truetype(_font_bold_file if bold else _font_file, size)
        _font_cache[key] = f
    return f


def _fmt_num(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    return f'{n:,}'


def _mask_id(s, n=3):
    s = str(s)
    return s if len(s) <= n * 2 else f'{s[:n]}****{s[-n:]}'


def _text_w(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _card_shadow(img, box, radius):
    """卡片柔和投影"""
    from PIL import Image, ImageDraw, ImageFilter

    x0, y0, x1, y1 = box
    pad = 40
    layer = Image.new('RGBA', (x1 - x0 + pad * 2, y1 - y0 + pad * 2), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rounded_rectangle(
        (pad, pad + 6, pad + (x1 - x0), pad + 6 + (y1 - y0)),
        radius=radius, fill=(*_SHADOW, 26),
    )
    layer = layer.filter(ImageFilter.GaussianBlur(12))
    img.paste(layer, (x0 - pad, y0 - pad), layer)


def _card(img, d, box, radius=20):
    _card_shadow(img, box, radius)
    d.rounded_rectangle(box, radius=radius, fill=_CARD)


def _delta_pill(d, x, y, diff, h=40):
    """涨跌胶囊, 返回宽度"""
    if diff is None:
        return 0
    if diff > 0:
        txt, fg, bg = f'↑ {_fmt_num(diff)}', _UP, _UP_BG
    elif diff < 0:
        txt, fg, bg = f'↓ {_fmt_num(abs(diff))}', _DOWN, _DOWN_BG
    else:
        txt, fg, bg = '· 0', _FLAT, _FLAT_BG
    f = _font(24, bold=True)
    w = _text_w(d, txt, f) + 28
    d.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=bg)
    d.text((x + 14, y + (h - 24) // 2 - 4), txt, font=f, fill=fg)
    return w


def render_dau_image(stats, title, sub_title='', y_stats=None, elapsed_ms=None):
    """渲染 DAU 统计卡片, 返回 PNG bytes; PIL 或中文字体不可用时返回 None"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    if not _find_font():
        return None

    width = 1000
    pad = 48
    gap = 28

    top_groups = (stats.get('top_groups') or [])[:3]
    top_users = (stats.get('top_users') or [])[:3]
    list_rows = max(len(top_groups), len(top_users), 1)

    header_h = 300
    metric_h = 190
    small_h = 150
    rank_h = 104 + list_rows * 84
    has_lifecycle = 'group_join' in stats
    small_rows = 2 if has_lifecycle else 1
    # 内容底部 = 卡片起点(header_h-60) + 指标区 + 小卡区 + 排行区 + 页脚
    height = (header_h - 60) + (metric_h * 2 + gap * 2 + 22) \
        + (small_h + gap) * small_rows + 14 + (rank_h + 40) + 70

    img = Image.new('RGB', (width, height), _PAGE_BG)
    d = ImageDraw.Draw(img)

    # ===== 渐变头图 =====
    for i in range(header_h):
        t = i / header_h
        r = int(_BG_TOP[0] + (_BG_BOTTOM[0] - _BG_TOP[0]) * t)
        g = int(_BG_TOP[1] + (_BG_BOTTOM[1] - _BG_TOP[1]) * t)
        b = int(_BG_TOP[2] + (_BG_BOTTOM[2] - _BG_TOP[2]) * t)
        d.line([(0, i), (width, i)], fill=(r, g, b))
    # 装饰圆
    deco = Image.new('RGBA', (width, header_h), (0, 0, 0, 0))
    dd = ImageDraw.Draw(deco)
    dd.ellipse((width - 320, -160, width + 80, 240), fill=(255, 255, 255, 18))
    dd.ellipse((width - 180, 60, width + 140, 380), fill=(255, 255, 255, 14))
    dd.ellipse((-120, 140, 200, 460), fill=(255, 255, 255, 12))
    img.paste(deco, (0, 0), deco)

    d.text((pad, 64), title, font=_font(52, bold=True), fill=(255, 255, 255))
    if sub_title:
        stf = _font(26)
        stw = _text_w(d, sub_title, stf) + 36
        d.rounded_rectangle((pad, 140, pad + stw, 188), radius=24, fill=(63, 122, 245))
        d.text((pad + 18, 148), sub_title, font=stf, fill=(224, 235, 255))
    wm_font = _font(22, bold=True)
    wm = 'DATA DASHBOARD'
    d.text((width - pad - _text_w(d, wm, wm_font), 80), wm, font=wm_font, fill=(173, 200, 252))

    y = header_h - 60

    # ===== 指标卡 2x2 (悬浮于头图) =====
    def _y(key):
        return y_stats.get(key) if y_stats else None

    metrics = [
        ('活跃用户数', stats.get('users', 0), _y('users')),
        ('活跃群聊数', stats.get('groups_', 0), _y('groups_')),
        ('上行消息数', stats.get('received', 0), _y('received')),
        ('下行消息数', stats.get('sent', 0), _y('sent')),
    ]
    card_w = (width - pad * 2 - gap) // 2
    for i, (label, val, y_val) in enumerate(metrics):
        cx = pad + (i % 2) * (card_w + gap)
        cy = y + (i // 2) * (metric_h + gap)
        _card(img, d, (cx, cy, cx + card_w, cy + metric_h))
        accent_fg, accent_bg = _ACCENTS[i]
        # 图标块
        ix, iy = cx + 32, cy + 32
        d.rounded_rectangle((ix, iy, ix + 56, iy + 56), radius=16, fill=accent_bg)
        if i == 0:  # 单人: 头+肩
            d.ellipse((ix + 20, iy + 11, ix + 36, iy + 27), fill=accent_fg)
            d.pieslice((ix + 12, iy + 29, ix + 44, iy + 59), 180, 360, fill=accent_fg)
        elif i == 1:  # 双人: 两组头+肩
            d.ellipse((ix + 12, iy + 14, ix + 25, iy + 27), fill=accent_fg)
            d.pieslice((ix + 6, iy + 29, ix + 31, iy + 53), 180, 360, fill=accent_fg)
            d.ellipse((ix + 32, iy + 14, ix + 45, iy + 27), fill=accent_fg)
            d.pieslice((ix + 26, iy + 29, ix + 51, iy + 53), 180, 360, fill=accent_fg)
        elif i == 2:  # 上行: 向上箭头
            d.polygon([(ix + 28, iy + 11), (ix + 42, iy + 27), (ix + 14, iy + 27)], fill=accent_fg)
            d.rounded_rectangle((ix + 23, iy + 26, ix + 33, iy + 45), radius=3, fill=accent_fg)
        else:  # 下行: 向下箭头
            d.rounded_rectangle((ix + 23, iy + 11, ix + 33, iy + 30), radius=3, fill=accent_fg)
            d.polygon([(ix + 28, iy + 45), (ix + 42, iy + 29), (ix + 14, iy + 29)], fill=accent_fg)
        d.text((cx + 108, cy + 34), label, font=_font(26), fill=_TEXT_SECONDARY)
        num_font = _font(56, bold=True)
        d.text((cx + 108, cy + 72), _fmt_num(val), font=num_font, fill=_TEXT)
        if y_val is not None:
            diff = int(val) - int(y_val)
            pw = _delta_pill(d, -1000, -1000, diff)  # 预算宽度
            _delta_pill(d, cx + card_w - 32 - pw, cy + 32, diff)

    y += metric_h * 2 + gap * 2 + 22

    # ===== 小卡: 私聊/最活跃时段 (+可选 加群/退群) =====
    small_cards = [
        ('私聊消息', _fmt_num(stats.get('private', 0)),
         _y('private'), stats.get('private', 0), None, 'chat', ((0, 153, 214), (227, 244, 253))),
        ('最活跃时段', f"{stats.get('peak_hour', 0)}:00",
         None, None, f"{_fmt_num(stats.get('peak_hour_count', 0))}条", 'clock', ((233, 84, 152), (253, 233, 242))),
    ]
    if has_lifecycle:
        small_cards += [
            ('今日加群', _fmt_num(stats.get('group_join', 0)),
             None, None, None, 'plus', ((0, 168, 112), (227, 249, 240))),
            ('今日退群', _fmt_num(stats.get('group_leave', 0)),
             None, None, None, 'minus', ((227, 77, 89), (253, 236, 238))),
        ]
    for i, (label, val, y_val, raw, extra, icon, (icon_fg, icon_bg)) in enumerate(small_cards):
        cx = pad + (i % 2) * (card_w + gap)
        cy = y + (i // 2) * (small_h + gap)
        _card(img, d, (cx, cy, cx + card_w, cy + small_h))
        # 图标块
        icx, icy = cx + 32, cy + (small_h - 56) // 2
        d.rounded_rectangle((icx, icy, icx + 56, icy + 56), radius=16, fill=icon_bg)
        if icon == 'chat':  # 聊天气泡
            d.rounded_rectangle((icx + 14, icy + 16, icx + 42, icy + 36), radius=8, fill=icon_fg)
            d.polygon([(icx + 20, icy + 34), (icx + 28, icy + 34), (icx + 18, icy + 42)], fill=icon_fg)
        elif icon == 'clock':  # 时钟
            d.ellipse((icx + 13, icy + 13, icx + 43, icy + 43), outline=icon_fg, width=4)
            d.line([(icx + 28, icy + 21), (icx + 28, icy + 29), (icx + 35, icy + 32)], fill=icon_fg, width=4)
        elif icon == 'plus':  # 加号
            d.rounded_rectangle((icx + 25, icy + 13, icx + 31, icy + 43), radius=3, fill=icon_fg)
            d.rounded_rectangle((icx + 13, icy + 25, icx + 43, icy + 31), radius=3, fill=icon_fg)
        else:  # 减号
            d.rounded_rectangle((icx + 13, icy + 25, icx + 43, icy + 31), radius=3, fill=icon_fg)
        d.text((cx + 108, cy + 26), label, font=_font(24), fill=_TEXT_SECONDARY)
        vf = _font(44, bold=True)
        d.text((cx + 108, cy + 62), val, font=vf, fill=_TEXT)
        vw = _text_w(d, val, vf)
        if y_val is not None and raw is not None:
            _delta_pill(d, cx + 108 + vw + 20, cy + 74, int(raw) - int(y_val))
        elif extra:
            d.text((cx + 108 + vw + 20, cy + 82), extra, font=_font(24), fill=_TEXT_TERTIARY)

    y += (small_h + gap) * small_rows + 14

    # ===== 排行榜 =====
    for i, (label, items, key) in enumerate(
        (('最活跃群组', top_groups, 'group_id'), ('最活跃用户', top_users, 'user_id'))
    ):
        cx = pad + i * (card_w + gap)
        _card(img, d, (cx, y, cx + card_w, y + rank_h))
        accent_fg, _bg = _ACCENTS[i]
        d.rounded_rectangle((cx + 32, y + 32, cx + 40, y + 62), radius=4, fill=accent_fg)
        d.text((cx + 56, y + 28), label, font=_font(28, bold=True), fill=_TEXT)
        if not items:
            d.text((cx + 32, y + 100), '暂无数据', font=_font(24), fill=_TEXT_TERTIARY)
            continue
        max_c = max(it.get('c', it.get('message_count', 0)) or 1 for it in items)
        for j, it in enumerate(items):
            ry = y + 98 + j * 84
            cnt = it.get('c', it.get('message_count', 0))
            rc = _RANK_COLORS[min(j, 2)]
            # 名次圆形奖牌
            d.ellipse((cx + 32, ry, cx + 76, ry + 44), fill=rc)
            rf = _font(26, bold=True)
            d.text(
                (cx + 32 + (44 - _text_w(d, str(j + 1), rf)) // 2, ry + 2),
                str(j + 1), font=rf, fill=(255, 255, 255),
            )
            d.text((cx + 96, ry - 2), _mask_id(it.get(key, '')), font=_font(26), fill=_TEXT)
            cf = _font(24, bold=True)
            cnt_txt = f'{_fmt_num(cnt)}条'
            d.text(
                (cx + card_w - 32 - _text_w(d, cnt_txt, cf), ry),
                cnt_txt, font=cf, fill=accent_fg,
            )
            # 比例条
            bar_x, bar_y = cx + 96, ry + 44
            bar_w = card_w - 96 - 32
            d.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + 12), radius=6, fill=_BAR_BG)
            fill_w = max(12, int(bar_w * (cnt / max_c)))
            d.rounded_rectangle(
                (bar_x, bar_y, bar_x + fill_w, bar_y + 12), radius=6, fill=accent_fg)

    y += rank_h + 40

    # ===== 底部 =====
    footer = 'ElainaBot · 数据统计'
    if elapsed_ms is not None:
        footer += f' · 查询耗时 {_fmt_num(elapsed_ms)}ms'
    ff = _font(22)
    d.text(((width - _text_w(d, footer, ff)) // 2, y), footer, font=ff, fill=_TEXT_TERTIARY)

    buf = BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()
