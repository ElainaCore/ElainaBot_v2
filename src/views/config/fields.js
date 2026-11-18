const field = (path, label, type = 'text', fallback = '', extra = {}) => ({ path, label, type, fallback, ...extra })
const toggle = (path, label, fallback = false, extra = {}) => field(path, label, 'boolean', fallback, extra)
const number = (path, label, fallback, extra = {}) => field(path, label, 'number', fallback, extra)

export const BOT_SECTIONS = [
  { id: 'identity', title: '基本信息', description: '机器人凭据与管理权限', icon: 'link', column: 0, fields: [
    field('appid', 'AppID'), field('secret', 'Secret', 'password'),
    field('api_version', 'QQ API 接口', 'select', 'new', { options: [{ value: 'new', label: '新接口 · api.bot.qq.com' }, { value: 'old', label: '旧接口 · bots.qq.com' }] }),
    field('robot_qq', '机器人 QQ'), field('owner_ids', '主人 OpenID', 'list', [], { full: true, placeholder: '多个 OpenID 用英文逗号分隔' }),
  ] },
  { id: 'websocket', title: 'WebSocket', description: '连接方式与自动重连', icon: 'link', column: 1, fields: [
    toggle('websocket.enabled', '启用 WebSocket'), toggle('websocket.subscribe_channel_events', '订阅频道消息'),
    field('websocket.custom_url', '自定义 WS 地址', 'text', '', { full: true, placeholder: '留空使用默认连接地址' }),
    number('websocket.reconnect_interval', '重连间隔（秒）', 5, { min: 0 }), number('websocket.max_reconnects', '最大重连次数', -1, { min: -1, hint: '-1 表示不限次数' }),
    field('websocket.identify.name', 'Identify 客户端名称', 'select', 'ElainaBot', { full: true, options: ['ElainaBot', 'WorkBuddy', 'OpenClaw', 'Hermes', 'QClaw'] }),
  ] },
  { id: 'message', title: '消息处理', description: '消息格式与指令回复策略', icon: 'chatbubbles', column: 0, fields: [
    toggle('message.use_markdown', '使用 Markdown', true), toggle('message.button_enter_to_send', '按钮自动发送转回调'),
    toggle('message.interaction_explicit_only', '回调事件需显式订阅', false, { full: true, hint: '默认关闭，回调按原规则匹配；开启后仅交给显式声明 INTERACTION_CREATE 的处理器。' }),
    toggle('message.suppress_bot_system_reply', '抑制其他机器人导致的无指令回复', false, { full: true }),
    toggle('message.send_error_response', '消息发送失败时回复模板', true, { full: true }),
    field('api_base', '第三方发信接口', 'text', '', { placeholder: '留空使用官方接口' }), field('message.markdown_suffix', 'Markdown 后缀'),
    field('message.default_response_excluded_regex', '排除无指令回复正则', 'list', [], { full: true, placeholder: '多个正则用英文逗号分隔' }),
  ] },
  { id: 'features', title: '功能开关', description: '欢迎消息、维护与事件去重', icon: 'grid', column: 1, fields: [
    toggle('message.send_default_response', '无指令回复'), toggle('welcome.group_welcome', '机器人入群回复'),
    toggle('welcome.new_user_welcome', '新用户首次交互'), toggle('welcome.friend_add_message', '添加好友消息'),
    toggle('maintenance.enabled', '机器人维护模式'), toggle('dedup.enabled', '消息事件去重'),
    toggle('maintenance.reply', '维护时回复提示', true, { when: data => data.maintenance?.enabled === true, full: true }),
  ] },
  { id: 'blacklist', title: '黑名单', description: '每行填写一个 OpenID，可在空格后添加理由', icon: 'shield', column: 0, fields: [
    toggle('blacklist.reply', '黑名单命中时回复模板', true, { full: true, hint: '同一用户或群每 60 秒最多触发一次。' }),
    field('blacklist.user_list', '用户黑名单', 'blacklist', {}, { placeholder: '用户 OpenID 理由（可选）' }),
    field('blacklist.group_list', '群黑名单', 'blacklist', {}, { placeholder: '群 OpenID 理由（可选）' }),
  ] },
  { id: 'non-at', title: '全量环境', description: '未 @ 机器人时的消息响应规则', icon: 'globe', column: 1, fields: [
    toggle('non_at_message.enabled', '未 @ 时正常响应指令', true, { full: true }),
    toggle('non_at_message.ignore_at_other_bot', '忽略仅 @ 其他机器人的消息', true, { full: true, when: data => data.non_at_message?.enabled !== false }),
    toggle('non_at_message.ignore_at_other_user', '忽略仅 @ 其他用户的消息', true, { full: true, when: data => data.non_at_message?.enabled !== false }),
    toggle('non_at_message.ignore_bot_sender', '屏蔽其他机器人发送的消息', true, { full: true }),
    toggle('non_at_message.quiet_at_self', '@ 机器人时抑制无指令回复', false, { full: true }),
    toggle('non_at_message.strip_bot_name_at', '匹配前剥离开头 @ 机器人名称', false, { full: true }),
    field('non_at_message.group_whitelist', '群白名单', 'list', [], { full: true, placeholder: '多个群 OpenID 用英文逗号分隔', when: data => data.non_at_message?.enabled === false }),
  ] },
  { id: 'user-id', title: '用户 ID 模式', description: '开启后 openid 与 union_openid 反转，请确认需要后调整', icon: 'people', column: 0, caution: true, fields: [
    toggle('identity.use_union_id_for_group', '群聊使用 union_openid'), toggle('identity.use_union_id_for_channel', '频道使用 union_openid'),
  ] },
]

export const SETTINGS_SECTIONS = [
  { id: 'server', title: 'HTTP 服务器', description: '服务监听地址与端口', icon: 'server', column: 0, fields: [
    field('server.host', '监听地址', 'host', '0.0.0.0', { placeholder: '0.0.0.0 或 ::，多个地址用逗号分隔' }), number('server.port', '端口', 5001, { min: 1, max: 65535 }),
  ] },
  { id: 'web', title: 'Web 面板', description: '面板访问与品牌信息', icon: 'desktop', column: 0, fields: [
    field('web.admin_password', '管理密码', 'password'), field('web.framework_name', '框架名称', 'text', 'Elaina'),
    field('web.favicon_url', '图标 URL', 'text', '', { full: true, placeholder: 'https://…' }),
    field('web.pc_title_suffix', 'PC 标题后缀'), field('web.login_title_suffix', '登录标题后缀'),
  ] },
  { id: 'logging', title: '日志', description: '存储策略与消息原始数据', icon: 'document-text', column: 1, fields: [
    field('logging.level', '日志等级', 'select', 'INFO', { options: ['DEBUG', 'INFO', 'WARNING', 'ERROR'] }), field('logging.dir', '日志目录', 'text', 'log'),
    number('logging.insert_interval', '写入间隔（秒）', 2, { min: 0 }), number('logging.batch_size', '批量写入条数', 0, { min: 0, hint: '0 表示不限条数' }),
    number('logging.retention_days', '保留天数', 5, { min: 0, hint: '0 表示永久保留' }),
  ], switches: [toggle('logging.wal_mode', 'WAL 模式', true), toggle('logging.record_raw_message', '记录消息原始响应', true)], note: '关闭原始响应记录可减少日志体积；关闭期间的历史报错将无法读取原始内容。' },
  { id: 'pip', title: '依赖管理', description: '插件与模块的依赖安装', icon: 'cube', column: 0, fields: [
    toggle('pip.auto_install', '自动安装依赖', true, { full: true }), field('pip.mirror', 'pip 镜像源', 'text', '', { full: true, placeholder: '留空使用默认镜像源' }),
  ] },
  { id: 'restart', title: '定时重启', description: '按每天固定时间或间隔执行', icon: 'time', column: 1, fields: [
    toggle('restart.enabled', '启用定时重启', false, { full: true }),
    field('restart.mode', '模式', 'select', 'daily', { options: [{ value: 'daily', label: '每天固定时间' }, { value: 'interval', label: '每隔 N 小时' }] }),
    field('restart.daily_time', '重启时间', 'time', '04:00', { when: data => (data.restart?.mode || 'daily') === 'daily' }),
    number('restart.interval_hours', '间隔小时', 24, { min: 1, when: data => data.restart?.mode === 'interval' }),
  ] },
  { id: 'auto-update', title: '自动更新', description: '自动检查框架新版本', icon: 'cloud-download', column: 0, fields: [
    toggle('auto_update.enabled', '启用自动更新', false, { full: true }), number('auto_update.interval_seconds', '检查间隔（秒）', 600, { min: 60, full: true }),
  ] },
  { id: 'statistics', title: '统计', description: '用户使用数据与每日聚合时间', icon: 'stats-chart', column: 1, fields: [
    toggle('statistics.enabled', '用户使用数据统计', true, { full: true }),
    number('statistics.schedule_hour', '每日统计 · 时', 4, { min: 0, max: 23 }), number('statistics.schedule_minute', '每日统计 · 分', 0, { min: 0, max: 59 }),
  ] },
]

export function getFieldValue(data, field) {
  const value = field.path.split('.').reduce((current, key) => current?.[key], data)
  return value ?? field.fallback
}

export function setFieldValue(data, path, value) {
  const keys = path.split('.')
  const last = keys.pop()
  const parent = keys.reduce((current, key) => {
    if (!current[key] || typeof current[key] !== 'object' || Array.isArray(current[key])) current[key] = {}
    return current[key]
  }, data)
  parent[last] = value
}
