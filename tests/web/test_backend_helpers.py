from types import SimpleNamespace

from web.tools._bots import iter_bots
from web.tools._market.install import _clear_dir_except_data
from web.tools._python_source import read_dict_assignment


def test_iter_bots_filters_explicit_manager():
    bots = {'1001': object(), '1002': object()}
    manager = SimpleNamespace(_bots=bots)

    assert iter_bots(None) == []
    assert iter_bots(manager, '1001') == [('1001', bots['1001'])]
    assert iter_bots(manager) == list(bots.items())


def test_read_dict_assignment(tmp_path):
    source = tmp_path / 'plugin.py'
    source.write_text("__plugin_meta__ = {'name': 'demo', 'version': 2}\n", encoding='utf-8')

    assert read_dict_assignment(str(source), '__plugin_meta__') == {'name': 'demo', 'version': 2}
    assert read_dict_assignment(str(source), '__module_meta__') is None

    source.write_text("__plugin_meta__ = ['demo']\n", encoding='utf-8')
    assert read_dict_assignment(str(source), '__plugin_meta__') is None

    source.write_text('__plugin_meta__ = unknown_name\n', encoding='utf-8')
    assert read_dict_assignment(str(source), '__plugin_meta__') is None

    source.write_text('__plugin_meta__ = {\n', encoding='utf-8')
    assert read_dict_assignment(str(source), '__plugin_meta__') is None


def test_clear_dir_except_data(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    (data_dir / 'config.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'main.py').write_text('', encoding='utf-8')
    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    (cache_dir / 'state').write_text('', encoding='utf-8')

    _clear_dir_except_data(str(tmp_path))

    assert [path.name for path in tmp_path.iterdir()] == ['data']
    assert (data_dir / 'config.json').is_file()

    _clear_dir_except_data(str(tmp_path / 'missing'))
