"""框架更新核心逻辑 — 版本检查/下载/备份/解压覆盖 + GitHub 镜像测速"""

import os
import json
import time
import shutil
import zipfile
import fnmatch
import logging
import asyncio
from pathlib import Path
from datetime import datetime

import aiohttp as _aiohttp

log = logging.getLogger('ElainaBot.web.updater')

GITHUB_REPO = "lengxi-root/ElainaBot_v2"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}"
GITHUB_DOWNLOAD_URL = f"https://github.com/{GITHUB_REPO}/archive/main.zip"
GITHUB_SHA_URL = f"https://codeload.github.com/{GITHUB_REPO}/zip/{{version}}"


GITHUB_FILE_MIRRORS = [
    'https://github.chenc.dev/',
    'https://ghproxy.cfd/',
    'https://github.tbedu.top/',
    'https://ghproxy.cc/',
    'https://gh.monlor.com/',
    'https://cdn.akaere.online/',
    'https://gh.idayer.com/',
    'https://gh.llkk.cc/',
    'https://ghpxy.hwinzniej.top/',
    'https://github-proxy.memory-echoes.cn/',
    'https://git.yylx.win/',
    'https://gitproxy.mrhjx.cn/',
    'https://gh.fhjhy.top/',
    'https://gp.zkitefly.eu.org/',
    'https://gh-proxy.com/',
    'https://ghfile.geekertao.top/',
    'https://j.1lin.dpdns.org/',
    'https://ghproxy.imciel.com/',
    'https://github-proxy.teach-english.tech/',
    'https://gh.927223.xyz/',
    'https://github.ednovas.xyz/',
    'https://ghf.xn--eqrr82bzpe.top/',
    'https://gh.dpik.top/',
    'https://gh.jasonzeng.dev/',
    'https://gh.xxooo.cf/',
    'https://gh.bugdey.us.kg/',
    'https://ghm.078465.xyz/',
    'https://j.1win.ggff.net/',
    'https://tvv.tw/',
    'https://gitproxy.127731.xyz/',
    'https://gh.inkchills.cn/',
    'https://ghproxy.cxkpro.top/',
    'https://gh.sixyin.com/',
    'https://github.geekery.cn/',
    'https://git.669966.xyz/',
    'https://gh.5050net.cn/',
    'https://gh.felicity.ac.cn/',
    'https://github.dpik.top/',
    'https://ghp.keleyaa.com/',
    'https://gh.wsmdn.dpdns.org/',
    'https://ghproxy.monkeyray.net/',
    'https://fastgit.cc/',
    'https://gh.catmak.name/',
    'https://gh.noki.icu/',
]

# ==================== 镜像缓存 ====================

_mirror_cache = None        # list[dict] 按延迟排序的可用镜像
_mirror_cache_ts = 0
_mirror_testing = None       # asyncio.Task
_MIRROR_CACHE_TTL = 30 * 60  # 30分钟


def _build_mirror_url(original_url, mirror):
    """拼接镜像 URL"""
    if not mirror:
        return original_url
    return mirror.rstrip('/') + '/' + original_url


async def _test_one_mirror(mirror, timeout=8):
    """GET 请求测试单个镜像延迟 (只读少量字节, 不下载全部)"""
    test_url = _build_mirror_url(f'https://github.com/{GITHUB_REPO}', mirror)
    start = time.time()
    try:
        async with _aiohttp.ClientSession() as session:
            async with session.get(test_url,
                                   headers={'User-Agent': 'Mozilla/5.0 ElainaBot/1.0'},
                                   timeout=_aiohttp.ClientTimeout(total=timeout),
                                   allow_redirects=True) as resp:
                # 只读前 512 字节, 确认可连通即可
                await resp.content.read(512)
                latency = time.time() - start
                ok = 200 <= resp.status < 400
                return {'mirror': mirror, 'latency': round(latency, 3), 'success': ok}
    except Exception:
        return {'mirror': mirror, 'latency': round(time.time() - start, 3), 'success': False}


async def test_all_mirrors(timeout=8):
    """并行测试所有镜像, 返回按延迟排序的结果列表"""
    tasks = [_test_one_mirror(m, timeout) for m in GITHUB_FILE_MIRRORS]
    # 加上 GitHub 直连
    tasks.append(_test_one_mirror('', timeout))
    results = await asyncio.gather(*tasks)
    results = sorted(results, key=lambda r: (not r['success'], r['latency']))
    return results


async def get_fast_mirrors(force=False):
    """获取按延迟排序的可用镜像列表 (缓存 30 分钟)"""
    global _mirror_cache, _mirror_cache_ts, _mirror_testing
    now = time.time()
    if not force and _mirror_cache and (now - _mirror_cache_ts) < _MIRROR_CACHE_TTL:
        return _mirror_cache
    if _mirror_testing and not _mirror_testing.done():
        return await _mirror_testing
    _mirror_testing = asyncio.ensure_future(test_all_mirrors())
    results = await _mirror_testing
    _mirror_cache = [r for r in results if r['success']]
    _mirror_cache_ts = now
    _mirror_testing = None
    return _mirror_cache


def clear_mirror_cache():
    global _mirror_cache, _mirror_cache_ts
    _mirror_cache = None
    _mirror_cache_ts = 0


# 默认跳过的路径
DEFAULT_SKIP = ['config/', 'data/', 'plugins/', 'modules/', '.git/', '__pycache__/']
# 白名单: 即使父目录在 skip 列表, 这些路径仍然正常更新
DEFAULT_WHITELIST = ['plugins/system/']


class FrameworkUpdater:
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.version_file = self.base_dir / "data" / "version.json"
        self.settings_file = self.base_dir / "data" / "update_settings.json"
        (self.base_dir / "data").mkdir(exist_ok=True)

        self.skip_files = self._load_skip_files()
        self.whitelist = self._load_setting('update_whitelist', None) or list(DEFAULT_WHITELIST)
        self.current_version = self._load_version()
        self.custom_mirror = self._load_setting('custom_mirror', '')
        self.progress = {
            'stage': 'idle', 'message': '', 'progress': 0,
            'is_updating': False, 'config_diff': None,
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
                json.dump({'version': version,
                           'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
                          f, indent=2, ensure_ascii=False)
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
            return {'version': self.current_version, 'update_time': 'unknown',
                    'custom_mirror': self.custom_mirror}

    # ==================== 进度 ====================

    def _report(self, stage, message, progress=0, config_diff=None):
        self.progress = {
            'stage': stage, 'message': message, 'progress': progress,
            'is_updating': stage not in ('idle', 'completed', 'failed'),
            'config_diff': config_diff,
        }
        log.info(f"[更新] {stage}: {message} ({progress}%)")

    def get_progress(self):
        return self.progress.copy()

    # ==================== 镜像管理 ====================

    def set_custom_mirror(self, mirror):
        self.custom_mirror = mirror or ''
        self._save_setting('custom_mirror', self.custom_mirror)

    async def _pick_download_url(self, original_url):
        """从缓存的快速镜像中选最快的可用 URL"""
        if self.custom_mirror:
            return _build_mirror_url(original_url, self.custom_mirror)
        mirrors = await get_fast_mirrors()
        if mirrors:
            return _build_mirror_url(original_url, mirrors[0]['mirror'])
        return original_url

    # ==================== 检查更新 ====================

    async def _fetch_api(self, url):
        """尝试通过镜像访问 GitHub API"""
        headers = {'User-Agent': 'ElainaBot/1.0', 'Accept': 'application/json'}
        timeout = _aiohttp.ClientTimeout(total=15)
        # 直连优先
        urls = [url]
        mirrors = await get_fast_mirrors()
        for m in mirrors[:3]:
            if m['mirror']:
                urls.append(_build_mirror_url(url, m['mirror']))
        async with _aiohttp.ClientSession() as session:
            for u in urls:
                try:
                    async with session.get(u, headers=headers, timeout=timeout) as resp:
                        if resp.status == 200:
                            return await resp.json()
                except Exception:
                    continue
        return None

    async def check_for_updates(self):
        try:
            self._report('checking', '正在检查更新...', 0)
            commits = await self._fetch_api(f"{GITHUB_API_URL}/commits?per_page=10")
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
        return await self._fetch_api(f"{GITHUB_API_URL}/commits?per_page=20")

    # ==================== 下载 ====================

    async def download_update(self, version):
        try:
            self._report('downloading', '正在选择最快镜像...', 5)
            original = GITHUB_DOWNLOAD_URL
            url = await self._pick_download_url(original)
            self._report('downloading', f'下载中...', 8)

            temp_dir = self.base_dir / "data" / "temp_update"
            temp_dir.mkdir(exist_ok=True)
            zip_file = temp_dir / f"{version}.zip"

            timeout = _aiohttp.ClientTimeout(total=180)
            headers = {'User-Agent': 'ElainaBot/1.0'}
            async with _aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=timeout,
                                       allow_redirects=True) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get('content-length', 0))
                    downloaded = 0
                    with open(zip_file, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                pct = 10 + downloaded * 30 // total
                                self._report('downloading', f'下载中... {downloaded*100//total}%', pct)

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
            backup_file = backup_dir / f"backup_{self.current_version}_{ts}.zip"

            skip_prefixes = ('plugins', 'modules', 'data/backup', 'data/temp_update',
                             'data\\backup', 'data\\temp_update')
            skip_contains = ('.git', '__pycache__', 'node_modules')

            with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(self.base_dir):
                    rel = os.path.relpath(root, self.base_dir)
                    if any(rel.startswith(p) for p in skip_prefixes) or any(s in rel for s in skip_contains):
                        dirs[:] = []
                        continue
                    dirs[:] = [d for d in dirs
                               if not any(os.path.relpath(os.path.join(root, d), self.base_dir).startswith(p)
                                          for p in skip_prefixes)]
                    for fname in files:
                        fp = os.path.join(root, fname)
                        if not any(s in fp for s in skip_contains):
                            zf.write(fp, os.path.relpath(fp, self.base_dir))

            return str(backup_file)
        except Exception as e:
            log.error(f"备份失败: {e}")
            return None

    # ==================== 覆盖文件 ====================

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
        result = {'success': False, 'message': '', 'updated': 0, 'skipped': 0, 'config_diff': None}
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
            temp = self.base_dir / "data" / "temp_extract"
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

    async def update_to_version(self, version, skip_backup=False):
        self._report('preparing', f'准备更新到 {version}...', 0)
        zip_file = await self.download_update(version)
        if not zip_file:
            return {'success': False, 'message': '下载失败'}
        result = self.apply_update(zip_file, version, skip_backup=skip_backup)
        if result['success']:
            self._save_version(version)
        return result

    async def update_to_latest(self, skip_backup=False):
        check = await self.check_for_updates()
        if check.get('error'):
            return {'success': False, 'message': f"检查失败: {check['error']}"}
        if not check['has_update']:
            return {'success': False, 'message': '已是最新版本'}
        return await self.update_to_version(check['latest_version'], skip_backup=skip_backup)

    async def force_update(self, skip_backup=False):
        try:
            self._report('checking', '获取最新版本...', 0)
            commits = await self._fetch_api(f"{GITHUB_API_URL}/commits?per_page=1")
            if not commits or not isinstance(commits, list):
                return {'success': False, 'message': '无法获取版本信息'}
            latest = commits[0].get('sha', '')[:8]
            return await self.update_to_version(latest, skip_backup=skip_backup)
        except Exception as e:
            self._report('failed', f'获取版本失败: {e}', 0)
            return {'success': False, 'message': str(e)}

    def update_from_upload(self, zip_file_path, version_name=None, skip_backup=False):
        """从上传的压缩包更新"""
        try:
            self._report('preparing', '准备从上传的压缩包更新...', 0)
            if not os.path.exists(zip_file_path):
                self._report('failed', '上传的文件不存在', 0)
                return {'success': False, 'message': '上传的文件不存在'}
            if not zipfile.is_zipfile(zip_file_path):
                self._report('failed', '无效的压缩包格式', 0)
                return {'success': False, 'message': '无效的压缩包格式'}

            version = version_name or f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            self._report('updating', '正在应用更新...', 40)
            result = self.apply_update(zip_file_path, version, skip_backup=skip_backup)
            if result['success']:
                self._save_version(version)
            return result
        except Exception as e:
            self._report('failed', f'更新失败: {e}', 0)
            return {'success': False, 'message': str(e)}
