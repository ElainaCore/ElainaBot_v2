import builtins
import importlib
import io
import sys
import zipfile

import pytest

from web.tools._zipsafe import is_within, safe_extractall


def test_safe_extractall_rejects_path_traversal(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as zf:
        zf.writestr('../escape.py', 'bad')
    data.seek(0)

    with zipfile.ZipFile(data) as zf, pytest.raises(ValueError):
        safe_extractall(zf, str(tmp_path))

    assert not (tmp_path.parent / 'escape.py').exists()


def test_is_within_allows_child_path(tmp_path):
    assert is_within(str(tmp_path), str(tmp_path / 'plugins' / 'main.py'))


def test_web_zip_imports_do_not_require_core_zipsafe(monkeypatch):
    modules = [
        'web.tools._market.install',
        'web.tools._plugin_mgr.files',
        'web.tools._plugin_mgr.module',
    ]
    for module in modules:
        sys.modules.pop(module, None)
    sys.modules.pop('core.base.zipsafe', None)

    real_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'core.base.zipsafe':
            raise ModuleNotFoundError("No module named 'core.base.zipsafe'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', blocked_import)

    for module in modules:
        importlib.import_module(module)
