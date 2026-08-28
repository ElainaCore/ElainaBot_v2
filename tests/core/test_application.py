"""Application / get_app / HookManager / Phase 2 模式测试"""

from core.application import Application, get_app


class TestGetApp:
    """get_app() 全局访问器"""

    def test_get_app_returns_none_before_start(self):
        """启动前 get_app() 返回 None"""
        # 注意: 如果之前有测试设置了 _app, 需要隔离
        assert get_app() is None

    def test_application_creates_hook_manager(self):
        """Application 创建时自动初始化 HookManager"""
        app = Application()
        assert app.hook_manager is not None
        assert app.hook_manager._hooks is not None

    def test_hook_manager_from_application(self):
        """通过 Application 获取 HookManager"""
        app = Application()
        hm = app.hook_manager
        assert hm is not None
        # 注册 hook
        called = []

        def my_cb(data):
            called.append(data)

        hm.register('test_hook', my_cb)
        assert hm.has('test_hook')
        hm.unregister('test_hook', my_cb)
        assert not hm.has('test_hook')


class TestErrorCallbacks:
    """Application 错误回调注册"""

    def test_register_error_callback(self):
        """注册和触发错误回调"""
        app = Application()
        errors = []
        app.on_error(lambda d: errors.append(d))
        assert len(app._error_callbacks) == 1

    def test_register_framework_callback(self):
        """注册和触发框架日志回调"""
        app = Application()
        logs = []
        app.on_framework_log(lambda d: logs.append(d))
        assert len(app._framework_callbacks) == 1

    def test_fire_error_callbacks(self):
        """触发错误回调"""
        app = Application()
        caught = []
        app.on_error(lambda d: caught.append(d))
        app._fire_error_callbacks({'test': True})
        assert len(caught) == 1
        assert caught[0] == {'test': True}

    def test_callback_exception_does_not_crash(self):
        """回调异常不中断其他回调"""
        app = Application()
        results = []

        def bad_cb(_):
            raise RuntimeError('boom')

        def good_cb(d):
            results.append(d)

        app.on_error(bad_cb)
        app.on_error(good_cb)
        app._fire_error_callbacks({'ok': True})
        assert len(results) == 1
        assert results[0] == {'ok': True}


class TestHookManagerCompat:
    """HookManager 向后兼容"""

    def test_get_hook_manager_fallback(self):
        """get_hook_manager() 在没有 Application 时回退到模块级单例"""
        from core.module.hook import get_hook_manager, reset_hook_manager

        reset_hook_manager()
        hm = get_hook_manager()
        assert hm is not None
        hm.register('fallback_test', lambda: None)
        assert hm.has('fallback_test')
        reset_hook_manager()

    def test_reset_hook_manager(self):
        """reset_hook_manager 创建新实例"""
        from core.module.hook import get_hook_manager, reset_hook_manager

        hm1 = get_hook_manager()
        hm2 = reset_hook_manager()
        assert hm1 is not hm2
        hm3 = get_hook_manager()
        assert hm3 is hm2  # reset 后 get_hook_manager 返回新实例
