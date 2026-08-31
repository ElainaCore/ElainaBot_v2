from core.message.keyboard import build_keyboard
from core.message.template import TemplateEngine


def test_build_keyboard_moves_simplified_group_id_to_final_button_root():
    payload = build_keyboard(
        {
            'rows': [
                [
                    {
                        'id': 'b_up',
                        'text': '赞👍',
                        'type': 1,
                        'data': 'vote_up',
                        'style': 1,
                        'group_id': 'vote',
                    },
                    {
                        'id': 'b_down',
                        'text': '踩👎',
                        'type': 1,
                        'data': 'vote_down',
                        'style': 0,
                        'group_id': 'vote',
                    },
                ]
            ]
        }
    )

    buttons = payload['content']['rows'][0]['buttons']
    assert [button['group_id'] for button in buttons] == ['vote', 'vote']
    assert all('group_id' not in button['action'] for button in buttons)
    assert [button['action']['data'] for button in buttons] == ['vote_up', 'vote_down']
    assert [button['render_data']['label'] for button in buttons] == ['赞👍', '踩👎']


def test_template_buttons_preserve_client_exclusive_group_id():
    buttons = TemplateEngine._build_buttons(
        [[{'text': '赞👍', 'type': 1, 'data': 'vote_up', 'group_id': 'vote'}]],
        {},
    )
    payload = build_keyboard(buttons)
    button = payload['content']['rows'][0]['buttons'][0]

    assert button['group_id'] == 'vote'
    assert 'group_id' not in button['action']
