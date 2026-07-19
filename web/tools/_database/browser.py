"""数据库浏览器 — 查询/浏览/删除/搜索/挂载"""

import json
import logging
import os
import re
import sqlite3

from aiohttp import web

from web.response import error, ok

log = logging.getLogger('ElainaBot.web.database')

_bot_manager = None
_base_dir = ''

# 判断是否为返回结果集的查询
_READ_PATTERN = re.compile(r'^\s*(SELECT|PRAGMA|EXPLAIN|WITH)\b', re.IGNORECASE)


def set_context(bot_manager, base_dir: str):
    global _bot_manager, _base_dir
    _bot_manager = bot_manager
    _base_dir = base_dir


def _mounted_file():
    """挂载数据库配置文件路径"""
    return os.path.join(_base_dir, 'data', 'mounted_databases.json')


def _load_mounted():
    """读取已挂载的数据库路径列表"""
    try:
        with open(_mounted_file(), encoding='utf-8') as f:
            paths = json.load(f)
        return [p for p in paths if isinstance(p, str)]
    except (OSError, ValueError):
        return []


def _save_mounted(paths):
    """保存挂载的数据库路径列表"""
    fpath = _mounted_file()
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(paths, f, ensure_ascii=False, indent=2)


def _log_base_dir():
    """日志根目录"""
    from core.base.config import cfg

    log_dir = cfg.get('settings', 'logging.dir', 'log')
    return os.path.join(_base_dir, 'data', log_dir)


def _find_databases():
    """扫描所有机器人的 .db 文件, 返回 [{appid, name, path, size, date}]"""
    log_base = _log_base_dir()
    result = []
    if not os.path.isdir(log_base):
        return result

    for appid_dir in sorted(os.listdir(log_base)):
        appid_path = os.path.join(log_base, appid_dir)
        if not os.path.isdir(appid_path):
            continue

        bot_name = appid_dir
        if _bot_manager:
            bot = _bot_manager.get_bot(appid_dir)
            if bot:
                bot_name = bot.name or appid_dir

        base = {'appid': appid_dir, 'bot_name': bot_name}

        # 根目录下的 .db 文件 (data.db, dau.db 等)
        _collect_db_files(result, appid_path, base, '')

        # 日期子目录下的 .db 文件
        for date_dir in sorted(os.listdir(appid_path), reverse=True):
            date_path = os.path.join(appid_path, date_dir)
            if os.path.isdir(date_path) and re.match(r'^\d{4}-\d{2}-\d{2}$', date_dir):
                _collect_db_files(result, date_path, base, date_dir)

    return result


def _mounted_databases():
    """已挂载的数据库列表, 返回 [{appid, bot_name, name, path, size, date, mounted}]"""
    result = []
    base_abs = os.path.abspath(_base_dir)
    for p in _load_mounted():
        abs_path = os.path.abspath(p)
        rel = os.path.relpath(abs_path, base_abs).replace('\\', '/')
        result.append(
            {
                'appid': '_mounted',
                'bot_name': '挂载数据库',
                'name': rel,
                'path': abs_path.replace('\\', '/'),
                'size': os.path.getsize(abs_path) if os.path.isfile(abs_path) else 0,
                'date': '',
                'mounted': True,
                'missing': not os.path.isfile(abs_path),
            }
        )
    return result


def _collect_db_files(result, directory, base, date):
    """扫描目录下的 .db 文件并追加到 result"""
    for f in sorted(os.listdir(directory)):
        fpath = os.path.join(directory, f)
        if f.endswith('.db') and os.path.isfile(fpath):
            result.append(
                {
                    **base,
                    'name': f,
                    'path': fpath.replace('\\', '/'),
                    'size': os.path.getsize(fpath),
                    'date': date,
                }
            )


def _validate_db_path(db_path):
    """校验路径为 log 目录下或已挂载的 .db 文件"""
    abs_path = os.path.abspath(db_path)
    if not abs_path.endswith('.db'):
        return False, ''
    if not os.path.isfile(abs_path):
        return False, ''
    log_base = os.path.abspath(_log_base_dir())
    if abs_path.startswith(log_base):
        return True, abs_path
    mounted = {os.path.abspath(p) for p in _load_mounted()}
    if abs_path in mounted:
        return True, abs_path
    return False, ''


def _open_readonly(db_path):
    """以只读方式打开 SQLite"""
    uri = f'file:{db_path}?mode=ro'
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.text_factory = lambda b: b.decode('utf-8', errors='replace')
    return conn


def _open_readwrite(db_path):
    """以读写方式打开 SQLite"""
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.text_factory = lambda b: b.decode('utf-8', errors='replace')
    return conn


# ==================== API handlers ====================


async def handle_list_databases(request: web.Request):
    """列出所有数据库文件 (含已挂载)"""
    databases = _find_databases() + _mounted_databases()
    return ok({'databases': databases})


async def handle_list_tables(request: web.Request):
    """列出某个数据库的所有表"""
    body = await request.json()
    db_path = body.get('path', '')
    if not db_path:
        return error('缺少 path', status=400)

    valid, abs_path = _validate_db_path(db_path)
    if not valid:
        return error('无效路径', status=403)

    try:
        conn = _open_readonly(abs_path)
        tables = []
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            tname = row['name']
            count_row = conn.execute(f'SELECT COUNT(*) as c FROM "{tname}"').fetchone()
            count = count_row['c'] if count_row else 0

            # 获取列信息
            columns = []
            for col in conn.execute(f'PRAGMA table_info("{tname}")'):
                columns.append(
                    {
                        'name': col['name'],
                        'type': col['type'],
                        'notnull': bool(col['notnull']),
                        'pk': bool(col['pk']),
                    }
                )

            tables.append(
                {
                    'name': tname,
                    'count': count,
                    'columns': columns,
                }
            )
        conn.close()
        return ok({'tables': tables})
    except Exception as e:
        log.warning(f'列出表失败: {e}')
        return error(str(e), status=500)


async def handle_query_table(request: web.Request):
    """分页查询表数据"""
    body = await request.json()
    db_path = body.get('path', '')
    table = body.get('table', '')
    page = max(1, int(body.get('page', 1)))
    page_size = min(200, max(1, int(body.get('page_size', 50))))
    order_by = body.get('order_by', '')
    order_dir = body.get('order_dir', 'DESC')

    if not db_path or not table:
        return error('缺少参数', status=400)

    valid, abs_path = _validate_db_path(db_path)
    if not valid:
        return error('无效路径', status=403)

    # 防注入: 表名只允许字母数字下划线
    if not re.match(r'^[\w]+$', table):
        return error('无效表名', status=400)

    if order_dir.upper() not in ('ASC', 'DESC'):
        order_dir = 'DESC'

    try:
        conn = _open_readonly(abs_path)

        # 总数
        total = conn.execute(f'SELECT COUNT(*) as c FROM "{table}"').fetchone()['c']

        # 排序 (默认按 rowid 倒序)
        order_clause = f'ORDER BY "{order_by}" {order_dir}' if order_by and re.match(r'^[\w]+$', order_by) else 'ORDER BY rowid DESC'

        offset = (page - 1) * page_size
        rows = conn.execute(
            f'SELECT rowid AS _rowid, * FROM "{table}" {order_clause} LIMIT ? OFFSET ?',
            (page_size, offset),
        ).fetchall()

        data = [dict(r) for r in rows]

        # 列信息
        columns = []
        for col in conn.execute(f'PRAGMA table_info("{table}")'):
            columns.append({'name': col['name'], 'type': col['type']})

        conn.close()
        return ok(
            {
                'rows': data,
                'columns': columns,
                'total': total,
                'page': page,
                'page_size': page_size,
            }
        )
    except Exception as e:
        log.warning(f'查询表失败: {e}')
        return error(str(e), status=500)


async def handle_execute_sql(request: web.Request):
    """执行任意 SQL (支持 SELECT / INSERT / UPDATE / DELETE / DDL 等全部指令)"""
    body = await request.json()
    db_path = body.get('path', '')
    sql = (body.get('sql', '') or '').strip()

    if not db_path or not sql:
        return error('缺少参数', status=400)

    valid, abs_path = _validate_db_path(db_path)
    if not valid:
        return error('无效路径', status=403)

    is_read = _READ_PATTERN.match(sql)

    # 读查询自动加 LIMIT 防止返回过多
    if is_read and not re.search(r'\bLIMIT\b', sql, re.IGNORECASE):
        sql = sql.rstrip(';') + ' LIMIT 1000'

    try:
        conn = _open_readwrite(abs_path)

        # 多语句 (含分号分割) 用 executescript (无结果集)
        statements = [s.strip() for s in sql.split(';') if s.strip()]
        if len(statements) > 1 and not is_read:
            conn.executescript(sql)
            conn.close()
            return ok({'affected': -1}, message=f'已执行 {len(statements)} 条语句')

        cursor = conn.execute(sql)

        if is_read:
            rows = cursor.fetchall()
            columns = [{'name': desc[0], 'type': ''} for desc in cursor.description] if cursor.description else []
            data = [dict(r) for r in rows]
            conn.close()
            return ok({'rows': data, 'columns': columns, 'total': len(data)})

        # 写操作: 返回影响行数
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return ok({'affected': affected}, message=f'执行成功, 影响 {affected} 行')
    except Exception as e:
        return error(str(e), status=400)


async def handle_search_database(request: web.Request):
    """全库模糊搜索: 在所有表的所有列中查找包含关键词的记录"""
    body = await request.json()
    db_path = body.get('path', '')
    keyword = str(body.get('keyword', '') or '').strip()
    limit = min(200, max(1, int(body.get('limit', 50))))

    if not db_path or not keyword:
        return error('缺少参数 (path/keyword)', status=400)

    valid, abs_path = _validate_db_path(db_path)
    if not valid:
        return error('无效路径', status=403)

    escaped = keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    pattern = f'%{escaped}%'

    try:
        conn = _open_readonly(abs_path)
        results = []
        table_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        for trow in table_rows:
            tname = trow['name']
            columns = [{'name': col['name'], 'type': col['type']} for col in conn.execute(f'PRAGMA table_info("{tname}")')]
            if not columns:
                continue
            conds = ' OR '.join('CAST("{}" AS TEXT) LIKE ? ESCAPE \'\\\''.format(c['name']) for c in columns)
            params = [pattern] * len(columns)
            try:
                total = conn.execute(f'SELECT COUNT(*) as c FROM "{tname}" WHERE {conds}', params).fetchone()['c']
                if not total:
                    continue
                rows = conn.execute(
                    f'SELECT rowid AS _rowid, * FROM "{tname}" WHERE {conds} ORDER BY rowid DESC LIMIT ?',
                    params + [limit],
                ).fetchall()
            except sqlite3.Error:
                continue
            results.append(
                {
                    'table': tname,
                    'columns': columns,
                    'data': [dict(r) for r in rows],
                    'total': total,
                }
            )
        conn.close()
        return ok({'results': results, 'keyword': keyword})
    except Exception as e:
        log.warning(f'全库搜索失败: {e}')
        return error(str(e), status=500)


async def handle_browse_files(request: web.Request):
    """浏览框架目录下的文件夹与 .db 文件 (用于挂载选择)"""
    body = await request.json()
    rel_dir = str(body.get('dir', '') or '').strip().strip('/')

    base_abs = os.path.abspath(_base_dir)
    target = os.path.abspath(os.path.join(base_abs, rel_dir)) if rel_dir else base_abs
    if not (target == base_abs or target.startswith(base_abs + os.sep)):
        return error('无效路径', status=403)
    if not os.path.isdir(target):
        return error('目录不存在', status=404)

    dirs, files = [], []
    try:
        for f in sorted(os.listdir(target)):
            if f.startswith('.') or f in ('__pycache__', 'node_modules'):
                continue
            fpath = os.path.join(target, f)
            if os.path.isdir(fpath):
                dirs.append({'name': f, 'type': 'dir'})
            elif f.endswith('.db') and os.path.isfile(fpath):
                files.append({'name': f, 'type': 'db', 'size': os.path.getsize(fpath), 'path': fpath.replace('\\', '/')})
    except OSError as e:
        return error(str(e), status=500)

    rel = os.path.relpath(target, base_abs).replace('\\', '/')
    return ok({'dir': '' if rel == '.' else rel, 'items': dirs + files})


async def handle_mount_database(request: web.Request):
    """挂载一个框架目录下的 .db 文件 (永久保留)"""
    body = await request.json()
    db_path = str(body.get('path', '') or '').strip()
    if not db_path:
        return error('缺少 path', status=400)

    abs_path = os.path.abspath(db_path)
    base_abs = os.path.abspath(_base_dir)
    if not abs_path.startswith(base_abs + os.sep):
        return error('只能挂载框架目录下的数据库', status=403)
    if not abs_path.endswith('.db') or not os.path.isfile(abs_path):
        return error('不是有效的 .db 文件', status=400)

    mounted = _load_mounted()
    norm = abs_path.replace('\\', '/')
    if norm not in mounted:
        mounted.append(norm)
        _save_mounted(mounted)
    return ok({'path': norm})


async def handle_unmount_database(request: web.Request):
    """取消挂载 (不删除文件)"""
    body = await request.json()
    db_path = str(body.get('path', '') or '').strip()
    if not db_path:
        return error('缺少 path', status=400)

    abs_path = os.path.abspath(db_path).replace('\\', '/')
    mounted = _load_mounted()
    remaining = [p for p in mounted if os.path.abspath(p).replace('\\', '/') != abs_path]
    if len(remaining) != len(mounted):
        _save_mounted(remaining)
    return ok()


async def handle_delete_rows(request: web.Request):
    """删除表中的单条或多条数据 (path=库路径, table=表名, rowids=rowid 列表)"""
    body = await request.json()
    db_path = body.get('path', '')
    table = body.get('table', '')
    rowids = body.get('rowids', [])

    if not db_path or not table or not rowids:
        return error('缺少参数 (path/table/rowids)', status=400)

    if not re.match(r'^[\w]+$', table):
        return error('无效表名', status=400)

    if not isinstance(rowids, list) or not all(isinstance(r, int) for r in rowids):
        return error('rowids 必须是整数数组', status=400)

    valid, abs_path = _validate_db_path(db_path)
    if not valid:
        return error('无效路径', status=403)

    try:
        conn = _open_readwrite(abs_path)
        placeholders = ','.join('?' * len(rowids))
        cursor = conn.execute(f'DELETE FROM "{table}" WHERE rowid IN ({placeholders})', rowids)
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return ok({'deleted': deleted})
    except Exception as e:
        log.warning(f'删除数据失败: {e}')
        return error(str(e), status=500)
