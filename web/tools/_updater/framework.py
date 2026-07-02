"""框架更新 — FrameworkUpdater 更新流程类"""

import fnmatch
import json
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

import aiohttp as _aiohttp

from web.tools._updater.mirror import detect_environment
from web.tools._updater.shared import (
    DEFAULT_SKIP,
    DEFAULT_WHITELIST,
    GITHUB_API_MIRRORS,
    GITHUB_DOWNLOAD_URL,
    _build_mirror_url,
    _load_mirror_cache,
    log,
)


class FrameworkUpdater:
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.version_file = self.base_dir / 'data' / 'version.json'
        self.settings_file = self.base_dir / 'data' / 'update_settings.json'
        (self.base_dir / 'data').mkdir(exist_ok=True)

        self.skip_files = self._load_skip_files()
        self.whitelist = self._load_setting('update_whitelist', None) or list(DEFAULT_WHITELIST)
        self.current_version = self._load_version()
        self.custom_mirror = self._load_setting('custom_mirror', '')
        self.progress = {
            'stage': 'idle',
            'message': '',
            'progress': 0,
            'is_updating': False,
            'config_diff': None,
        }

    # ==================== 配置读写 ====================

    def _load_skip_files(self):
        try:
            cfg = self._load_setting('skip_files', None)
            if cfg:
                return cfg
        except Exception:
            pass
        return list(DEFAULT_SKIP)

    def _load_version(self):
        try:
            with open(self.version_file, encoding='utf-8') as f:
                return json.load(f).get('version', 'unknown')
        except Exception:
            return 'unknown'

    def _save_version(self, version):
        try:
            with open(self.version_file, 'w', encoding='utf-8') as f:
                json.dump(
                    {
                        'version': version,
                        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            self.current_version = version
        except Exception:
            pass

    def _load_setting(self, key, default):
        try:
            with open(self.settings_file, encoding='utf-8') as f:
                return json.load(f).get(key, default)
        except Exception:
            return default

    def _save_setting(self, key, value):
        try:
            data = {}
            if self.settings_file.exists():
                with open(self.settings_file, encoding='utf-8') as f:
                    data = json.load(f)
            data[key] = value
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def get_version_info(self):
        try:
            with open(self.version_file, encoding='utf-8') as f:
                info = json.load(f)
            info['custom_mirror'] = self.custom_mirror
            return info
        except Exception:
            return {
                'version': self.current_version,
                'update_time': 'unknown',
                'custom_mirror': self.custom_mirror,
            }

    # ==================== 进度 ====================

    def _report(self, stage, message, progress=0, config_diff=None):
        self.progress = {
            'stage': stage,
            'message': message,
            'progress': progress,
            'is_updating': stage not in ('idle', 'completed', 'failed'),
            'config_diff': config_diff,
        }
        log.info(f'[更新] {stage}: {message} ({progress}%)')

    def get_progress(self):
        return self.progress.copy()

    # ==================== 镜像管理 ====================

    def set_custom_mirror(self, mirror):
        self.custom_mirror = mirror or ''
        self._save_setting('custom_mirror', self.custom_mirror)

    async def _pick_download_url(self, original_url):
        """从磁盘缓存的排名中选最快的镜像 URL"""
        if self.custom_mirror:
            return _build_mirror_url(original_url, self.custom_mirror)
        cached = _load_mirror_cache()
        if cached:
            return _build_mirror_url(original_url, cached[0]['mirror'])
        return original_url

    # ==================== 检查更新 ====================

    async def _fetch_api(self, path=''):
        """通过多个 API 代理访问 GitHub API"""
        headers = {
            'User-Agent': 'Mozilla/5.0 ElainaBot/1.0',
            'Accept': 'application/vnd.github+json',
        }
        timeout = _aiohttp.ClientTimeout(total=15)
        urls = [base + path for base in GITHUB_API_MIRRORS]
        async with _aiohttp.ClientSession() as session:
            for u in urls:
                try:
                    log.debug(f'API 请求: {u}')
                    async with session.get(
                        u,
                        headers=headers,
                        timeout=timeout,
                        allow_redirects=True,
                        ssl=False,
                    ) as resp:
                        if resp.status == 200:
                            ct = resp.headers.get('content-type', '')
                            body = await resp.read()
                            if b'[' in body[:2] or b'{' in body[:2]:
                                return json.loads(body)
                            log.debug(f'API 返回非 JSON: {ct}, url={u}')
                        else:
                            log.debug(f'API 状态码 {resp.status}: {u}')
                except Exception as e:
                    log.debug(f'API 请求失败: {u} -> {e}')
                    continue
        return None

    async def check_for_updates(self):
        try:
            self._report('checking', '正在检查更新...', 0)
            commits = await self._fetch_api('/commits?per_page=10')
            if not commits or not isinstance(commits, list):
                self._report('idle', '', 0)
                return {'has_update': False, 'error': '无法获取更新信息'}

            latest = commits[0].get('sha', '')[:8]
            current = self.current_version[:8] if len(self.current_version) >= 8 else self.current_version
            has_update = (current != latest and self.current_version != 'unknown') or self.current_version == 'unknown'

            self._report('idle', '', 0)
            return {
                'has_update': has_update,
                'latest_version': latest,
                'current_version': self.current_version,
                'changelog': commits[:10],
                'error': None,
            }
        except Exception as e:
            self._report('idle', '', 0)
            return {'has_update': False, 'error': str(e)}

    async def fetch_changelog(self):
        """获取更新日志 (commits)"""
        return await self._fetch_api('/commits?per_page=20')

    # ==================== 下载 ====================

    async def download_update(self, version):
        try:
            self._report('downloading', '正在选择最快镜像...', 5)
            original = GITHUB_DOWNLOAD_URL
            url = await self._pick_download_url(original)
            self._report('downloading', '下载中...', 8)

            temp_dir = self.base_dir / 'data' / 'temp_update'
            temp_dir.mkdir(exist_ok=True)
            zip_file = temp_dir / f'{version}.zip'

            timeout = _aiohttp.ClientTimeout(total=180)
            headers = {'User-Agent': 'ElainaBot/1.0'}
            async with (
                _aiohttp.ClientSession() as session,
                session.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=True,
                    ssl=False,
                ) as resp,
            ):
                resp.raise_for_status()
                total = int(resp.headers.get('content-length', 0))
                downloaded = 0
                with open(zip_file, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = 10 + downloaded * 30 // total
                            self._report(
                                'downloading',
                                f'下载中... {downloaded * 100 // total}%',
                                pct,
                            )

            self._report('downloading', '下载完成', 40)
            return str(zip_file)
        except Exception as e:
            self._report('failed', f'下载失败: {e}', 0)
            return None

    # ==================== 备份 ====================

    def backup_current_version(self):
        try:
            backup_dir = self.base_dir / 'data' / 'backup'
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_file = backup_dir / f'backup_{self.current_version}_{ts}.zip'

            # 读取日志目录名 (默认 'log')
            try:
                import yaml

                with open(self.base_dir / 'config' / 'settings.yaml', encoding='utf-8') as f:
                    _s = yaml.safe_load(f) or {}
                log_dir_name = (_s.get('logging') or {}).get('dir', 'log')
            except Exception:
                log_dir_name = 'log'
            log_prefix = f'data/{log_dir_name}'
            log_prefix_win = f'data\\{log_dir_name}'

            skip_prefixes = (
                'plugins',
                'modules',
                'data/backup',
                'data/temp_update',
                'data/media',
                'data\\backup',
                'data\\temp_update',
                'data\\media',
                log_prefix,
                log_prefix_win,
            )
            skip_contains = ('.git', '__pycache__', 'node_modules')

            # 日志目录中仅备份的文件 (每个 appid 子目录下的)
            log_keep_names = frozenset({'data.db', 'dau.db'})

            with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(self.base_dir):
                    rel = os.path.relpath(root, self.base_dir)
                    if any(rel.startswith(p) for p in skip_prefixes) or any(s in rel for s in skip_contains):
                        dirs[:] = []
                        continue
                    dirs[:] = [d for d in dirs if not any(os.path.relpath(os.path.join(root, d), self.base_dir).startswith(p) for p in skip_prefixes)]
                    for fname in files:
                        fp = os.path.join(root, fname)
                        if not any(s in fp for s in skip_contains):
                            zf.write(fp, os.path.relpath(fp, self.base_dir))

                # 单独收集日志目录中的 data.db / dau.db
                log_abs = self.base_dir / 'data' / log_dir_name
                if log_abs.is_dir():
                    for appid_dir in log_abs.iterdir():
                        if not appid_dir.is_dir():
                            continue
                        for db_name in log_keep_names:
                            db_file = appid_dir / db_name
                            if db_file.is_file():
                                zf.write(
                                    str(db_file),
                                    os.path.relpath(str(db_file), self.base_dir),
                                )

            return str(backup_file)
        except Exception as e:
            log.error(f'备份失败: {e}')
            return None

    # ==================== 覆盖文件 ==

    def _should_skip(self, path):
        path = path.replace('\\', '/')
        # 白名单优先: plugins/system/ 等路径不跳过
        for w in self.whitelist:
            w = w.replace('\\', '/')
            if path == w.rstrip('/') or path.startswith(w.rstrip('/') + '/'):
                return False
        for p in self.skip_files:
            p = p.replace('\\', '/')
            if path == p.rstrip('/') or path.startswith(p.rstrip('/') + '/') or fnmatch.fnmatch(path, p):
                return True
        return False

    def apply_update(self, zip_file, version, skip_backup=False):
        result = {
            'success': False,
            'message': '',
            'updated': 0,
            'skipped': 0,
            'config_diff': None,
        }
        try:
            if skip_backup:
                self._report('backing_up', '跳过备份...', 45)
            else:
                self._report('backing_up', '正在备份...', 45)
                backup = self.backup_current_version()
                if not backup:
                    self._report('failed', '备份失败', 0)
                    return result

            self._report('updating', '正在解压...', 55)
            temp = self.base_dir / 'data' / 'temp_extract'
            if temp.exists():
                shutil.rmtree(temp)
            temp.mkdir(parents=True)

            with zipfile.ZipFile(zip_file, 'r') as zf:
                zf.extractall(temp)

            items = list(temp.iterdir())
            source = items[0] if len(items) == 1 and items[0].is_dir() else temp

            self._report('updating', '正在更新文件...', 60)
            for root, _, files in os.walk(source):
                for fname in files:
                    src = os.path.join(root, fname)
                    rel = os.path.relpath(src, source)
                    if self._should_skip(rel):
                        result['skipped'] += 1
                        continue
                    dst = self.base_dir / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    result['updated'] += 1

            shutil.rmtree(temp, ignore_errors=True)
            if os.path.exists(zip_file):
                os.remove(zip_file)

            result['success'] = True
            result['message'] = f'更新成功！更新 {result["updated"]} 个文件，跳过 {result["skipped"]} 个'
            self._report('completed', result['message'], 100, result['config_diff'])
        except Exception as e:
            result['message'] = f'更新失败: {e}'
            self._report('failed', result['message'], 0)
        return result

    # ==================== 更新流程 ====================

    async def update_to_version(self, version, skip_backup=False, auto_restart=False):
        # 更新前环境检查
        env = detect_environment()
        if not env['writable']:
            self._report('failed', '项目目录不可写, 无法更新', 0)
            return {'success': False, 'message': '项目目录不可写, 无法更新'}

        self._report('preparing', f'准备更新到 {version}...', 0)
        zip_file = await self.download_update(version)
        if not zip_file:
            return {'success': False, 'message': '下载失败'}
        result = self.apply_update(zip_file, version, skip_backup=skip_backup)
        if result['success']:
            self._save_version(version)
            result['environment'] = env
            if auto_restart:
                self._trigger_restart()
        return result

    async def update_to_latest(self, skip_backup=False, auto_restart=False):
        check = await self.check_for_updates()
        if check.get('error'):
            return {'success': False, 'message': f'检查失败: {check["error"]}'}
        if not check['has_update']:
            return {'success': False, 'message': '已是最新版本'}
        return await self.update_to_version(check['latest_version'], skip_backup=skip_backup, auto_restart=auto_restart)

    async def force_update(self, skip_backup=False, auto_restart=False):
        try:
            self._report('checking', '获取最新版本...', 0)
            commits = await self._fetch_api('/commits?per_page=1')
            if not commits or not isinstance(commits, list):
                return {'success': False, 'message': '无法获取版本信息'}
            latest = commits[0].get('sha', '')[:8]
            return await self.update_to_version(latest, skip_backup=skip_backup, auto_restart=auto_restart)
        except Exception as e:
            self._report('failed', f'获取版本失败: {e}', 0)
            return {'success': False, 'message': str(e)}

    @staticmethod
    def _trigger_restart():
        """更新后重启: Windows 用外部脚本, Linux/Docker 走内部重启 + os.execv"""
        try:
            from core.application import get_app

            app = get_app()
            if app:
                log.info('更新完成, 触发重启...')
                app._restart_requested = True
                if app._stop_event:
                    app._stop_event.set()
                return
        except Exception:
            pass
        log.warning('无法自动重启, 请手动重启')

    def update_from_upload(self, zip_file_path, version_name=None, skip_backup=False, auto_restart=False):
        """从上传的压缩包更新"""
        try:
            self._report('preparing', '准备从上传的压缩包更新...', 0)
            if not os.path.exists(zip_file_path):
                self._report('failed', '上传的文件不存在', 0)
                return {'success': False, 'message': '上传的文件不存在'}
            if not zipfile.is_zipfile(zip_file_path):
                self._report('failed', '无效的压缩包格式', 0)
                return {'success': False, 'message': '无效的压缩包格式'}

            version = version_name or f'upload_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
            self._report('updating', '正在应用更新...', 40)
            result = self.apply_update(zip_file_path, version, skip_backup=skip_backup)
            if result['success']:
                self._save_version(version)
                if auto_restart:
                    self._trigger_restart()
            return result
        except Exception as e:
            self._report('failed', f'更新失败: {e}', 0)
            return {'success': False, 'message': str(e)}
