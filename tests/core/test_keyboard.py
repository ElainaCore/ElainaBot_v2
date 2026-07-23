"""build_keyboard / build_prompt_keyboard 单元测试"""

from unittest.mock import patch

from core.message.keyboard import build_keyboard, build_prompt_keyboard

# ==================== build_keyboard 基础结构测试 ====================


class TestBuildKeyboardBasic:
    """基本输入结构"""

    def test_empty_rows(self):
        """空按钮列表返回空 rows"""
        result = build_keyboard([])
        assert result == {'content': {'rows': []}}

    def test_empty_rows_none(self):
        """None 列表也被接受 (迭代时正常) - 实际为参数列表为空场景"""
        result = build_keyboard([])
        assert result['content']['rows'] == []

    def test_single_button_minimal(self):
        """最简按钮: 只给必要字段"""
        btn = {'text': '测试', 'data': 'test_data'}
        result = build_keyboard([[btn]])
        rows = result['content']['rows']
        assert len(rows) == 1
        buttons = rows[0]['buttons']
        assert len(buttons) == 1
        b = buttons[0]
        assert b['id'] == '0'
        assert b['render_data']['label'] == '测试'
        assert b['render_data']['visited_label'] == '测试'
        assert b['render_data']['style'] == 1
        assert b['action']['type'] == 2
        assert b['action']['data'] == 'test_data'
        assert b['action']['permission'] == {'type': 2}

    def test_single_button_all_fields(self):
        """按钮所有字段一次性覆盖"""
        btn = {
            'id': 'btn_99',
            'text': '点我',
            'show': '已点击',
            'style': 2,
            'type': 1,
            'data': 'custom_data',
            'link': 'https://example.com',
            'enter': True,
            'reply': True,
            'permission': {'type': 0, 'specify_user_ids': ['user_1']},
            'limit': 3,
            'tips': '不支持时提示',
        }
        result = build_keyboard([[btn]])
        b = result['content']['rows'][0]['buttons'][0]
        assert b['id'] == 'btn_99'
        assert b['render_data']['label'] == '点我'
        assert b['render_data']['visited_label'] == '已点击'
        assert b['render_data']['style'] == 2
        # link 覆盖 type/data
        assert b['action']['type'] == 0
        assert b['action']['data'] == 'https://example.com'
        assert b['action']['enter'] is True
        assert b['action']['reply'] is True
        assert b['action']['permission'] == {'type': 0, 'specify_user_ids': ['user_1']}
        assert b['action']['click_limit'] == 3
        assert b['action']['unsupport_tips'] == '不支持时提示'

    def test_multiple_rows(self):
        """多行按钮"""
        rows_input = [
            [{'text': 'A', 'data': 'a'}, {'text': 'B', 'data': 'b'}],
            [{'text': 'C', 'data': 'c'}],
        ]
        result = build_keyboard(rows_input)
        rows = result['content']['rows']
        assert len(rows) == 2
        assert len(rows[0]['buttons']) == 2
        assert len(rows[1]['buttons']) == 1
        assert rows[0]['buttons'][0]['render_data']['label'] == 'A'
        assert rows[1]['buttons'][0]['render_data']['label'] == 'C'

    def test_row_as_dict_buttons_key(self):
        """行以 dict 传入, 使用 buttons 键"""
        result = build_keyboard([{'buttons': [{'text': 'X', 'data': 'x'}]}])
        assert len(result['content']['rows'][0]['buttons']) == 1
        assert result['content']['rows'][0]['buttons'][0]['render_data']['label'] == 'X'

    def test_row_as_dict_btns_alias(self):
        """行以 dict 传入, 使用 btns 键 (别名)"""
        result = build_keyboard([{'btns': [{'text': 'Y', 'data': 'y'}]}])
        assert result['content']['rows'][0]['buttons'][0]['render_data']['label'] == 'Y'

    def test_row_as_dict_no_buttons_key(self):
        """行 dict 无 buttons/btns 键, 回退到空列表"""
        result = build_keyboard([{'some_other': 'value'}])
        assert result['content']['rows'][0]['buttons'] == []

    def test_row_as_dict_empty_buttons(self):
        """行 dict 的 buttons 为空列表"""
        result = build_keyboard([{'buttons': []}])
        assert result['content']['rows'][0]['buttons'] == []


# ==================== 按钮字段默认值测试 ====================


class TestBuildKeyboardDefaults:
    """字段默认值"""

    def test_id_default_auto_index(self):
        """未提供 id 时自动使用按钮索引"""
        result = build_keyboard([[{'text': 'A'}, {'text': 'B'}, {'text': 'C'}]])
        buttons = result['content']['rows'][0]['buttons']
        assert buttons[0]['id'] == '0'
        assert buttons[1]['id'] == '1'
        assert buttons[2]['id'] == '2'

    def test_style_default(self):
        """未提供 style 时默认为 1"""
        result = build_keyboard([[{'text': 'btn'}]])
        assert result['content']['rows'][0]['buttons'][0]['render_data']['style'] == 1

    def test_type_default(self):
        """未提供 type 时默认为 2 (回调)"""
        result = build_keyboard([[{'text': 'btn'}]])
        assert result['content']['rows'][0]['buttons'][0]['action']['type'] == 2

    def test_data_default_empty_string(self):
        """未提供 data 时默认为空字符串"""
        result = build_keyboard([[{'text': 'btn'}]])
        assert result['content']['rows'][0]['buttons'][0]['action']['data'] == ''

    def test_style_from_render_data_fallback(self):
        """render_data 中已有 style 时不覆盖"""
        btn = {'render_data': {'label': 'L', 'style': 3}}
        result = build_keyboard([[btn]])
        assert result['content']['rows'][0]['buttons'][0]['render_data']['style'] == 3

    def test_type_from_action_fallback(self):
        """action 中已有 type 时不覆盖"""
        btn = {'action': {'type': 99, 'data': 'd'}, 'text': 'btn'}
        result = build_keyboard([[btn]])
        assert result['content']['rows'][0]['buttons'][0]['action']['type'] == 99


# ==================== text / show 渲染测试 ====================


class TestBuildKeyboardTextShow:
    """label / visited_label 的 text 与 show 覆盖逻辑"""

    def test_text_sets_label_and_visited_label(self):
        """text 同时设置 label 和 visited_label"""
        btn = {'text': '我的按钮'}
        result = build_keyboard([[btn]])
        b = result['content']['rows'][0]['buttons'][0]
        assert b['render_data']['label'] == '我的按钮'
        assert b['render_data']['visited_label'] == '我的按钮'

    def test_show_only_sets_visited_label(self):
        """show 仅设置 visited_label, 不设置 label"""
        btn = {'text': '点击', 'show': '已点击过'}
        result = build_keyboard([[btn]])
        b = result['content']['rows'][0]['buttons'][0]
        assert b['render_data']['label'] == '点击'
        assert b['render_data']['visited_label'] == '已点击过'

    def test_show_without_text(self):
        """只有 show 没有 text: visited_label=show, label 保持 render_data 原值"""
        btn = {'show': 'visited_only'}
        result = build_keyboard([[btn]])
        b = result['content']['rows'][0]['buttons'][0]
        # label 未被设置 (render_data 中也没有)
        assert 'label' not in b['render_data']
        assert b['render_data']['visited_label'] == 'visited_only'

    def test_render_data_has_label_not_overwritten_by_text(self):
        """render_data 已设置 label 时, text 覆盖之"""
        btn = {'render_data': {'label': '原始标签'}, 'text': '新标签'}
        result = build_keyboard([[btn]])
        b = result['content']['rows'][0]['buttons'][0]
        assert b['render_data']['label'] == '新标签'  # text 覆盖

    def test_show_preserves_existing_visited_label(self):
        """render_data 已设置 visited_label 时, show 会用 setdefault 不覆盖"""
        btn = {
            'render_data': {'label': 'L', 'visited_label': '原始'},
            'text': '新标签',
            'show': '新visited',
        }
        result = build_keyboard([[btn]])
        b = result['content']['rows'][0]['buttons'][0]
        assert b['render_data']['label'] == '新标签'  # text 覆盖 → r_data['label'] = text
        # show 用的是 setdefault → 已有 visited_label '原始' 不会被覆盖
        assert b['render_data']['visited_label'] == '原始'


# ==================== link 测试 ====================


class TestBuildKeyboardLink:
    """link 字段覆盖 type/data"""

    def test_link_overrides_type_and_data(self):
        """link 存在时 type=0, data=link"""
        btn = {'text': '链接', 'type': 2, 'data': 'old', 'link': 'https://qq.com'}
        result = build_keyboard([[btn]])
        b = result['content']['rows'][0]['buttons'][0]
        assert b['action']['type'] == 0
        assert b['action']['data'] == 'https://qq.com'

    def test_link_empty_string(self):
        """link 为空字符串也会覆盖"""
        btn = {'text': 'btn', 'data': 'original', 'link': ''}
        result = build_keyboard([[btn]])
        b = result['content']['rows'][0]['buttons'][0]
        assert b['action']['type'] == 0
        assert b['action']['data'] == ''

    def test_no_link_preserves_type_data(self):
        """无 link 保持原有 type/data"""
        btn = {'text': 'btn', 'type': 2, 'data': 'my_data'}
        result = build_keyboard([[btn]])
        b = result['content']['rows'][0]['buttons'][0]
        assert b['action']['type'] == 2
        assert b['action']['data'] == 'my_data'


# ==================== enter 行为测试 ====================


class TestBuildKeyboardEnter:
    """enter 字段与 button_enter_to_send 交互"""

    @patch('core.message.keyboard.cfg')
    def test_enter_with_button_enter_to_send_disabled(self, mock_cfg):
        """button_enter_to_send=False: type=2 的按钮 enter → action.enter=True"""
        mock_cfg.get_bot_setting.return_value = False
        btn = {'text': 'btn', 'type': 2, 'data': 'd', 'enter': True}
        result = build_keyboard([[btn]], appid='test')
        b = result['content']['rows'][0]['buttons'][0]
        assert b['action']['type'] == 2
        assert b['action']['enter'] is True

    @patch('core.message.keyboard.cfg')
    def test_enter_with_button_enter_to_send_enabled(self, mock_cfg):
        """button_enter_to_send=True: type=2+enter → type=1"""
        mock_cfg.get_bot_setting.return_value = True
        btn = {'text': 'btn', 'type': 2, 'data': 'd', 'enter': True}
        result = build_keyboard([[btn]], appid='test')
        b = result['content']['rows'][0]['buttons'][0]
        assert b['action']['type'] == 1
        assert 'enter' not in b['action']

    @patch('core.message.keyboard.cfg')
    def test_enter_type_not_2_with_enter_to_send(self, mock_cfg):
        """button_enter_to_send=True 但 type!=2 → 保持 type, 设 enter=True"""
        mock_cfg.get_bot_setting.return_value = True
        btn = {'text': 'btn', 'type': 99, 'data': 'd', 'enter': True}
        result = build_keyboard([[btn]], appid='test')
        b = result['content']['rows'][0]['buttons'][0]
        assert b['action']['type'] == 99
        assert b['action']['enter'] is True

    @patch('core.message.keyboard.cfg')
    def test_no_enter_key(self, mock_cfg):
        """按钮无 enter 字段, 不设置 enter, type 不变"""
        mock_cfg.get_bot_setting.return_value = False
        btn = {'text': 'btn', 'type': 2, 'data': 'd'}
        result = build_keyboard([[btn]], appid='test')
        b = result['content']['rows'][0]['buttons'][0]
        assert 'enter' not in b['action']
        assert b['action']['type'] == 2

    def test_appid_none_defaults_to_false(self):
        """appid=None 时 button_enter_to_send 默认为 False, enter → action.enter=True"""
        btn = {'text': 'btn', 'type': 2, 'data': 'd', 'enter': True}
        result = build_keyboard([[btn]])
        b = result['content']['rows'][0]['buttons'][0]
        assert b['action']['type'] == 2  # type 不变 (button_enter_to_send=False)
        assert b['action']['enter'] is True


# ==================== reply 测试 ====================


class TestBuildKeyboardReply:
    """reply 字段"""

    def test_reply_true(self):
        btn = {'text': 'btn', 'data': 'd', 'reply': True}
        result = build_keyboard([[btn]])
        assert result['content']['rows'][0]['buttons'][0]['action']['reply'] is True

    def test_reply_false_ignored(self):
        """reply=False 是 falsy, 不会添加 reply 键 (代码逻辑: if btn.get('reply'))"""
        btn = {'text': 'btn', 'data': 'd', 'reply': False}
        result = build_keyboard([[btn]])
        assert 'reply' not in result['content']['rows'][0]['buttons'][0]['action']

    def test_reply_default_not_set(self):
        """无 reply 字段时 action 中不包含 reply 键"""
        btn = {'text': 'btn', 'data': 'd'}
        result = build_keyboard([[btn]])
        assert 'reply' not in result['content']['rows'][0]['buttons'][0]['action']


# ==================== permission 测试 ====================


class TestBuildKeyboardPermission:
    """5 种权限模式"""

    def test_permission_explicit(self):
        """显式 permission 最高优先级"""
        btn = {
            'text': 'btn',
            'permission': {'type': 5, 'custom': 'val'},
            'role': ['r1'],
            'list': ['u1'],
            'admin': True,
        }
        result = build_keyboard([[btn]])
        assert result['content']['rows'][0]['buttons'][0]['action']['permission'] == {
            'type': 5,
            'custom': 'val',
        }

    def test_permission_role(self):
        """role → type=3, specify_role_ids"""
        btn = {'text': 'btn', 'role': ['role_a', 'role_b']}
        result = build_keyboard([[btn]])
        p = result['content']['rows'][0]['buttons'][0]['action']['permission']
        assert p == {'type': 3, 'specify_role_ids': ['role_a', 'role_b']}

    def test_permission_list(self):
        """list → type=0, specify_user_ids"""
        btn = {'text': 'btn', 'list': ['user_a', 'user_b']}
        result = build_keyboard([[btn]])
        p = result['content']['rows'][0]['buttons'][0]['action']['permission']
        assert p == {'type': 0, 'specify_user_ids': ['user_a', 'user_b']}

    def test_permission_admin(self):
        """admin → type=1"""
        btn = {'text': 'btn', 'admin': True}
        result = build_keyboard([[btn]])
        p = result['content']['rows'][0]['buttons'][0]['action']['permission']
        assert p == {'type': 1}

    def test_permission_admin_false(self):
        """admin=False 不前置于默认值, 仍落入默认 type=2"""
        btn = {'text': 'btn', 'admin': False}
        result = build_keyboard([[btn]])
        p = result['content']['rows'][0]['buttons'][0]['action']['permission']
        assert p == {'type': 2}

    def test_permission_default(self):
        """无任何权限字段 → type=2 所有人"""
        btn = {'text': 'btn'}
        result = build_keyboard([[btn]])
        p = result['content']['rows'][0]['buttons'][0]['action']['permission']
        assert p == {'type': 2}

    def test_permission_role_over_list(self):
        """role 优先级高于 list (代码中 elif 顺序)"""
        btn = {'text': 'btn', 'role': ['r1'], 'list': ['u1']}
        result = build_keyboard([[btn]])
        p = result['content']['rows'][0]['buttons'][0]['action']['permission']
        assert p == {'type': 3, 'specify_role_ids': ['r1']}

    def test_permission_list_over_admin(self):
        """list 优先级高于 admin"""
        btn = {'text': 'btn', 'list': ['u1'], 'admin': True}
        result = build_keyboard([[btn]])
        p = result['content']['rows'][0]['buttons'][0]['action']['permission']
        assert p == {'type': 0, 'specify_user_ids': ['u1']}


# ==================== limit / tips 测试 ====================


class TestBuildKeyboardLimitTips:
    """点击次数限制和不支持提示"""

    def test_click_limit(self):
        btn = {'text': 'btn', 'limit': 5}
        result = build_keyboard([[btn]])
        assert result['content']['rows'][0]['buttons'][0]['action']['click_limit'] == 5

    def test_click_limit_zero(self):
        """limit=0 也设置"""
        btn = {'text': 'btn', 'limit': 0}
        result = build_keyboard([[btn]])
        assert result['content']['rows'][0]['buttons'][0]['action']['click_limit'] == 0

    def test_no_limit(self):
        """无 limit 字段时不包含 click_limit 键"""
        btn = {'text': 'btn'}
        result = build_keyboard([[btn]])
        assert 'click_limit' not in result['content']['rows'][0]['buttons'][0]['action']

    def test_unsupport_tips(self):
        btn = {'text': 'btn', 'tips': '当前版本不支持此功能'}
        result = build_keyboard([[btn]])
        assert (
            result['content']['rows'][0]['buttons'][0]['action']['unsupport_tips']
            == '当前版本不支持此功能'
        )

    def test_no_tips(self):
        """无 tips 字段时不包含 unsupport_tips 键"""
        btn = {'text': 'btn'}
        result = build_keyboard([[btn]])
        assert 'unsupport_tips' not in result['content']['rows'][0]['buttons'][0]['action']


# ==================== modal / subscribe 测试 ====================


class TestBuildKeyboardModalSubscribe:
    """二次确认弹窗和订阅按钮"""

    def test_modal_and_subscribe(self):
        """subscribe 自动 type=4 并生成 subscribe_data (含 '_' 为自定义模板), modal 任意按钮可用"""
        btns = [[
            {'text': '订阅', 'subscribe': ['12345', '102722993_1769091467'],
             'modal': {'content': '确认订阅？', 'confirm_text': '✔️确认', 'cancel_text': '❌取消'}},
            {'text': '回调', 'data': 'cb', 'type': 1, 'modal': '确认执行？'},
            {'text': '原生', 'subscribe_data': {'template_ids': [{'template_id': '999'}]}},
        ]]
        sub_btn, cb_btn, raw_btn = build_keyboard(btns)['content']['rows'][0]['buttons']
        assert sub_btn['action']['type'] == 4
        assert sub_btn['action']['subscribe_data'] == {
            'template_ids': [
                {'template_id': '12345'},
                {'custom_template_id': '102722993_1769091467'},
            ]
        }
        assert sub_btn['action']['modal']['confirm_text'] == '✔️确认'
        # 字符串 modal 转 {'content': ...}, 不影响按钮类型
        assert cb_btn['action']['modal'] == {'content': '确认执行？'}
        assert cb_btn['action']['type'] == 1
        assert 'subscribe_data' not in cb_btn['action']
        # 平台原生字段直接写在 btn 里原样透传, subscribe_data 自动 type=4
        assert raw_btn['action']['type'] == 4
        assert raw_btn['action']['subscribe_data'] == {'template_ids': [{'template_id': '999'}]}


# ==================== 综合场景测试 ====================


class TestBuildKeyboardIntegration:
    """模拟真实使用场景"""

    def test_admin_menu_with_back_button(self):
        """管理员菜单: 多个按钮 + 返回按钮"""
        button_rows = [
            [
                {'text': '用户管理', 'data': '/admin users', 'style': 1, 'admin': True},
                {'text': '插件管理', 'data': '/admin plugins', 'style': 1, 'admin': True},
                {'text': '系统状态', 'data': '/admin status', 'style': 1, 'admin': True},
            ],
            [{'text': '返回', 'data': '/menu', 'style': 2}],
        ]
        result = build_keyboard(button_rows)
        rows = result['content']['rows']
        # 第一行 3 个管理按钮, 均为 admin 权限
        admin_row = rows[0]['buttons']
        assert len(admin_row) == 3
        for b in admin_row:
            assert b['action']['permission'] == {'type': 1}
        # 第二行 1 个普通按钮
        back_row = rows[1]['buttons']
        assert len(back_row) == 1
        assert back_row[0]['action']['permission'] == {'type': 2}
        assert back_row[0]['render_data']['label'] == '返回'

    def test_role_based_action_buttons(self):
        """按角色分配权限的按钮"""
        button_rows = [
            [
                {'text': '踢人', 'data': '/kick', 'role': ['admin', 'moderator']},
                {'text': '禁言', 'data': '/mute', 'role': ['admin']},
                {'text': '举报', 'data': '/report', 'list': []},
            ],
        ]
        result = build_keyboard(button_rows)
        buttons = result['content']['rows'][0]['buttons']
        assert buttons[0]['action']['permission'] == {
            'type': 3,
            'specify_role_ids': ['admin', 'moderator'],
        }
        assert buttons[1]['action']['permission'] == {'type': 3, 'specify_role_ids': ['admin']}
        # list=[] → type=0, specify_user_ids=[]
        assert buttons[2]['action']['permission'] == {'type': 0, 'specify_user_ids': []}

    def test_link_buttons_with_tips(self):
        """链接按钮 + 点击次数限制 + 不支持提示"""
        button_rows = [
            [
                {
                    'text': '官网',
                    'link': 'https://example.com',
                    'tips': '请在客户端打开',
                    'limit': 10,
                },
                {
                    'text': 'GitHub',
                    'link': 'https://github.com',
                },
            ],
        ]
        result = build_keyboard(button_rows)
        buttons = result['content']['rows'][0]['buttons']
        # 第一个按钮
        b1 = buttons[0]
        assert b1['action']['type'] == 0
        assert b1['action']['data'] == 'https://example.com'
        assert b1['action']['click_limit'] == 10
        assert b1['action']['unsupport_tips'] == '请在客户端打开'
        # 第二个按钮
        b2 = buttons[1]
        assert b2['action']['type'] == 0
        assert b2['action']['data'] == 'https://github.com'

    def test_enter_buttons_with_config(self):
        """enter 按钮在 button_enter_to_send=True 时的行为"""
        with patch('core.message.keyboard.cfg') as mock_cfg:
            mock_cfg.get_bot_setting.return_value = True
            button_rows = [
                [
                    {'text': '下一页', 'data': '/next', 'enter': True},
                    {'text': '上一页', 'data': '/prev', 'enter': True},
                ],
            ]
            result = build_keyboard(button_rows, appid='mybot')
            buttons = result['content']['rows'][0]['buttons']
            assert buttons[0]['action']['type'] == 1  # 自动发送
            assert buttons[1]['action']['type'] == 1

    def test_row_dict_format_from_config(self):
        """模拟从 YAML 配置读取的 dict 行格式"""
        button_rows = [
            {'buttons': [{'text': '选项A', 'data': 'a'}, {'text': '选项B', 'data': 'b'}]},
            {'buttons': [{'text': '取消', 'data': 'cancel', 'style': 2}]},
        ]
        result = build_keyboard(button_rows)
        rows = result['content']['rows']
        assert len(rows) == 2
        assert rows[0]['buttons'][0]['render_data']['label'] == '选项A'
        assert rows[1]['buttons'][0]['render_data']['label'] == '取消'


# ==================== build_prompt_keyboard 测试 ====================


class TestBuildPromptKeyboard:
    """prompt_keyboard 扩展按钮"""

    def test_empty_input(self):
        assert build_prompt_keyboard(None) is None
        assert build_prompt_keyboard([]) is None
        assert build_prompt_keyboard(()) is None

    def test_dict_input(self):
        """dict 输入直接包装返回"""
        result = build_prompt_keyboard({'keyboard': {'content': {'rows': []}}})
        assert result == {'keyboard': {'keyboard': {'content': {'rows': []}}}}

    def test_single_string(self):
        """单个字符串, 自动补全为按钮"""
        result = build_prompt_keyboard(['选项1'])
        keyboard = result['keyboard']
        rows = keyboard['content']['rows']
        assert len(rows) == 1
        btn = rows[0]['buttons'][0]
        assert btn['id'] == '1'
        assert btn['render_data']['label'] == '选项1'
        assert btn['render_data']['visited_label'] == '选项1'
        assert btn['render_data']['style'] == 1
        assert btn['action']['type'] == 2
        assert btn['action']['data'] == 'elaina'
        assert btn['action']['enter'] is True

    def test_multiple_strings_max_3(self):
        """最多 3 个按钮"""
        result = build_prompt_keyboard(['A', 'B', 'C', 'D', 'E'])
        rows = result['keyboard']['content']['rows']
        assert len(rows[0]['buttons']) == 3
        assert rows[0]['buttons'][0]['render_data']['label'] == 'A'
        assert rows[0]['buttons'][1]['render_data']['label'] == 'B'
        assert rows[0]['buttons'][2]['render_data']['label'] == 'C'

    def test_list_tuple_items_with_style(self):
        """list/tuple 格式: [label, style]"""
        result = build_prompt_keyboard([('选项A', 2), ['选项B', 3]])
        buttons = result['keyboard']['content']['rows'][0]['buttons']
        assert buttons[0]['render_data']['label'] == '选项A'
        assert buttons[0]['render_data']['style'] == 2
        assert buttons[1]['render_data']['label'] == '选项B'
        assert buttons[1]['render_data']['style'] == 3

    def test_list_item_single_element(self):
        """list 只有一个元素的格式: [label]"""
        result = build_prompt_keyboard([('选项A',)])
        btn = result['keyboard']['content']['rows'][0]['buttons'][0]
        assert btn['render_data']['label'] == '选项A'
        assert btn['render_data']['style'] == 1  # 默认值

    def test_dict_button_items(self):
        """dict 格式按钮: 仅自动补全 id 和 action, 不经过 render_data 加工"""
        result = build_prompt_keyboard([{'text': '自定义', 'data': 'custom_data', 'style': 3}])
        btn = result['keyboard']['content']['rows'][0]['buttons'][0]
        # dict 格式保持原始结构, 仅 setdefault('id') 和 setdefault('action')
        assert btn['id'] == '1'
        assert btn['text'] == '自定义'
        assert btn['data'] == 'custom_data'
        assert btn['style'] == 3
        assert btn['action']['type'] == 2
        assert btn['action']['data'] == 'elaina'
        assert btn['action']['enter'] is True

    def test_mixed_formats(self):
        """混合格式输入: dict 格式不经过 render_data 加工"""
        inputs = [
            '字符串按钮',
            ('元组格式', 2),
            {'text': '字典格式', 'data': 'dict_data', 'style': 3},
        ]
        result = build_prompt_keyboard(inputs)
        buttons = result['keyboard']['content']['rows'][0]['buttons']
        assert len(buttons) == 3
        assert buttons[0]['render_data']['label'] == '字符串按钮'
        assert buttons[1]['render_data']['label'] == '元组格式'
        # dict 格式: text 保持在顶层, 不转为 render_data
        assert buttons[2]['text'] == '字典格式'
        assert buttons[2]['data'] == 'dict_data'
        assert buttons[2]['style'] == 3

    def test_empty_list_in_mixed(self):
        """空 list 项匹配 isinstance(btn, list) → 创建空 label 按钮 (不跳过)"""
        result = build_prompt_keyboard(['A', [], 'B'])
        buttons = result['keyboard']['content']['rows'][0]['buttons']
        # 空 list [] 也会产生一个 label='' 的按钮
        assert len(buttons) == 3
        assert buttons[0]['render_data']['label'] == 'A'
        assert buttons[1]['render_data']['label'] == ''
        assert buttons[2]['render_data']['label'] == 'B'

    def test_prompt_buttons_plain_value(self):
        """非列表/元组/字典的字符串直接包装"""
        result = build_prompt_keyboard('单一字符串')
        keyboard = result['keyboard']
        rows = keyboard['content']['rows']
        btn = rows[0]['buttons'][0]
        assert btn['render_data']['label'] == '单一字符串'
