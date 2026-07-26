"""QQ 分片文件图床 (官方分片上传, 返回 COS 预签名直链与 ttl)"""

import contextlib
import hashlib
import os
import tempfile

from ._common import BaseBed, get_any_sender, log, run_sync


class Bed(BaseBed):
    name = 'qq_file'
    display_name = 'QQ分片文件'
    priority = 70
    defaults = {
        'enabled': True,
        'target_type': 'group',
        'target_id': '',
    }
    comments = {
        '__desc__': 'QQ 分片文件图床 (官方分片上传, 返回 COS 预签名直链与 ttl)',
        'enabled': '是否启用 QQ 分片文件图床 (默认开启)',
        'target_type': '默认上传作用域类型: group(群) / user(用户)',
        'target_id': '默认上传作用域 ID (群 openid 或用户 openid), 留空则自动从数据库获取',
    }

    __slots__ = ()

    async def upload(self, file_data, file_type=1, *, file_name=None, sender=None, target_id=None, target_type=None):
        """QQ 分片文件图床: 走官方分片上传流程, 返回上传结果 dict 或 (False, 原因)

        file_type: 1图片 / 2视频 / 3语音 (raw_url 仅这三类返回, 文件类型4无直链)
        sender: MessageSender 实例; 不传时自动取第一个在线机器人
        target_id / target_type: 上传作用域 (群/用户 openid), 不传时用配置默认值, 未配置则自动从数据库获取
        返回: {'success', 'url', 'ttl', 'file_info', 'file_uuid', 'file_size'}
        """
        if not self._cfg.get('enabled'):
            return (False, 'QQ分片文件图床未开启, 请在 image_hosting 模块配置中启用')
        target_type = target_type or self._cfg.get('target_type', 'group')
        target_id = target_id or self._cfg.get('target_id', '') or await _auto_target(target_type)
        if not target_id:
            return (False, 'QQ分片文件图床无可用 target_id (未配置且自动获取失败)')
        if not isinstance(file_data, bytes) or not file_data:
            return (False, '无效的文件数据')
        if sender is None:
            sender = get_any_sender()
        if sender is None:
            return (False, '无可用机器人实例')

        kind = 'groups' if target_type == 'group' else 'users'
        scope = f'/v2/{kind}/{target_id}'

        from core.message.media import compute_file_hashes

        suffix = os.path.splitext(file_name)[1] if file_name else ''
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                f.write(file_data)
                tmp_path = f.name
            file_size = len(file_data)
            hashes = await run_sync(compute_file_hashes, tmp_path, file_size)

            # 1. 申请上传
            success, prep = await sender.post_json(f'{scope}/upload_prepare', {
                'file_type': file_type,
                'file_name': file_name or f'image{suffix or ".png"}',
                'file_size': file_size,
                **hashes,
            })
            if not success:
                return (False, f'upload_prepare 失败: {prep}')

            upload_id = prep['upload_id']
            block_size = int(prep['block_size'])

            # 2. 逐片 PUT 预签名链接 + 确认
            for part in prep['parts']:
                idx = part['index']
                offset = (idx - 1) * block_size
                chunk = file_data[offset:offset + block_size]
                resp = await sender._client.put(
                    part['presigned_url'], content=chunk,
                    headers={'Content-Length': str(len(chunk))}, timeout=300.0)
                if resp.status_code >= 400:
                    return (False, f'第{idx}片上传失败 (HTTP {resp.status_code})')
                await sender.post_json(f'{scope}/upload_part_finish', {
                    'upload_id': upload_id,
                    'part_index': idx,
                    'block_size': len(chunk),
                    'md5': hashlib.md5(chunk).hexdigest(),
                })

            # 3. 合并 (响应含 raw_url 下载直链, 有效期与 ttl 一致)
            success, result = await sender.post_json(f'{scope}/files', {'upload_id': upload_id})
            if not success:
                return (False, f'合并失败: {result}')
            return {
                'success': True,
                'url': result.get('raw_url', ''),
                'ttl': result.get('ttl', 0),
                'file_info': result.get('file_info', ''),
                'file_uuid': result.get('file_uuid', ''),
                'file_size': file_size,
            }
        except Exception as e:
            log.error(f'QQ分片文件上传失败: {e}')
            return (False, str(e))
        finally:
            if tmp_path and os.path.exists(tmp_path):
                with contextlib.suppress(Exception):
                    os.unlink(tmp_path)

    async def upload_url(self, file_data, file_type=1, *, file_name=None, sender=None, target_id=None, target_type=None):
        """只返回 raw_url 字符串, 失败返回 (False, 原因)"""
        result = await self.upload(
            file_data, file_type, file_name=file_name, sender=sender,
            target_id=target_id, target_type=target_type)
        if isinstance(result, tuple):
            return result
        return result['url'] if result.get('url') else (False, '未返回 raw_url (文件类型4无直链)')


async def _auto_target(target_type):
    """未配置 target_id 时自动从数据库取一个群/用户 openid"""
    try:
        from core.bot.manager import _bot_manager_ref
        if not (_bot_manager_ref and _bot_manager_ref._bots):
            return ''
        ls = getattr(next(iter(_bot_manager_ref._bots.values())), 'log_service', None)
        if ls is None:
            return ''
        if target_type == 'group':
            row = await ls.db_fetch_one(
                'SELECT group_id FROM groups_users WHERE in_group=1 ORDER BY rowid DESC LIMIT 1')
            return (row or {}).get('group_id', '') or ''
        row = await ls.db_fetch_one('SELECT user_id FROM members ORDER BY rowid DESC LIMIT 1')
        return (row or {}).get('user_id', '') or ''
    except Exception as e:
        log.debug(f'自动获取 qq_file target_id 失败: {e}')
        return ''
