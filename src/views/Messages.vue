<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useAppStore } from '../stores/app'
import { on, off, isConnected } from '../utils/ws'
import axios from '../utils/axios'
import { parseMessageReference } from '../utils/messageReference'
import { safeResourceUrl, parseMessageMedia } from '../utils/messageMarkdown'
import { copyText } from '../utils/clipboard'
import SvgIcon from '../components/SvgIcon.vue'
import MessageAvatar from '../components/messages/MessageAvatar.vue'
import MessageContent from '../components/messages/MessageContent.vue'
import GroupStatus from '../components/messages/GroupStatus.vue'

const app = useAppStore()
let _unmounted = false
const nickCache = Object.create(null)
const PAGE = 50
const HISTORY_PAGE = 60
const isMobile = ref(window.innerWidth < 768)
const mobileView = ref('list')
const chatType = ref('full_access')
const chatSearch = ref('')
const chats = ref([])
const page = ref(1)
const total = ref(0)
const chatsLoading = ref(false)
const chatsError = ref('')
const chatsIndexing = ref(false)
const current = ref(null)
const history = ref([])
const historyRef = ref(null)
const msgType = ref('markdown')
const msgText = ref('')
const imgFile = ref(null)
const mediaFile = ref(null)
const sending = ref(false)
const sendErr = ref('')
const recalling = ref('')
const rawDataMsg = ref(null)
const quotedMsg = ref(null)
const lastMsgId = ref('')
const oldestDate = ref('')
const historyCursor = ref('')
const hasMore = ref(true)
const loadingOlder = ref(false)
const mediaFileType = ref('1')
const mediaAccept = computed(
  () => ({ 1: 'image/*', 2: 'video/*', 3: 'audio/*', 4: '*/*' })[mediaFileType.value] || '*/*'
)
const sendMode = ref('default')
const customSendId = ref('')
const arkTpl = ref('23')
const arkFields = ref({})
const arkList = ref('')
const cardFields = ref({})
const imgPreview = ref('')
const msgTypeOptions = [
  { value: 'markdown', label: 'Markdown' },
  { value: 'text', label: '文本' },
  { value: 'media', label: '媒体' },
  { value: 'ark', label: 'ARK' },
  { value: 'card', label: '卡片' }
]
const mediaTypeOptions = [
  { value: '1', label: '图片' },
  { value: '2', label: '视频' },
  { value: '3', label: '语音' },
  { value: '4', label: '文件' }
]
const sendModeOptions = [
  { value: 'default', label: '默认 (全量群主动/其他被动)' },
  { value: 'passive', label: '被动 (msg_id)' },
  { value: 'active', label: '主动' },
  { value: 'custom_msg_id', label: '自定义 msg_id' },
  { value: 'custom_event_id', label: '自定义事件 ID' }
]
const isCustomSendMode = computed(
  () => sendMode.value === 'custom_msg_id' || sendMode.value === 'custom_event_id'
)

const apiChatType = computed(() =>
  chatType.value === 'full_access' || chatType.value === 'remark' ? 'group' : chatType.value
)
const groupRoles = ref({})
const botIsGroupAdmin = ref(false)
const currentGroupMembers = computed(() => {
  const count = Number(current.value?.group_member_num)
  return Number.isFinite(count) && count > 0 ? `${count.toLocaleString()} 人` : '人数未知'
})
const muteModalVisible = ref(false)
const muteTarget = ref(null)
const muteMinutes = ref(30)
const muting = ref(false)
const muteError = ref('')
const groupActionMessage = ref('')
const remarkInput = ref('')
const remarkQqInput = ref('')
const remarkEditing = ref(null)
const remarkModalVisible = ref(false)
const remarkError = ref('')
const remarkSaving = ref(false)
const startChatModalVisible = ref(false)
const startChatType = ref('group')
const startChatOpenid = ref('')
const startChatError = ref('')
const startingChat = ref(false)
const addRemarkModalVisible = ref(false)
const addRemarkOpenid = ref('')
const addRemarkName = ref('')
const addRemarkQq = ref('')
const refreshingGroup = ref('')
const refreshingPage = ref(false)
const groupRefreshError = ref('')
const historyLoading = ref(false)
const historyError = ref('')
const olderError = ref('')
const wsConnected = ref(isConnected())
const searchVisible = ref(false)
const searchRef = ref(null)
const messageSearch = ref('')
const messageDirection = ref('all')
const detailsVisible = ref(false)
const userGroupsVisible = ref(false)
const userGroupsTarget = ref(null)
const userGroups = ref([])
const userGroupsLoading = ref(false)
const userGroupsError = ref('')
let userGroupsRequest = 0

async function openUserGroups(user) {
  if (!user?.user_id) return
  const request = ++userGroupsRequest
  userGroupsTarget.value = { ...user, appid: currentAppid(user) }
  userGroupsVisible.value = true
  userGroupsLoading.value = true
  userGroupsError.value = ''
  userGroups.value = []
  try {
    const res = await axios.post('/api/message/user-groups', {
      user_id: user.user_id, appid: userGroupsTarget.value.appid
    })
    if (_unmounted || request !== userGroupsRequest) return
    userGroups.value = res.data?.data?.groups || []
  } catch (error) {
    if (!_unmounted && request === userGroupsRequest)
      userGroupsError.value = error.normalizedMessage || '群聊加载失败，请重试'
  } finally {
    if (request === userGroupsRequest) userGroupsLoading.value = false
  }
}

async function openUserGroup(group) {
  userGroupsVisible.value = false
  if (group.appid && group.appid !== app.currentBotId) app.switchBot(group.appid)
  chatType.value = 'group'
  await nextTick()
  await selectChat(group)
}

const groupMembersVisible = ref(false)
const groupMembersTarget = ref(null)
const groupMembers = ref([])
const groupMembersLoading = ref(false)
const groupMembersError = ref('')
let groupMembersRequest = 0

function maskedOpenid(openid) {
  const value = String(openid || '')
  return `${value.slice(0, 3)}**${value.slice(-3)}`
}

async function openGroupMembers(group = current.value) {
  if (!group?.chat_id) return
  const request = ++groupMembersRequest
  groupMembersTarget.value = { ...group, appid: group.appid || app.currentBotId || '' }
  groupMembersVisible.value = true
  groupMembersLoading.value = true
  groupMembersError.value = ''
  groupMembers.value = []
  try {
    const res = await axios.post('/api/message/group-members', {
      group_id: group.chat_id, appid: groupMembersTarget.value.appid
    })
    if (_unmounted || request !== groupMembersRequest) return
    groupMembers.value = res.data?.data?.members || []
  } catch (error) {
    if (!_unmounted && request === groupMembersRequest)
      groupMembersError.value = error.normalizedMessage || '群用户加载失败，请重试'
  } finally {
    if (request === groupMembersRequest) groupMembersLoading.value = false
  }
}

async function openMemberChat(member) {
  groupMembersVisible.value = false
  if (member.appid && member.appid !== app.currentBotId) app.switchBot(member.appid)
  chatType.value = 'user'
  await nextTick()
  sendMode.value = 'active'
  await selectChat({ chat_id: member.user_id, appid: member.appid })
}

const sendOptionsOpen = ref(false)
const showPreview = ref(false)
const editorRef = ref(null)
watch([msgText, editorRef], resizeEditor, { flush: 'post' })
const stickToBottom = ref(true)
const newMessageCount = ref(0)
const copiedMessage = ref('')
const lightboxSrc = ref('')
const lightboxCloseRef = ref(null)
let lightboxPreviousFocus = null
let _copyTimer = null
let historyResizeObserver = null

const arkFieldOptions = computed(() => {
  const labels = {
    '#DESC#': '描述',
    '#PROMPT#': '提示',
    '#TITLE#': '标题',
    '#METADESC#': '详情',
    '#IMG#': '图片 URL',
    '#LINK#': '跳转 URL',
    '#SUBTITLE#': '副标题',
    '#METATITLE#': '标题',
    '#METASUBTITLE#': '副标题',
    '#METACOVER#': '封面 URL',
    '#METAURL#': '跳转 URL'
  }
  const keys =
    arkTpl.value === '24'
      ? ['#DESC#', '#PROMPT#', '#TITLE#', '#METADESC#', '#IMG#', '#LINK#', '#SUBTITLE#']
      : arkTpl.value === '37'
        ? ['#PROMPT#', '#METATITLE#', '#METASUBTITLE#', '#METACOVER#', '#METAURL#']
        : ['#DESC#', '#PROMPT#']
  return keys.map((key) => ({ key, label: labels[key] }))
})
const chatTypeOptions = [
  { value: 'full_access', label: '全量群' },
  { value: 'remark', label: '备注' },
  { value: 'group', label: '群聊' },
  { value: 'user', label: '私聊' }
]
const directionOptions = [
  { value: 'all', label: '全部消息' },
  { value: 'received', label: '收到' },
  { value: 'sent', label: '发出' }
]
const markdownTools = [
  { label: '加粗', symbol: 'B', prefix: '**', suffix: '**', sample: '加粗文字', class: 'tool-bold' },
  { label: '斜体', symbol: 'I', prefix: '*', suffix: '*', sample: '斜体文字', class: 'tool-italic' },
  { label: '插入链接', icon: 'link', prefix: '[', suffix: '](https://example.com)', sample: '链接文字' },
  { label: '行内代码', icon: 'code', prefix: '`', suffix: '`', sample: 'code' },
  { label: '代码块', symbol: '{ }', prefix: '```text\n', suffix: '\n```', sample: '代码内容', block: true },
  { label: '引用', symbol: '❞', prefix: '> ', sample: '引用内容', block: true },
  { label: '列表', icon: 'list', prefix: '- ', sample: '列表内容', block: true },
  {
    label: '插入图片',
    icon: 'image',
    prefix: '![',
    suffix: '](https://example.com/image.png)',
    sample: '图片描述'
  }
]
const visibleMessages = computed(() => {
  const query = messageSearch.value.trim().toLocaleLowerCase()
  return history.value.filter((message) => {
    if (messageDirection.value === 'sent' && (!message.is_self || message.event_type)) return false
    if (messageDirection.value === 'received' && (message.is_self || message.event_type)) return false
    return (
      !query ||
      [message.content, message.nickname, message.user_id, message.message_id].some((value) =>
        String(value || '')
          .toLocaleLowerCase()
          .includes(query)
      )
    )
  })
})
const isFiltering = computed(() => !!messageSearch.value.trim() || messageDirection.value !== 'all')
const canSend = computed(() => {
  if (!current.value || sending.value) return false
  if (msgType.value === 'ark') return buildArkKv().length > 0
  if (msgType.value === 'card') return Object.keys(buildCardContent()).length > 0
  return (
    !!msgText.value.trim() ||
    (msgType.value === 'text' && !!imgFile.value) ||
    (msgType.value === 'media' && !!mediaFile.value)
  )
})
const placeholder = computed(() =>
  msgType.value === 'markdown'
    ? '写下消息，支持 Markdown 格式…'
    : msgType.value === 'media'
      ? '粘贴资源 URL，或在下方上传文件…'
      : '写下你想发送的消息…'
)
const quotedPreview = computed(() => (quotedMsg.value ? buildQuotePreview(quotedMsg.value) : ''))
const olderBtnLabel = computed(() =>
  historyCursor.value ? '加载更早消息' : !oldestDate.value || oldestDate.value >= localDate() ? '加载昨日消息' : '加载更早消息'
)

function localDate() {
  const date = new Date()
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
function dateLabel(timestamp) {
  const date = String(timestamp || '').slice(0, 10)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return '消息记录'
  if (date === localDate()) return '今天'
  return `${date.slice(0, 4)} 年 ${Number(date.slice(5, 7))} 月 ${Number(date.slice(8, 10))} 日`
}
function messageKey(message) {
  return `${message.appid || ''}:${message.message_id || `${message.id}:${message.timestamp}`}`
}
function handleResize() {
  isMobile.value = window.innerWidth < 768
  if (stickToBottom.value) nextTick(scrollBottom)
}
function changePage(delta) {
  page.value += delta
  fetchChats()
}
function dismissConversationNotice() {
  groupRefreshError.value = ''
  groupActionMessage.value = ''
}
function resetMessageFilters() {
  messageSearch.value = ''
  messageDirection.value = 'all'
}
function goBackToList() {
  resetConversation()
  mobileView.value = 'list'
}
function selectMsgType(value) {
  msgType.value = value
  showPreview.value = false
  sendErr.value = ''
}
function avatarUrl(appid, uid) {
  return `https://q.qlogo.cn/qqapp/${encodeURIComponent(appid)}/${encodeURIComponent(uid)}/0`
}
function getBotAvatar(appid) {
  return app.bots.find((bot) => bot.appid === appid)?.avatar || ''
}
function qqAvatar(qq) {
  return `https://q1.qlogo.cn/g?b=qq&nk=${encodeURIComponent(qq)}&s=100`
}
function groupQqAvatar(qq) {
  return `https://p.qlogo.cn/gh/${encodeURIComponent(qq)}/${encodeURIComponent(qq)}/100/`
}
function chatAvatar(chat) {
  return chatType.value === 'user' && chat.appid
    ? avatarUrl(chat.appid, chat.chat_id)
    : chat.group_qq
      ? groupQqAvatar(chat.group_qq)
      : ''
}
function messageAvatar(message) {
  if (message.is_self) return message.bot_qq ? qqAvatar(message.bot_qq) : getBotAvatar(message.appid)
  return message.appid && message.user_id ? avatarUrl(message.appid, message.user_id) : ''
}
function chatPreview(chat) {
  return (
    String(chat.last_content || '')
      .replace(/\n\[keyboard\][\s\S]*$/, '')
      .replace(/\s+/g, ' ')
      .trim() || '暂无消息，点击查看会话'
  )
}
async function previewImg(src) {
  const url = safeResourceUrl(src)
  if (!url) return
  lightboxPreviousFocus = document.activeElement
  lightboxSrc.value = url
  await nextTick()
  lightboxCloseRef.value?.focus()
}
function closeLightbox() {
  lightboxSrc.value = ''
  lightboxPreviousFocus?.focus?.({ preventScroll: true })
}
function onEscape(event) {
  if (event.key === 'Escape' && lightboxSrc.value) closeLightbox()
}
function onConnectionClose() {
  wsConnected.value = false
}
async function toggleSearch() {
  searchVisible.value = !searchVisible.value
  if (searchVisible.value) {
    await nextTick()
    searchRef.value?.focus()
  } else messageSearch.value = ''
}
async function insertMarkdown(tool) {
  showPreview.value = false
  await nextTick()
  const editor = editorRef.value
  if (!editor) return
  const start = editor.selectionStart,
    end = editor.selectionEnd
  const selection = msgText.value.slice(start, end) || tool.sample
  const prefix = (tool.block && start > 0 && msgText.value[start - 1] !== '\n' ? '\n' : '') + tool.prefix
  msgText.value =
    msgText.value.slice(0, start) + prefix + selection + (tool.suffix || '') + msgText.value.slice(end)
  await nextTick()
  editor.focus()
  editor.setSelectionRange(start + prefix.length, start + prefix.length + selection.length)
}
async function copyMessage(message) {
  const ok = await copyText(message.content)
  if (!ok) {
    window.$message?.error('复制失败，请手动选择内容')
    return
  }
  copiedMessage.value = messageKey(message)
  clearTimeout(_copyTimer)
  _copyTimer = setTimeout(() => {
    copiedMessage.value = ''
  }, 1800)
}
async function copyValue(value) {
  if (await copyText(value)) window.$message?.success('已复制')
}

function shortTime(t) {
  return t ? (t.length > 10 ? t.slice(11, 16) : t) : ''
}
function chatDisplayName(chat) {
  if (!chat) return ''
  if (chatType.value === 'user') return chat.nickname || chat.chat_id
  return chat.remark || chat.group_name || chat.chat_id
}

function isAuditId(id) {
  return id && id.startsWith('msg_auditid_')
}
function canRecall(m) {
  if (!m.message_id || isAuditId(m.message_id)) return false
  return !!m.is_self || (apiChatType.value === 'group' && botIsGroupAdmin.value)
}
function canQuote(m) {
  return !!m.message_id && !isAuditId(m.message_id) && !m._recalled && !m._audit_rejected
}
async function showRaw(m) {
  rawDataMsg.value = rawDataMsg.value === m ? null : m
  if (rawDataMsg.value !== m || !m.raw_available || m.raw_message || m._rawLoaded || m._rawLoading) return
  m._rawLoading = true
  m._rawError = ''
  const selection = _selectId
  try {
    const res = await axios.post('/api/message/history', {
      chat_type: apiChatType.value, chat_id: current.value.chat_id, appid: m.appid || currentAppid(),
      raw_id: m.id, date: m.date || String(m.timestamp).slice(0, 10)
    })
    if (_unmounted || selection !== _selectId) return
    m.raw_message = res.data?.data?.raw_message || ''
    m._rawLoaded = true
  } catch {
    m._rawError = '原始数据加载失败，请重新打开重试'
  } finally {
    m._rawLoading = false
  }
}
function formatRaw(m) {
  if (m.raw_message) {
    try {
      return JSON.stringify(JSON.parse(m.raw_message), null, 2)
    } catch {}
    return m.raw_message
  }
  return JSON.stringify(
    {
      message_id: m.message_id,
      user_id: m.user_id,
      content: m.content,
      timestamp: m.timestamp,
      source: m.source,
      is_self: m.is_self,
      appid: m.appid
    },
    null,
    2
  )
}

function quoteAuthor(m) {
  return m.is_self ? '我' : m.nickname || m.user_id || '未知用户'
}
function buildQuotePreview(m) {
  if (m._media)
    return [m._media.type ? `[${m._media.type}]` : '[媒体]', m._media.text, m._media.src]
      .filter(Boolean)
      .join(' ')
  return (
    String(m.content || '')
      .replace(/\n\[keyboard\] [\s\S]*$/, '')
      .replace(/\s+/g, ' ')
      .trim() || '空消息'
  )
}
function quoteTargetText(q) {
  return q?.text || (q?.id ? `ID: ${q.id}` : '引用消息')
}
function prepareMessage(m) {
  m._media = parseMessageMedia(m.content)
  m._recalled = !!m.recalled
  m._quote = m.reference || parseMessageReference(m)
  if (!m.is_self && m.user_id && m.nickname && !/^(?:rt_|lc_rt_)/.test(String(m.id))) nickCache[m.user_id] = m.nickname
  return m
}
function resolveMessageReferences(messages) {
  const byReferenceId = Object.create(null),
    byMessageId = Object.create(null)
  for (const m of messages) {
    if (m.reference_id) byReferenceId[m.reference_id] = m
    if (m.message_id) byMessageId[m.message_id] = m
  }
  for (const m of messages) {
    const q = m._quote
    if (!q?.id) continue
    const target = byReferenceId[q.id] || byMessageId[q.id]
    if (!target || target === m) continue
    q.author = quoteAuthor(target)
    q.text = buildQuotePreview(target)
    q.message_id = target.message_id || ''
  }
}
function quoteMsg(m) {
  if (!canQuote(m)) return
  quotedMsg.value = m
  showPreview.value = false
  nextTick(() => editorRef.value?.focus())
}
function clearQuote() {
  quotedMsg.value = null
}

async function recallMsg(m) {
  if (recalling.value || !m.message_id) return
  const selection = _selectId
  recalling.value = m.message_id
  try {
    const res = await axios.post('/api/message/recall', {
      chat_type: apiChatType.value,
      chat_id: current.value?.chat_id || '',
      appid: m.appid || app.currentBotId || '',
      message_id: m.message_id
    })
    if (res.data?.success) {
      m._recalled = true
    } else if (selection === _selectId) {
      sendErr.value = res.data?.message || '撤回失败'
    }
  } catch (e) {
    if (selection === _selectId) sendErr.value = e.response?.data?.message || e.message || '撤回失败'
  } finally {
    recalling.value = ''
  }
}
function buildCardContent() {
  const f = cardFields.value,
    obj = {}
  for (const k of ['title', 'description', 'pic_url', 'url']) if (f[k]?.trim()) obj[k] = f[k].trim()
  return obj
}

function buildArkKv() {
  const tpl = arkTpl.value,
    f = arkFields.value,
    kv = []
  if (tpl === '24') {
    for (const k of ['#DESC#', '#PROMPT#', '#TITLE#', '#METADESC#', '#IMG#', '#LINK#', '#SUBTITLE#'])
      if (f[k]) kv.push({ key: k, value: f[k] })
  } else if (tpl === '37') {
    for (const k of ['#PROMPT#', '#METATITLE#', '#METASUBTITLE#', '#METACOVER#', '#METAURL#'])
      if (f[k]) kv.push({ key: k, value: f[k] })
  } else {
    if (f['#DESC#']) kv.push({ key: '#DESC#', value: f['#DESC#'] })
    if (f['#PROMPT#']) kv.push({ key: '#PROMPT#', value: f['#PROMPT#'] })
    const lines = arkList.value.trim().split('\n').filter(Boolean)
    if (lines.length) {
      const objs = lines
        .map((l) => {
          const [desc, link] = l.split('|', 2)
          const arr = []
          if (desc?.trim()) arr.push({ key: 'desc', value: desc.trim() })
          if (link?.trim()) arr.push({ key: 'link', value: link.trim() })
          return arr.length ? { obj_kv: arr } : null
        })
        .filter(Boolean)
      if (objs.length) kv.push({ key: '#LIST#', obj: objs })
    }
  }
  return kv
}

let _fetchTimer = null
let _indexPollTimer = null
let _chatController = null
let _chatRequestId = 0
async function fetchChats() {
  if (_unmounted) return
  const requestId = ++_chatRequestId
  clearTimeout(_indexPollTimer)
  _chatController?.abort()
  _chatController = new AbortController()
  chatsLoading.value = true
  chatsError.value = ''
  try {
    const res = await axios.post('/api/message/chats', {
      type: chatType.value,
      search: chatSearch.value,
      appid: app.currentBotId || '',
      page: page.value,
      page_size: PAGE
    }, { signal: _chatController.signal })
    if (_unmounted || requestId !== _chatRequestId) return
    chats.value = res.data?.data?.chats || []
    total.value = res.data?.data?.total ?? chats.value.length
    page.value = res.data?.data?.page || 1
    chatsIndexing.value = res.data?.data?.indexing === true
    if (chatsIndexing.value) _indexPollTimer = setTimeout(fetchChats, 1500)
    syncCurrentChat()
  } catch {
    if (!_unmounted && requestId === _chatRequestId) chatsError.value = '聊天列表加载失败'
  } finally {
    if (!_unmounted && requestId === _chatRequestId) chatsLoading.value = false
  }
}

function memberInfo(uid) {
  return groupRoles.value[uid] || {}
}
function roleLabel(uid) {
  const r = String(memberInfo(uid).role || '')
  if (r === 'owner') return '群主'
  if (r === 'admin') return '管理员'
  if (r) return '群员'
  return ''
}
function roleClass(uid) {
  const r = String(memberInfo(uid).role || '')
  if (r === 'owner') return 'role-owner'
  if (r === 'admin') return 'role-admin'
  return 'role-member'
}
function isBot(uid) {
  return !!memberInfo(uid).is_bot
}
function canMuteMember(m) {
  if (!botIsGroupAdmin.value || apiChatType.value !== 'group' || m.is_self || !m.user_id || isBot(m.user_id))
    return false
  return !['owner', 'admin'].includes(String(memberInfo(m.user_id).role || ''))
}
function currentAppid(m = null) {
  return String(m?.appid || current.value?.appid || app.currentBotId || '')
}
function openMuteModal(m) {
  if (!canMuteMember(m)) return
  muteTarget.value = m
  muteMinutes.value = 30
  muteError.value = ''
  muteModalVisible.value = true
}
function closeMuteModal() {
  if (muting.value) return
  muteModalVisible.value = false
  muteTarget.value = null
  muteError.value = ''
}
async function submitMute() {
  if (
    !muteTarget.value ||
    muting.value ||
    !Number.isFinite(muteMinutes.value) ||
    muteMinutes.value < 1 ||
    muteMinutes.value > 43200
  )
    return
  const selection = _selectId
  muting.value = true
  muteError.value = ''
  groupActionMessage.value = ''
  try {
    const res = await axios.post('/api/message/group-member/mute', {
      group_id: current.value?.chat_id || '',
      user_id: muteTarget.value.user_id,
      appid: currentAppid(muteTarget.value),
      minutes: muteMinutes.value
    })
    if (_unmounted || selection !== _selectId) return
    groupActionMessage.value = res.data?.message || '禁言成功'
    muteModalVisible.value = false
    muteTarget.value = null
  } catch (e) {
    if (selection === _selectId) muteError.value = e.response?.data?.message || e.message || '禁言操作失败'
  } finally {
    muting.value = false
  }
}

const _rolesCache = {}
const _ROLES_CACHE_TTL = 120000
async function fetchGroupRoles(groupId, appid = '') {
  if (!groupId) {
    groupRoles.value = {}
    botIsGroupAdmin.value = false
    return
  }
  const cacheKey = `${appid}:${groupId}`
  const ids = [...new Set(history.value.map(message => message.user_id).filter(Boolean))]
  let cached = _rolesCache[cacheKey]
  if (!cached || Date.now() - cached.ts >= _ROLES_CACHE_TTL) {
    cached = _rolesCache[cacheKey] = { data: {}, known: new Set(), botIsAdmin: false, ts: Date.now() }
  }
  const missing = ids.filter(id => !cached.known.has(id))
  if (!missing.length && cached.loaded) {
    if (current.value?.chat_id === groupId && currentAppid() === String(appid)) {
      groupRoles.value = cached.data
      botIsGroupAdmin.value = cached.botIsAdmin
    }
    return
  }
  try {
    const res = await axios.post('/api/message/group-roles', { group_id: groupId, appid, user_ids: missing.slice(0, 500) })
    const data = res.data?.data || {}
    const botIsAdmin = res.data?.bot_is_admin === true
    Object.assign(cached.data, data)
    missing.slice(0, 500).forEach(id => cached.known.add(id))
    cached.botIsAdmin = botIsAdmin
    cached.loaded = true
    if (!_unmounted && current.value?.chat_id === groupId && currentAppid() === String(appid)) {
      groupRoles.value = { ...cached.data }
      botIsGroupAdmin.value = botIsAdmin
    }
  } catch {
    if (current.value?.chat_id === groupId && currentAppid() === String(appid)) {
      groupRoles.value = {}
      botIsGroupAdmin.value = false
    }
  }
}

async function setRemark() {
  if (!remarkEditing.value || remarkSaving.value) return false
  const groupId = remarkEditing.value
  const remark = remarkInput.value.trim(),
    groupQq = remarkQqInput.value.trim()
  remarkSaving.value = true
  remarkError.value = ''
  try {
    await axios.post('/api/message/remarks', {
      group_id: groupId,
      remark,
      group_qq: groupQq
    })
    if (current.value?.chat_id === groupId) Object.assign(current.value, { remark, group_qq: groupQq })
    remarkModalVisible.value = false
    remarkEditing.value = null
    remarkInput.value = ''
    remarkQqInput.value = ''
    fetchChats()
  } catch (error) {
    remarkError.value = error.normalizedMessage || '备注保存失败，请重试'
    return false
  } finally {
    remarkSaving.value = false
  }
}

function startRemark(c) {
  remarkEditing.value = c.chat_id
  remarkInput.value = c.remark || ''
  remarkQqInput.value = c.group_qq || ''
  remarkError.value = ''
  remarkModalVisible.value = true
}
function cancelRemark() {
  remarkModalVisible.value = false
  remarkEditing.value = null
  remarkInput.value = ''
  remarkQqInput.value = ''
}

function openAddRemark() {
  remarkError.value = ''
  addRemarkOpenid.value = ''
  addRemarkName.value = ''
  addRemarkQq.value = ''
  addRemarkModalVisible.value = true
}
async function submitAddRemark() {
  const openid = addRemarkOpenid.value.trim()
  if (remarkSaving.value) return false
  if (!openid) {
    remarkError.value = '请输入群 OpenID'
    return false
  }
  remarkSaving.value = true
  remarkError.value = ''
  try {
    await axios.post('/api/message/remarks', {
      group_id: openid,
      remark: addRemarkName.value.trim(),
      group_qq: addRemarkQq.value.trim()
    })
    addRemarkModalVisible.value = false
    fetchChats()
  } catch (error) {
    remarkError.value = error.normalizedMessage || '备注添加失败，请重试'
    return false
  } finally {
    remarkSaving.value = false
  }
}
function cancelAddRemark() {
  addRemarkModalVisible.value = false
}
function fetchChatsDebounced() {
  if (_fetchTimer) return
  _fetchTimer = setTimeout(() => {
    _fetchTimer = null
    fetchChats()
  }, 5000)
}

function resetConversation() {
  _selectId++
  current.value = null
  history.value = []
  historyLoading.value = false
  historyError.value = ''
  olderError.value = ''
  sendErr.value = ''
  lastMsgId.value = ''
  oldestDate.value = ''
  historyCursor.value = ''
  hasMore.value = true
  loadingOlder.value = false
  groupRoles.value = {}
  botIsGroupAdmin.value = false
  groupActionMessage.value = ''
  groupRefreshError.value = ''
  rawDataMsg.value = null
  quotedMsg.value = null
  muteTarget.value = null
  muteModalVisible.value = false
  messageSearch.value = ''
  messageDirection.value = 'all'
  searchVisible.value = false
  detailsVisible.value = false
  showPreview.value = false
  newMessageCount.value = 0
  clearImg()
  clearMediaFile()
}

let _refreshSelection = null
async function refreshCurrentChat(prune = false) {
  if (!current.value || _unmounted || historyLoading.value) return
  const selection = _selectId
  if (_refreshSelection === selection) return
  _refreshSelection = selection
  const before = prune ? new Set(history.value.map(messageKey)) : null
  try {
    const res = await axios.post('/api/message/history', {
      chat_type: apiChatType.value,
      chat_id: current.value.chat_id,
      appid: currentAppid(), limit: HISTORY_PAGE, include_raw: false
    })
    if (_unmounted || selection !== _selectId || !current.value) return
    const msgs = res.data?.data?.messages || []
    lastMsgId.value = res.data?.data?.last_msg_id || lastMsgId.value
    if (prune && stickToBottom.value) {
      const seen = new Set(msgs.map(messageKey))
      const live = history.value.filter(message => !before.has(messageKey(message)) && !seen.has(messageKey(message)))
      msgs.forEach(prepareMessage)
      history.value = [...msgs, ...live].sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)))
      historyCursor.value = res.data?.data?.next_cursor || ''
      oldestDate.value = res.data?.data?.oldest_date || localDate()
      hasMore.value = res.data?.data?.has_more === true
      collectMentions(msgs)
      resolveMessageReferences(history.value)
      await nextTick()
      scrollBottom()
      return
    }
    const seen = new Set(history.value.map(messageKey))
    const fresh = msgs.filter((message) => !seen.has(messageKey(message)))
    if (!fresh.length) return
    const stick = stickToBottom.value
    for (const message of fresh) prepareMessage(message)
    collectMentions(fresh)
    history.value = [...history.value, ...fresh].sort((a, b) =>
      String(a.timestamp).localeCompare(String(b.timestamp))
    )
    resolveMessageReferences(history.value)
    if (!stick) newMessageCount.value += fresh.length
    await nextTick()
    if (stick) scrollBottom()
  } catch {
    /* Keep the current history visible while reconnecting. */
  } finally {
    if (_refreshSelection === selection) _refreshSelection = null
  }
}

function onWsReconnect() {
  wsConnected.value = true
  refreshCurrentChat()
  fetchChats()
}
function onVisibleChange() {
  if (document.visibilityState === 'visible') {
    refreshCurrentChat()
    fetchChats()
  }
}

function openStartChat() {
  startChatType.value = apiChatType.value
  startChatOpenid.value = ''
  startChatError.value = ''
  startChatModalVisible.value = true
}

async function submitStartChat() {
  if (startingChat.value) return false
  const openid = startChatOpenid.value.trim()
  if (!openid || /\s/.test(openid)) {
    startChatError.value = '请输入有效的 OpenID，不能包含空白字符'
    return false
  }
  startingChat.value = true
  try {
    chatType.value = startChatType.value
    // Let the tab watcher reset the old conversation before selecting the target.
    await nextTick()
    const existing = chats.value.find((chat) => chat.chat_id === openid)
    startChatModalVisible.value = false
    sendMode.value = 'active'
    await selectChat(existing || { chat_id: openid, appid: app.currentBotId || '' })
    await nextTick()
    editorRef.value?.focus()
  } finally {
    startingChat.value = false
  }
  return false
}

let _selectId = 0
async function selectChat(chat) {
  resetConversation()
  const selection = _selectId
  current.value = chat
  msgText.value = ''
  stickToBottom.value = true
  if (isMobile.value) mobileView.value = 'chat'
  historyLoading.value = true
  try {
    const res = await axios.post('/api/message/history', {
      chat_type: apiChatType.value,
      chat_id: chat.chat_id,
      appid: currentAppid(), limit: HISTORY_PAGE, include_raw: false
    })
    if (_unmounted || selection !== _selectId) return
    const msgs = res.data?.data?.messages || []
    for (const message of msgs) prepareMessage(message)
    resolveMessageReferences(msgs)
    collectMentions(msgs)
    // Logs may arrive over WS while the initial HTTP request is in flight.
    const seen = new Set(msgs.map(messageKey))
    history.value = [...msgs, ...history.value.filter((message) => !seen.has(messageKey(message)))].sort(
      (a, b) => String(a.timestamp).localeCompare(String(b.timestamp))
    )
    resolveMessageReferences(history.value)
    const latestReply = [...history.value]
      .reverse()
      .find(
        (message) =>
          !message.is_self && message.message_id && String(message.timestamp || '').startsWith(localDate())
      )
    lastMsgId.value = latestReply?.message_id || res.data?.data?.last_msg_id || ''
    if (apiChatType.value === 'group') fetchGroupRoles(chat.chat_id, currentAppid())
    oldestDate.value = res.data?.data?.oldest_date || localDate()
    historyCursor.value = res.data?.data?.next_cursor || ''
    hasMore.value = res.data?.data?.has_more !== false
    historyLoading.value = false
    await nextTick()
    scrollBottom()
  } catch (error) {
    if (selection === _selectId && !_unmounted) {
      historyError.value = error.normalizedMessage || '消息加载失败，请重试'
      hasMore.value = false
    }
  } finally {
    if (selection === _selectId && !_unmounted) historyLoading.value = false
  }
}

async function loadOlder() {
  if (loadingOlder.value || historyLoading.value || !hasMore.value || !current.value || !oldestDate.value)
    return
  const selection = _selectId
  loadingOlder.value = true
  olderError.value = ''
  try {
    const res = await axios.post('/api/message/history', {
      chat_type: apiChatType.value,
      chat_id: current.value.chat_id,
      appid: currentAppid(),
      ...(historyCursor.value ? { cursor: historyCursor.value } : { before_date: oldestDate.value }),
      limit: HISTORY_PAGE, include_raw: false
    })
    if (_unmounted || selection !== _selectId) return
    const msgs = res.data?.data?.messages || []
    const seen = new Set(history.value.map(messageKey))
    const fresh = msgs.filter((message) => !seen.has(messageKey(message)))
    for (const message of fresh) prepareMessage(message)
    collectMentions(fresh)
    const el = historyRef.value
    const prevHeight = el?.scrollHeight || 0
    const prevTop = el?.scrollTop || 0
    stickToBottom.value = false
    history.value = [...fresh, ...history.value]
    resolveMessageReferences(history.value)
    const nextDate = res.data?.data?.oldest_date
    const nextCursor = res.data?.data?.next_cursor || ''
    hasMore.value = res.data?.data?.has_more !== false && (nextCursor ? nextCursor !== historyCursor.value : !!nextDate && nextDate < oldestDate.value)
    historyCursor.value = nextCursor
    oldestDate.value = nextDate || oldestDate.value
    if (apiChatType.value === 'group') fetchGroupRoles(current.value.chat_id, currentAppid())
    await nextTick()
    if (el) el.scrollTop = prevTop + el.scrollHeight - prevHeight
  } catch (error) {
    if (selection === _selectId) olderError.value = error.normalizedMessage || '历史消息加载失败'
  } finally {
    if (selection === _selectId) loadingOlder.value = false
  }
}

function onHistoryScroll() {
  stickToBottom.value = isNearBottom()
  if (stickToBottom.value) newMessageCount.value = 0
}
function onMediaLoad() {
  if (stickToBottom.value && !loadingOlder.value) nextTick(scrollBottom)
}

async function refreshMsgId() {
  if (!current.value) return
  const selection = _selectId
  try {
    const res = await axios.post('/api/message/history', {
      chat_type: apiChatType.value,
      chat_id: current.value.chat_id,
      appid: currentAppid(),
      limit: 1, include_raw: false
    })
    if (!_unmounted && selection === _selectId)
      lastMsgId.value = res.data?.data?.last_msg_id || lastMsgId.value
  } catch {
    /* Existing reply ID remains available. */
  }
}

function requestGroupRefresh(chat) {
  return axios.post('/api/message/group-info/refresh', {
    group_id: chat.chat_id,
    appid: chat.appid || app.currentBotId || ''
  })
}

function isHandledGroupError(error) {
  const data = error.response?.data
  return !!(data?.removed || data?.left_group)
}

function syncCurrentChat() {
  if (!current.value) return
  const refreshed = chats.value.find(
    (c) => c.chat_id === current.value.chat_id && (!current.value.appid || c.appid === current.value.appid)
  )
  if (refreshed) Object.assign(current.value, refreshed)
}

async function refreshGroup(chat) {
  let removed = false
  let groupInfo
  const selection = _selectId
  try {
    const response = await requestGroupRefresh(chat)
    groupInfo = response.data?.data?.group_info
  } catch (error) {
    if (!isHandledGroupError(error)) throw error
    removed = true
  }
  await fetchChats()
  if (removed && current.value?.chat_id === chat.chat_id && current.value?.appid === chat.appid) {
    resetConversation()
    mobileView.value = 'list'
  } else if (
    groupInfo && selection === _selectId &&
    current.value?.chat_id === chat.chat_id && current.value?.appid === chat.appid
  ) {
    current.value.union_group_openid = groupInfo.union_group_openid || ''
  }
}

async function refreshGroupInfo() {
  const chat = current.value
  if (!chat?.chat_id || apiChatType.value !== 'group' || refreshingGroup.value || refreshingPage.value) return
  const selection = _selectId
  const appid = currentAppid()
  refreshingGroup.value = chat.chat_id
  groupRefreshError.value = ''
  try {
    await refreshGroup(chat)
    if (_unmounted || selection !== _selectId) return
    const cacheKey = `${appid}:${chat.chat_id}`
    delete _rolesCache[cacheKey]
    await fetchGroupRoles(chat.chat_id, appid)
  } catch (e) {
    if (selection === _selectId)
      groupRefreshError.value = e.response?.data?.message || e.message || '群信息更新失败'
  } finally {
    refreshingGroup.value = ''
  }
}

async function refreshPageGroups() {
  const groups = chats.value.filter((c) => c.chat_id).slice(0, 25)
  if (apiChatType.value !== 'group' || !groups.length || refreshingPage.value || refreshingGroup.value) return
  refreshingPage.value = true
  const context = `${chatType.value}:${app.currentBotId}`
  groupRefreshError.value = ''
  let failed = 0
  for (
    let i = 0;
    i < groups.length && !_unmounted && context === `${chatType.value}:${app.currentBotId}`;
    i++
  ) {
    refreshingGroup.value = groups[i].chat_id
    try {
      await refreshGroup(groups[i])
    } catch {
      failed++
    }
    if (i < groups.length - 1) await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  refreshingGroup.value = ''
  refreshingPage.value = false
  if (failed && context === `${chatType.value}:${app.currentBotId}`)
    groupRefreshError.value = `${failed} 个群更新失败`
}

function isNearBottom() {
  const el = historyRef.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 80
}
function scrollBottom() {
  const el = historyRef.value
  if (el) el.scrollTop = el.scrollHeight
  stickToBottom.value = true
  newMessageCount.value = 0
}
watch(historyRef, (element) => {
  historyResizeObserver?.disconnect()
  if (!element) return
  historyResizeObserver = new ResizeObserver(() => {
    if (stickToBottom.value && !loadingOlder.value) scrollBottom()
  })
  historyResizeObserver.observe(element)
  const inner = element.querySelector('.history-inner')
  if (inner) historyResizeObserver.observe(inner)
})

const mentionNicks = ref({})
const _mentionPending = new Set()
const _nicknameQueue = new Set()
let _nicknameTimer = null
const MENTION_RE = /<@!?([^<>\s]+)>/g
function queueNickname(id) {
  if (!id || nickCache[id] || _mentionPending.has(id)) return
  _mentionPending.add(id)
  _nicknameQueue.add(id)
  if (!_nicknameTimer) _nicknameTimer = setTimeout(loadQueuedNicknames, 100)
}
async function loadQueuedNicknames() {
  _nicknameTimer = null
  const ids = [..._nicknameQueue].slice(0, 500)
  ids.forEach(id => _nicknameQueue.delete(id))
  if (!ids.length || _unmounted) return
  try {
    const res = await axios.post('/api/message/nicknames', { user_ids: ids })
    if (_unmounted) return
    const nicks = res.data?.data?.nicknames || {}
    for (const [id, name] of Object.entries(nicks)) {
      nickCache[id] = name
      mentionNicks.value[id] = name
    }
    for (const message of history.value) {
      if (!message.is_self && nicks[message.user_id]) message.nickname = nicks[message.user_id]
    }
  } catch {
    /* Names can be retried by the next message without delaying its content. */
  } finally {
    ids.forEach(id => _mentionPending.delete(id))
    if (_nicknameQueue.size && !_unmounted) _nicknameTimer = setTimeout(loadQueuedNicknames, 100)
  }
}
function collectMentions(msgs) {
  for (const m of msgs) {
    for (const mt of String(m.content || '').matchAll(MENTION_RE)) {
      const id = mt[1]
      if (nickCache[id]) mentionNicks.value[id] = nickCache[id]
      else if (!(id in mentionNicks.value)) queueNickname(id)
    }
  }
}

function matchesCurrentLog(data) {
  if (!current.value || (currentAppid() && String(data.appid || '') !== currentAppid())) return false
  const gid = data.group_id || '',
    uid = data.user_id || ''
  return apiChatType.value === 'group' ? gid === current.value.chat_id : uid === current.value.chat_id && (!gid || gid === 'c2c')
}

const _liveMessages = []
let _liveFrame = null
let _rolesTimer = null
function flushLiveMessages() {
  _liveFrame = null
  if (_unmounted) return
  const seen = new Set(history.value.map(messageKey))
  const fresh = []
  for (const { selection, item } of _liveMessages.splice(0)) {
    if (selection !== _selectId || seen.has(messageKey(item))) continue
    seen.add(messageKey(item))
    fresh.push(item)
    if (!item.is_self && item.user_id && !nickCache[item.user_id]) queueNickname(item.user_id)
  }
  if (!fresh.length) return
  const stick = stickToBottom.value
  collectMentions(fresh)
  history.value = [...history.value, ...fresh].sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)))
  const latest = [...history.value].reverse().find(message => !message.is_self && !message.event_type && message.message_id && String(message.timestamp).startsWith(localDate()))
  if (latest) lastMsgId.value = latest.message_id
  resolveMessageReferences(history.value)
  if (apiChatType.value === 'group' && !_rolesTimer) {
    _rolesTimer = setTimeout(() => {
      _rolesTimer = null
      if (current.value) fetchGroupRoles(current.value.chat_id, currentAppid())
    }, 250)
  }
  if (stick) {
    nextTick(scrollBottom)
    if (history.value.length > 800) refreshCurrentChat(true)
  } else newMessageCount.value += fresh.length
}
function queueLiveMessage(item) {
  _liveMessages.push({ selection: _selectId, item })
  if (_liveFrame === null) _liveFrame = requestAnimationFrame(flushLiveMessages)
}
function onNewLog(data) {
  if (!data || _unmounted) return
  if (data.log_type === 'audit') {
    onAuditLog(data)
    return
  }
  if (data.log_type === 'lifecycle') {
    onLifecycleLog(data)
    return
  }
  if (data.log_type !== 'message') return
  if (document.visibilityState !== 'visible') return
  if ((!app.currentBotId || String(data.appid || '') === String(app.currentBotId)) && document.visibilityState === 'visible') fetchChatsDebounced()
  if (!matchesCurrentLog(data)) return
  const uid = data.user_id || ''
  const nick = data.is_bot ? data.bot_name || 'Bot' : nickCache[uid] || `用户${uid.slice(-6)}`
  const item = prepareMessage({
    id: `rt_${Date.now()}_${_liveMessages.length}`,
    message_id: data.message_id || '',
    reference_id: data.reference_id || '',
    user_id: uid,
    appid: data.appid || '',
    bot_qq: data.bot_qq || '',
    nickname: nick,
    content: data.content || '',
    timestamp: data.timestamp || '',
    is_self: !!data.is_bot,
    source:
      data.source ||
      (data.plugin_name === 'WebPanel' ? 'web_panel' : data.plugin_name === 'onebot_adapter' ? 'onebot' : ''),
    raw_message: data.raw_message || ''
  })
  queueLiveMessage(item)
}

function onLifecycleLog(data) {
  if (document.visibilityState !== 'visible') return
  if (apiChatType.value !== 'group' || !matchesCurrentLog(data)) return
  if (!['group_member_add', 'group_member_del'].includes(data.type)) return
  const uid = data.user_id || ''
  queueLiveMessage({
    id: `lc_rt_${Date.now()}_${_liveMessages.length}`,
    message_id: '',
    user_id: data.user_id || '',
    appid: data.appid || '',
    nickname: nickCache[uid] || `用户${uid.slice(-6)}`,
    content: '',
    timestamp: data.timestamp || '',
    is_self: false,
    event_type: data.type === 'group_member_add' ? 'member_add' : 'member_remove'
  })
}

function onAuditLog(data) {
  if (data.appid && currentAppid() && String(data.appid) !== currentAppid()) return
  if (!data.audit_id) return
  const m = history.value.find((msg) => msg.message_id === data.audit_id)
  if (m) {
    if (data.passed && data.message_id) {
      m.message_id = data.message_id
    } else if (!data.passed) {
      m._audit_rejected = true
    }
  }
}

function onImgSelect(e) {
  const f = e.target.files?.[0]
  if (f) {
    clearImg()
    imgFile.value = f
    imgPreview.value = URL.createObjectURL(f)
  }
  e.target.value = ''
}
function clearImg() {
  if (imgPreview.value) URL.revokeObjectURL(imgPreview.value)
  imgFile.value = null
  imgPreview.value = ''
}
function onMediaSelect(e) {
  const f = e.target.files?.[0]
  if (f) mediaFile.value = f
  e.target.value = ''
}
function clearMediaFile() {
  mediaFile.value = null
}

function resizeEditor() {
  const editor = editorRef.value
  if (!editor) return
  editor.style.height = 'auto'
  const maximum = Number.parseFloat(getComputedStyle(editor).maxHeight) || 120
  editor.style.height = `${Math.min(editor.scrollHeight, maximum)}px`
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault()
    sendMsg()
  }
}

async function sendMsg() {
  if (sending.value || !current.value) return
  const selection = _selectId
  sending.value = true
  sendErr.value = ''
  try {
    let content = ''
    if (msgType.value === 'ark') {
      const kv = buildArkKv()
      if (!kv.length) {
        sendErr.value = '请至少填写一个字段'
        sending.value = false
        return
      }
      content = JSON.stringify(kv)
    } else if (msgType.value === 'card') {
      const obj = buildCardContent()
      if (!Object.keys(obj).length) {
        sendErr.value = '请至少填写一个字段'
        sending.value = false
        return
      }
      content = JSON.stringify(obj)
    } else {
      content = msgText.value.trim()
      if (!content && !imgFile.value && !mediaFile.value) {
        sending.value = false
        return
      }
    }
    const fd = new FormData()
    fd.append('chat_type', apiChatType.value)
    fd.append('chat_id', current.value.chat_id)
    fd.append('appid', app.currentBotId || current.value.appid || '')
    fd.append('msg_type', msgType.value)
    fd.append('content', content)
    fd.append('msg_id', lastMsgId.value)
    fd.append('send_mode', sendMode.value)
    if (isCustomSendMode.value) {
      const cid = customSendId.value.trim()
      if (!cid) {
        sendErr.value = sendMode.value === 'custom_event_id' ? '请输入事件 ID' : '请输入 msg_id'
        sending.value = false
        return
      }
      fd.append('custom_id', cid)
    }
    if (quotedMsg.value) {
      if (quotedMsg.value.reference_id) fd.append('message_reference_id', quotedMsg.value.reference_id)
      if (quotedMsg.value.message_id) fd.append('quote_message_id', quotedMsg.value.message_id)
    }
    if (imgFile.value && msgType.value === 'text') fd.append('image', imgFile.value)
    if (msgType.value === 'media') {
      fd.append('media_file_type', mediaFileType.value)
      if (mediaFile.value) fd.append('media', mediaFile.value)
    }
    if (msgType.value === 'ark') fd.append('ark_template_id', arkTpl.value)
    if (msgType.value === 'card') fd.append('card_type', 'tuwen')
    const res = await axios.post('/api/message/send', fd)
    if (_unmounted || selection !== _selectId) return
    if (res.data?.success) {
      showPreview.value = false
      msgText.value = ''
      clearImg()
      clearMediaFile()
      clearQuote()
      if (msgType.value === 'ark') {
        arkFields.value = {}
        arkList.value = ''
      }
      if (msgType.value === 'card') cardFields.value = {}
    } else sendErr.value = res.data?.message || '发送失败'
  } catch (e) {
    if (selection === _selectId) sendErr.value = e.response?.data?.message || e.message || '发送失败'
  } finally {
    sending.value = false
  }
}

watch(chatType, () => {
  resetConversation()
  chats.value = []
  total.value = 0
  page.value = 1
  mobileView.value = 'list'
  fetchChats()
})
let _searchTimer = null
watch(chatSearch, () => {
  clearTimeout(_searchTimer)
  _searchTimer = setTimeout(() => {
    _searchTimer = null
    page.value = 1
    fetchChats()
  }, 300)
})
watch(
  () => app.currentBotId,
  () => {
    resetConversation()
    chats.value = []
    total.value = 0
    page.value = 1
    mobileView.value = 'list'
    fetchChats()
  }
)

onMounted(() => {
  fetchChats()
  on('new_log', onNewLog)
  on('open', onWsReconnect)
  on('close', onConnectionClose)
  window.addEventListener('resize', handleResize)
  window.addEventListener('keydown', onEscape)
  document.addEventListener('visibilitychange', onVisibleChange)
})
onUnmounted(() => {
  _unmounted = true
  _selectId++
  _chatRequestId++
  off('new_log', onNewLog)
  off('open', onWsReconnect)
  off('close', onConnectionClose)
  window.removeEventListener('resize', handleResize)
  window.removeEventListener('keydown', onEscape)
  document.removeEventListener('visibilitychange', onVisibleChange)
  clearTimeout(_fetchTimer)
  clearTimeout(_indexPollTimer)
  clearTimeout(_nicknameTimer)
  clearTimeout(_rolesTimer)
  if (_liveFrame !== null) cancelAnimationFrame(_liveFrame)
  _chatController?.abort()
  clearTimeout(_searchTimer)
  clearTimeout(_copyTimer)
  historyResizeObserver?.disconnect()
  clearImg()
})
</script>

<template>
  <section class="msg-page" aria-label="消息记录">
    <div class="msg-layout">
      <aside
        class="chat-list-panel"
        :class="{ 'mobile-hidden': isMobile && mobileView !== 'list' }"
        aria-label="会话列表"
      >
        <div class="list-heading">
          <div>
            <h2>会话</h2>
            <span class="total-badge">{{ total }}</span>
          </div>
          <div>
            <button class="icon-button" title="发起对话" aria-label="发起对话" @click="openStartChat">
              <SvgIcon name="chatbubbles" :size="20" />
            </button>
            <button class="icon-button" title="添加群备注" aria-label="添加群备注" @click="openAddRemark">
              <SvgIcon name="plus" :size="20" />
            </button>
          </div>
        </div>
        <nav class="chat-type-tabs" aria-label="会话类型">
          <button
            v-for="option in chatTypeOptions"
            :key="option.value"
            :class="{ active: chatType === option.value }"
            :aria-pressed="chatType === option.value"
            @click="chatType = option.value"
          >
            {{ option.label }}
          </button>
        </nav>
        <label class="chat-search">
          <SvgIcon name="search" :size="16" />
          <input v-model="chatSearch" type="search" placeholder="搜索会话名称或 ID" aria-label="搜索会话" />
          <button
            v-if="chatSearch"
            class="clear-search"
            type="button"
            aria-label="清除会话搜索"
            @click.prevent="chatSearch = ''"
          >
            <SvgIcon name="x" :size="14" />
          </button>
        </label>
        <div class="list-section-label">
          <span>{{ chatSearch ? '搜索结果' : chatType === 'remark' ? '已备注的会话' : '最近会话' }}</span>
          <button
            v-if="apiChatType === 'group'"
            :disabled="!chats.length || !!refreshingGroup || refreshingPage"
            title="更新本页前 25 个群的信息"
            @click="refreshPageGroups"
          >
            <SvgIcon name="refresh" :size="12" :class="{ spinning: refreshingPage }" />
            {{ refreshingPage ? '更新中' : '更新群信息' }}
          </button>
          <span v-else>今日</span>
        </div>
        <div v-if="chatsError" class="list-error" role="alert">
          <SvgIcon name="alert-circle" :size="15" />
          <span>{{ chatsError }}</span>
          <button @click="fetchChats">重试</button>
        </div>
        <div class="chat-items" :aria-busy="chatsLoading">
          <div v-if="chatsLoading && !chats.length" class="chat-skeletons">
            <div v-for="n in 6" :key="n" class="chat-skeleton">
              <i />
              <div>
                <span />
                <span />
              </div>
            </div>
          </div>
          <template v-else>
            <article
              v-for="chat in chats"
              :key="`${chat.appid}:${chat.chat_id}`"
              class="chat-item"
              :class="{ active: current?.chat_id === chat.chat_id && current?.appid === chat.appid }"
            >
              <button
                class="chat-select"
                :aria-current="
                  current?.chat_id === chat.chat_id && current?.appid === chat.appid ? 'true' : undefined
                "
                @click="selectChat(chat)"
              >
                <span class="chat-avatar-wrap">
                  <MessageAvatar
                    :src="chatAvatar(chat)"
                    :name="chatDisplayName(chat)"
                    :group="chatType !== 'user'"
                    :size="isMobile ? 44 : 36"
                  />
                  <GroupStatus
                    v-if="apiChatType === 'group'"
                    class="chat-status-dots"
                    :full-access="!!chat.is_full_access"
                    :proactive="!!chat.allow_proactive_msg"
                  />
                </span>
                <span class="chat-info">
                  <span class="chat-name-row">
                    <span class="chat-nick" :title="chatDisplayName(chat)">{{ chatDisplayName(chat) }}</span>
                    <span v-if="chat.in_group === false" class="left-tag">已退群</span>
                  </span>
                  <span class="chat-preview">{{ chatPreview(chat) }}</span>
                  <span v-if="app.isAllBots && chat.bot_name" class="chat-bot-name">{{ chat.bot_name }}</span>
                </span>
                <span class="chat-meta">
                  <time>{{ shortTime(chat.last_time) }}</time>
                  <span
                    v-if="chat.msg_count"
                    class="chat-count"
                    :class="{ 'has-remark': chatType !== 'user' }"
                  >
                    {{ chat.msg_count > 999 ? '999+' : chat.msg_count }}
                  </span>
                </span>
              </button>
              <button
                v-if="chatType !== 'user'"
                class="chat-remark"
                title="编辑群备注"
                :aria-label="`编辑 ${chatDisplayName(chat)} 的备注`"
                @click="startRemark(chat)"
              >
                <SvgIcon name="edit" :size="13" />
              </button>
            </article>
            <div v-if="!chats.length && !chatsError" class="list-empty">
              <span><SvgIcon :name="chatSearch ? 'search' : 'chat-group'" :size="26" /></span>
              <strong>{{ chatSearch ? '没有找到相关会话' : '暂无会话' }}</strong>
              <p>{{ chatSearch ? '试试其他名称或会话 ID' : '收到消息后，会话将显示在这里' }}</p>
              <button v-if="chatSearch" class="text-button" @click="chatSearch = ''">清除搜索</button>
            </div>
          </template>
        </div>
        <footer class="chat-list-footer">
          <span>共 {{ total }} 个会话</span>
          <div v-if="total > PAGE" class="chat-pager">
            <button
              class="icon-button"
              :disabled="page <= 1 || chatsLoading"
              aria-label="上一页会话"
              @click="changePage(-1)"
            >
              <SvgIcon name="chevron-back" :size="15" />
            </button>
            <span>{{ page }} / {{ Math.ceil(total / PAGE) }}</span>
            <button
              class="icon-button"
              :disabled="page >= Math.ceil(total / PAGE) || chatsLoading"
              aria-label="下一页会话"
              @click="changePage(1)"
            >
              <SvgIcon name="chevron-forward" :size="15" />
            </button>
          </div>
          <span v-else class="list-footer-note">
            <i />
            {{ chatsIndexing ? '正在整理更早会话…' : wsConnected ? '自动更新' : '等待连接' }}
          </span>
        </footer>
      </aside>

      <section
        class="chat-history-panel"
        :class="{ 'mobile-hidden': isMobile && mobileView !== 'chat' }"
        aria-label="聊天记录"
      >
        <template v-if="current">
          <header class="conversation-header">
            <button
              v-if="isMobile"
              class="icon-button mobile-back"
              aria-label="返回会话列表"
              @click="goBackToList"
            >
              <SvgIcon name="chevron-back" :size="22" />
            </button>
            <MessageAvatar
              :src="chatAvatar(current)"
              :name="chatDisplayName(current)"
              :group="apiChatType === 'group'"
              :size="28"
            />
            <div class="conversation-heading">
              <h2 :title="chatDisplayName(current)">{{ chatDisplayName(current) }}</h2>
              <div class="conversation-summary">
                <span class="conversation-type">{{ apiChatType === 'group' ? '群聊' : '私聊' }}</span>
                <span class="conversation-bot" :title="current.bot_name || app.currentBot?.name || '机器人'">
                  {{ current.bot_name || app.currentBot?.name || '机器人' }}
                </span>
                <span v-if="apiChatType === 'group'" class="conversation-members" title="群成员人数">
                  {{ currentGroupMembers }}
                </span>
                <span class="conversation-today">今日 {{ current.msg_count || 0 }} 条</span>
                <GroupStatus
                  v-if="apiChatType === 'group'"
                  :full-access="!!current.is_full_access"
                  :proactive="!!current.allow_proactive_msg"
                />
                <span v-if="current.in_group === false" class="left-badge">已退群</span>
              </div>
            </div>
            <div class="direction-tabs" aria-label="消息方向筛选">
              <button
                v-for="option in directionOptions"
                :key="option.value"
                :aria-pressed="messageDirection === option.value"
                :class="{ active: messageDirection === option.value }"
                @click="messageDirection = option.value"
              >
                {{ option.label }}
              </button>
            </div>
            <div class="conversation-actions">
              <button
                class="icon-button"
                :class="{ selected: searchVisible }"
                aria-label="搜索已加载消息"
                title="搜索消息"
                :aria-expanded="searchVisible"
                @click="toggleSearch"
              >
                <SvgIcon name="search" :size="19" />
              </button>
              <button
                v-if="apiChatType === 'group'"
                class="icon-button desktop-action"
                :disabled="!!refreshingGroup || refreshingPage"
                title="更新群信息"
                aria-label="更新当前群信息"
                @click="refreshGroupInfo"
              >
                <SvgIcon
                  name="refresh"
                  :size="18"
                  :class="{ spinning: refreshingGroup === current.chat_id }"
                />
              </button>
              <span class="header-divider" />
              <button
                class="icon-button"
                :class="{ selected: detailsVisible }"
                title="会话详情"
                aria-label="会话详情"
                :aria-expanded="detailsVisible"
                @click="detailsVisible = !detailsVisible"
              >
                <SvgIcon name="information-circle" :size="20" />
              </button>
            </div>
          </header>
          <div
            v-if="groupRefreshError || groupActionMessage"
            class="conversation-notice"
            :class="{ error: groupRefreshError }"
            role="status"
          >
            <SvgIcon :name="groupRefreshError ? 'alert-circle' : 'check'" :size="14" />
            {{ groupRefreshError || groupActionMessage }}
            <button class="icon-button" aria-label="关闭提示" @click="dismissConversationNotice">
              <SvgIcon name="x" :size="14" />
            </button>
          </div>
          <div class="chat-workspace">
            <div class="conversation">
              <div v-if="searchVisible" class="message-search-row">
                <SvgIcon name="search" :size="16" />
                <input
                  ref="searchRef"
                  v-model="messageSearch"
                  type="search"
                  aria-label="搜索已加载消息内容"
                  placeholder="搜索已加载消息的内容、昵称或 ID…"
                  @keydown.esc="toggleSearch"
                />
                <button
                  v-if="messageSearch"
                  class="icon-button"
                  aria-label="清除消息搜索"
                  @click="messageSearch = ''"
                >
                  <SvgIcon name="x" :size="15" />
                </button>
                <span class="loaded-count">
                  {{ isFiltering ? `匹配 ${visibleMessages.length} 条` : `已加载 ${history.length} 条消息` }}
                </span>
              </div>
              <div class="timeline-shell">
                <div
                  ref="historyRef"
                  class="history-body"
                  :aria-busy="historyLoading"
                  @scroll="onHistoryScroll"
                >
                  <div class="history-inner">
                    <div v-if="historyLoading" class="history-loading" role="status">
                      <span class="load-spinner" />
                      正在加载消息…
                    </div>
                    <div v-else-if="historyError" class="history-error" role="alert">
                      <SvgIcon name="alert-circle" :size="28" />
                      <strong>{{ historyError }}</strong>
                      <button class="secondary-button" @click="selectChat(current)">重新加载</button>
                    </div>
                    <template v-else>
                      <div v-if="hasMore" class="history-start">
                        <button :disabled="loadingOlder" class="load-older-button" @click="loadOlder">
                          <span v-if="loadingOlder" class="load-spinner" />
                          <SvgIcon v-else name="time" :size="13" />
                          {{ loadingOlder ? '正在加载…' : olderBtnLabel }}
                        </button>
                        <span v-if="olderError" class="older-error" role="alert">
                          {{ olderError }}，点击重试
                        </span>
                      </div>
                      <div v-else-if="history.length" class="history-start">
                        <span class="history-start-label">没有更早的消息了</span>
                      </div>
                      <template v-for="(message, index) in visibleMessages" :key="messageKey(message)">
                        <div
                          v-if="
                            !index ||
                            message.timestamp?.slice(0, 10) !==
                              visibleMessages[index - 1].timestamp?.slice(0, 10)
                          "
                          class="date-divider"
                        >
                          <span>{{ dateLabel(message.timestamp) }}</span>
                        </div>
                        <div v-if="message.event_type" class="event-row">
                          <SvgIcon
                            :name="message.event_type === 'member_add' ? 'person-add' : 'person-remove'"
                            :size="13"
                          />
                          <span>
                            {{ message.nickname || message.user_id }}
                            {{ message.event_type === 'member_add' ? '加入了群聊' : '退出了群聊' }}
                          </span>
                          <time>{{ shortTime(message.timestamp) }}</time>
                        </div>
                        <article
                          v-else
                          class="message-row"
                          :class="{
                            self: message.is_self,
                            'message-recalled': message._recalled,
                            'rich-message': /```|\|.*\|/.test(message.content || '')
                          }"
                        >
                          <button
                            class="icon-button timeline-avatar"
                            :disabled="message.is_self || !message.user_id"
                            title="查看该用户所在群聊"
                            aria-label="查看该用户所在群聊"
                            @click="openUserGroups(message)"
                          >
                            <MessageAvatar
                              :src="messageAvatar(message)"
                              :name="message.nickname"
                              :bot="message.is_self"
                              :size="30"
                            />
                          </button>
                          <div class="message-main">
                            <div class="message-meta">
                              <span class="message-author" :title="message.user_id">
                                {{ message.nickname || '未知用户' }}
                              </span>
                              <span
                                v-if="message.is_self || isBot(message.user_id)"
                                class="message-badge bot-badge"
                              >BOT</span>
                              <span
                                v-if="
                                  apiChatType === 'group' &&
                                  (message.is_self ? botIsGroupAdmin : roleLabel(message.user_id))
                                "
                                class="message-badge"
                                :class="message.is_self ? 'role-admin' : roleClass(message.user_id)"
                              >
                                {{ message.is_self ? '管理员' : roleLabel(message.user_id) }}
                              </span>
                              <span v-if="message.source" class="message-source">
                                {{
                                  message.source === 'web_panel'
                                    ? 'Web 面板'
                                    : message.source === 'onebot'
                                      ? 'OneBot'
                                      : message.source
                                }}
                              </span>
                              <time :title="message.timestamp">{{ shortTime(message.timestamp) }}</time>
                            </div>
                            <div class="message-bubble">
                              <div v-if="message._quote" class="bubble-quote-ref">
                                <span>引用 {{ message._quote.author || '消息' }}</span>
                                <p>{{ quoteTargetText(message._quote) }}</p>
                              </div>
                              <div
                                v-if="
                                  message._recalled ||
                                  message._audit_rejected ||
                                  (message.is_self && isAuditId(message.message_id))
                                "
                                class="message-delivery-state"
                                :class="{ rejected: message._audit_rejected }"
                              >
                                <SvgIcon :name="message._recalled ? 'return' : 'time'" :size="12" />
                                {{
                                  message._recalled
                                    ? '消息已撤回'
                                    : message._audit_rejected
                                      ? '审核未通过'
                                      : '正在审核'
                                }}
                              </div>
                              <template v-if="message._media">
                                <MessageContent
                                  v-if="message._media.text"
                                  :content="message._media.text"
                                  :mentions="mentionNicks"
                                  @preview-image="previewImg"
                                  @media-load="onMediaLoad"
                                />
                                <MessageContent
                                  v-if="message._media.type === '图片'"
                                  :content="`![消息图片](${message._media.src})`"
                                  @preview-image="previewImg"
                                  @media-load="onMediaLoad"
                                />
                                <audio
                                  v-else-if="message._media.type === '语音'"
                                  :src="message._media.src"
                                  controls
                                  preload="none"
                                  class="message-audio"
                                />
                                <video
                                  v-else-if="message._media.type === '视频'"
                                  :src="message._media.src"
                                  controls
                                  preload="none"
                                  class="message-video"
                                  @loadedmetadata="onMediaLoad"
                                />
                                <a
                                  v-else
                                  class="message-file"
                                  :href="message._media.src"
                                  target="_blank"
                                  rel="noopener noreferrer"
                                >
                                  <span><SvgIcon name="file" :size="24" /></span>
                                  <div>
                                    <strong>
                                      {{ message._media.src.split('/').pop()?.split('?')[0] || '附件' }}
                                    </strong>
                                    <small>
                                      点击打开文件
                                      <SvgIcon name="open-outline" :size="11" />
                                    </small>
                                  </div>
                                </a>
                              </template>
                              <MessageContent
                                v-else
                                :content="message.content || ''"
                                :mentions="mentionNicks"
                                @preview-image="previewImg"
                                @media-load="onMediaLoad"
                              />
                            </div>
                            <div class="message-actions">
                              <button
                                v-if="canQuote(message)"
                                title="引用回复"
                                aria-label="引用这条消息"
                                @click="quoteMsg(message)"
                              >
                                <SvgIcon name="return" :size="14" />
                              </button>
                              <button
                                :title="copiedMessage === messageKey(message) ? '已复制' : '复制消息'"
                                aria-label="复制消息"
                                @click="copyMessage(message)"
                              >
                                <SvgIcon
                                  :name="copiedMessage === messageKey(message) ? 'check' : 'copy'"
                                  :size="13"
                                />
                              </button>
                              <button
                                title="原始数据"
                                aria-label="查看原始数据"
                                :class="{ active: rawDataMsg === message }"
                                @click="showRaw(message)"
                              >
                                <SvgIcon name="code" :size="14" />
                              </button>
                              <button
                                v-if="canMuteMember(message)"
                                class="danger-action"
                                title="禁言成员"
                                aria-label="禁言该成员"
                                @click="openMuteModal(message)"
                              >
                                <SvgIcon name="mute" :size="14" />
                              </button>
                              <button
                                v-if="canRecall(message) && !message._recalled"
                                class="danger-action"
                                :disabled="recalling === message.message_id"
                                :aria-busy="recalling === message.message_id"
                                title="撤回消息"
                                aria-label="撤回这条消息"
                                @click="recallMsg(message)"
                              >
                                <SvgIcon name="recall" :size="14" />
                              </button>
                            </div>
                            <div v-if="rawDataMsg === message" class="raw-data-box">
                              <div>
                                <span>原始消息数据</span>
                                <button
                                  class="icon-button"
                                  aria-label="复制原始数据"
                                  @click="copyValue(formatRaw(message))"
                                >
                                  <SvgIcon name="copy" :size="14" />
                                </button>
                                <button
                                  class="icon-button"
                                  aria-label="关闭原始数据"
                                  @click="rawDataMsg = null"
                                >
                                  <SvgIcon name="x" :size="14" />
                                </button>
                              </div>
                              <p v-if="message._rawLoading" role="status">正在加载原始数据…</p>
                              <p v-else-if="message._rawError" role="alert">{{ message._rawError }}</p>
                              <pre v-else>{{ formatRaw(message) }}</pre>
                            </div>
                          </div>
                        </article>
                      </template>
                      <div v-if="!visibleMessages.length" class="timeline-empty">
                        <span><SvgIcon :name="isFiltering ? 'search' : 'chat-group'" :size="28" /></span>
                        <h3>{{ isFiltering ? '没有找到匹配的消息' : '还没有消息记录' }}</h3>
                        <p>
                          {{
                            isFiltering
                              ? '试试其他关键词，或切换消息筛选条件'
                              : '从下方发送第一条消息，开始这段对话'
                          }}
                        </p>
                        <button v-if="isFiltering" class="text-button" @click="resetMessageFilters">
                          重置筛选
                        </button>
                      </div>
                    </template>
                  </div>
                </div>
                <button v-if="!stickToBottom && history.length" class="jump-to-latest" @click="scrollBottom">
                  <SvgIcon name="arrow-down" :size="14" />
                  {{ newMessageCount ? `${newMessageCount} 条新消息` : '回到最新' }}
                </button>
              </div>

              <form class="send-area" @submit.prevent="sendMsg">
                <div v-if="quotedMsg" class="quote-preview">
                  <SvgIcon name="return" :size="16" />
                  <div>
                    <strong>回复 {{ quoteAuthor(quotedMsg) }}</strong>
                    <span>{{ quotedPreview }}</span>
                  </div>
                  <button
                    type="button"
                    class="icon-button"
                    title="取消引用"
                    aria-label="取消引用"
                    @click="clearQuote"
                  >
                    <SvgIcon name="x" :size="16" />
                  </button>
                </div>
                <div class="composer-card" :class="{ 'preview-mode': showPreview }">
                  <div class="composer-header">
                    <div class="msg-type-tabs" aria-label="消息类型">
                      <button
                        v-for="option in msgTypeOptions"
                        :key="option.value"
                        type="button"
                        :disabled="sending"
                        :aria-pressed="msgType === option.value"
                        :class="{ active: msgType === option.value }"
                        @click="selectMsgType(option.value)"
                      >
                        {{ option.label }}
                      </button>
                    </div>
                    <div class="composer-header-actions">
                      <button
                        v-if="msgType === 'markdown'"
                        class="preview-toggle"
                        :class="{ active: showPreview }"
                        type="button"
                        :aria-pressed="showPreview"
                        @click="showPreview = !showPreview"
                      >
                        <SvgIcon :name="showPreview ? 'edit' : 'eye'" :size="14" />
                        <span>{{ showPreview ? '编辑' : '预览' }}</span>
                      </button>
                      <button
                        type="button"
                        class="icon-button composer-settings"
                        :class="{ selected: sendOptionsOpen }"
                        title="发送设置"
                        aria-label="发送设置"
                        :aria-expanded="sendOptionsOpen"
                        @click="sendOptionsOpen = !sendOptionsOpen"
                      >
                        <SvgIcon name="settings" :size="16" />
                      </button>
                    </div>
                  </div>
                  <div v-if="sendOptionsOpen || isCustomSendMode" class="send-options">
                    <label>
                      <span>发送方式</span>
                      <select v-model="sendMode" :disabled="sending" aria-label="发送方式">
                        <option v-for="option in sendModeOptions" :key="option.value" :value="option.value">
                          {{ option.label }}
                        </option>
                      </select>
                    </label>
                    <input
                      v-if="isCustomSendMode"
                      v-model="customSendId"
                      :disabled="sending"
                      :aria-label="sendMode === 'custom_event_id' ? '自定义事件 ID' : '自定义 msg_id'"
                      :placeholder="sendMode === 'custom_event_id' ? '输入事件 ID' : '输入 msg_id'"
                    />
                  </div>
                  <div v-if="msgType === 'ark' || msgType === 'card'" class="structured-editor">
                    <label v-if="msgType === 'ark'" class="template-select">
                      <span>ARK 模板</span>
                      <select v-model="arkTpl" :disabled="sending" aria-label="ARK 模板">
                        <option value="23">23 · 链接卡片</option>
                        <option value="24">24 · 文本卡片</option>
                        <option value="37">37 · 大图卡片</option>
                      </select>
                    </label>
                    <div v-if="msgType === 'card'" class="ark-grid">
                      <label
                        v-for="field in [
                          { key: 'title', label: '标题' },
                          { key: 'description', label: '描述' },
                          { key: 'pic_url', label: '图片 URL' },
                          { key: 'url', label: '跳转 URL' }
                        ]"
                        :key="field.key"
                        class="ark-field"
                      >
                        <span>{{ field.label }}</span>
                        <input
                          v-model="cardFields[field.key]"
                          :disabled="sending"
                          :placeholder="`输入${field.label}`"
                        />
                      </label>
                    </div>
                    <div v-else class="ark-grid">
                      <label v-for="field in arkFieldOptions" :key="field.key" class="ark-field">
                        <span>
                          {{ field.label }}
                          <small>{{ field.key }}</small>
                        </span>
                        <input
                          v-model="arkFields[field.key]"
                          :disabled="sending"
                          :placeholder="`输入${field.label}`"
                        />
                      </label>
                    </div>
                    <label v-if="msgType === 'ark' && arkTpl === '23'" class="ark-list-field">
                      <span>
                        链接列表
                        <small>每行一条：文字 | 链接 URL</small>
                      </span>
                      <textarea
                        v-model="arkList"
                        :disabled="sending"
                        rows="2"
                        placeholder="查看详情 | https://example.com"
                      />
                    </label>
                  </div>
                  <template v-else>
                    <div v-if="showPreview && msgType === 'markdown'" class="composer-preview">
                      <MessageContent
                        v-if="msgText.trim()"
                        :content="msgText"
                        :mentions="mentionNicks"
                        @preview-image="previewImg"
                      />
                      <div v-else class="preview-empty">
                        <SvgIcon name="eye" :size="20" />
                        <span>输入内容后，在这里查看排版效果</span>
                      </div>
                    </div>
                    <textarea
                      v-else
                      ref="editorRef"
                      v-model="msgText"
                      class="message-editor"
                      :disabled="sending"
                      :aria-label="
                        msgType === 'markdown'
                          ? 'Markdown 消息内容'
                          : msgType === 'media'
                            ? '媒体资源 URL'
                            : '文本消息内容'
                      "
                      :placeholder="placeholder"
                      rows="1"
                      @keydown="onKeydown"
                    />
                  </template>
                  <div v-if="imgFile || mediaFile" class="attachment-row">
                    <div v-if="imgFile" class="attachment-chip">
                      <img v-if="imgPreview" :src="imgPreview" alt="待发送的图片" />
                      <span>{{ imgFile.name }}</span>
                      <button type="button" class="icon-button" aria-label="移除待发送图片" @click="clearImg">
                        <SvgIcon name="x" :size="14" />
                      </button>
                    </div>
                    <div v-if="mediaFile" class="attachment-chip">
                      <SvgIcon name="file" :size="17" />
                      <span>{{ mediaFile.name }}</span>
                      <button
                        type="button"
                        class="icon-button"
                        aria-label="移除待发送文件"
                        @click="clearMediaFile"
                      >
                        <SvgIcon name="x" :size="14" />
                      </button>
                    </div>
                  </div>
                  <footer class="composer-footer">
                    <div class="composer-footer-left">
                      <div
                        v-if="msgType === 'markdown' && !showPreview"
                        class="format-toolbar"
                        aria-label="Markdown 格式工具"
                      >
                        <button
                          v-for="tool in markdownTools"
                          :key="tool.label"
                          type="button"
                          :title="tool.label"
                          :aria-label="tool.label"
                          :disabled="sending"
                          :class="tool.class"
                          @mousedown.prevent
                          @click="insertMarkdown(tool)"
                        >
                          <SvgIcon v-if="tool.icon" :name="tool.icon" :size="15" />
                          <span v-else>{{ tool.symbol }}</span>
                        </button>
                      </div>
                      <label
                        v-if="msgType === 'text' || msgType === 'media'"
                        class="upload-button"
                        :title="msgType === 'text' ? '添加图片' : '上传媒体文件'"
                      >
                        <input
                          type="file"
                          :disabled="sending"
                          :accept="msgType === 'text' ? 'image/*' : mediaAccept"
                          :aria-label="msgType === 'text' ? '添加图片' : '上传媒体文件'"
                          @change="msgType === 'text' ? onImgSelect($event) : onMediaSelect($event)"
                        />
                        <SvgIcon :name="msgType === 'text' ? 'image' : 'upload'" :size="17" />
                        <span>{{ msgType === 'text' ? '添加图片' : '上传文件' }}</span>
                      </label>
                      <select
                        v-if="msgType === 'media'"
                        v-model="mediaFileType"
                        :disabled="sending"
                        class="media-type-select"
                        aria-label="媒体类型"
                      >
                        <option v-for="option in mediaTypeOptions" :key="option.value" :value="option.value">
                          {{ option.label }}
                        </option>
                      </select>
                      <span
                        v-if="msgText.length && msgType !== 'ark' && msgType !== 'card'"
                        class="character-count"
                      >
                        {{ msgText.length }} 字
                      </span>
                    </div>
                    <div class="composer-footer-right">
                      <span class="keyboard-hint">
                        <kbd>Enter</kbd>
                        发送
                        <span>·</span>
                        <kbd>Shift + Enter</kbd>
                        换行
                      </span>
                      <button type="submit" class="send-button" :disabled="!canSend">
                        <span v-if="sending" class="load-spinner" />
                        <SvgIcon v-else name="send" :size="15" />
                        <span>{{ sending ? '发送中' : '发送' }}</span>
                      </button>
                    </div>
                  </footer>
                </div>
                <div v-if="sendErr" class="send-error" role="alert">
                  <SvgIcon name="alert-circle" :size="14" />
                  <span>{{ sendErr }}</span>
                  <button
                    type="button"
                    class="icon-button"
                    aria-label="关闭发送错误提示"
                    @click="sendErr = ''"
                  >
                    <SvgIcon name="x" :size="14" />
                  </button>
                </div>
              </form>
            </div>
            <aside v-if="detailsVisible" class="conversation-details" aria-label="会话详情">
              <header>
                <h3>会话详情</h3>
                <button class="icon-button" aria-label="关闭会话详情" @click="detailsVisible = false">
                  <SvgIcon name="x" :size="17" />
                </button>
              </header>
              <div class="details-profile">
                <MessageAvatar
                  :src="chatAvatar(current)"
                  :name="chatDisplayName(current)"
                  :group="apiChatType === 'group'"
                  :size="60"
                />
                <strong>{{ chatDisplayName(current) }}</strong>
                <span>{{ apiChatType === 'group' ? '群聊会话' : '私聊会话' }}</span>
              </div>
              <button v-if="apiChatType === 'group'" class="secondary-button" @click="openGroupMembers()">
                查看该群用户
              </button>
              <button v-if="apiChatType === 'user'" class="secondary-button"
                @click="openUserGroups({ user_id: current.chat_id, nickname: chatDisplayName(current), appid: current.appid })">
                查看该用户所在群聊
              </button>
              <dl>
                <div>
                  <dt>
                    会话 ID
                    <button class="icon-button" aria-label="复制会话 ID" @click="copyValue(current.chat_id)">
                      <SvgIcon name="copy" :size="13" />
                    </button>
                  </dt>
                  <dd>{{ current.chat_id }}</dd>
                </div>
                <div>
                  <dt>所属机器人</dt>
                  <dd>{{ current.bot_name || app.currentBot?.name || current.appid || '全部机器人' }}</dd>
                </div>
                <div v-if="current.appid">
                  <dt>AppID</dt>
                  <dd>{{ current.appid }}</dd>
                </div>
                <div v-if="apiChatType === 'group'">
                  <dt>群成员人数</dt>
                  <dd>{{ currentGroupMembers }}</dd>
                </div>
                <div v-if="apiChatType === 'group' && current.union_group_openid">
                  <dt>
                    跨应用群 OpenID
                    <button class="icon-button" aria-label="复制跨应用群 OpenID" @click="copyValue(current.union_group_openid)">
                      <SvgIcon name="copy" :size="13" />
                    </button>
                  </dt>
                  <dd>{{ current.union_group_openid }}</dd>
                </div>
                <div v-if="apiChatType === 'group' && current.group_finger_memo">
                  <dt>群简介</dt>
                  <dd>{{ current.group_finger_memo }}</dd>
                </div>
                <div v-if="apiChatType === 'group' && current.group_class_text">
                  <dt>群分类</dt>
                  <dd>{{ current.group_class_text }}</dd>
                </div>
                <div v-if="apiChatType === 'group' && current.group_tags?.length">
                  <dt>群标签</dt>
                  <dd>{{ current.group_tags.join('、') }}</dd>
                </div>
                <div v-if="apiChatType === 'group'">
                  <dt>主动消息</dt>
                  <dd>{{ current.allow_proactive_msg ? '已开启' : '未开启' }}</dd>
                </div>
                <div v-if="apiChatType === 'group'">
                  <dt>机器人身份</dt>
                  <dd>{{ botIsGroupAdmin ? '管理员' : '群成员' }}</dd>
                </div>
                <div v-if="apiChatType === 'group'">
                  <dt>接入状态</dt>
                  <dd>
                    <span class="details-status" :class="{ inactive: current.in_group === false }">
                      <i />
                      {{
                        current.in_group === false
                          ? '已退群'
                          : current.is_full_access
                            ? '全量接入'
                            : '标准接入'
                      }}
                    </span>
                  </dd>
                </div>
                <div>
                  <dt>
                    最近消息 ID
                    <button class="icon-button" aria-label="刷新消息 ID" @click="refreshMsgId">
                      <SvgIcon name="refresh" :size="13" />
                    </button>
                  </dt>
                  <dd>{{ lastMsgId || '暂无可用的消息 ID' }}</dd>
                </div>
              </dl>
              <div v-if="apiChatType === 'group'" class="details-actions">
                <button class="secondary-button" @click="startRemark(current)">
                  <SvgIcon name="edit" :size="15" />
                  编辑群备注
                </button>
                <button
                  class="secondary-button"
                  :disabled="!!refreshingGroup || refreshingPage"
                  @click="refreshGroupInfo"
                >
                  <SvgIcon
                    name="refresh"
                    :size="15"
                    :class="{ spinning: refreshingGroup === current.chat_id }"
                  />
                  更新群信息
                </button>
              </div>
              <p class="details-note">会话中的新消息会自动同步。更早的记录可从时间线顶部加载。</p>
            </aside>
          </div>
        </template>
        <div v-else class="no-chat">
          <div class="empty-illustration" aria-hidden="true">
            <span class="illustration-dot dot-one" />
            <span class="illustration-dot dot-two" />
            <div class="illustration-card back">
              <i />
              <i />
            </div>
            <div class="illustration-card front">
              <span class="illustration-avatar"><SvgIcon name="chat-group" :size="26" /></span>
              <div>
                <i />
                <i />
              </div>
              <span class="illustration-check"><SvgIcon name="check" :size="13" /></span>
            </div>
          </div>
          <span class="empty-eyebrow">每一条消息，都清晰可见</span>
          <h2>选择一个会话，开始查看</h2>
          <p>
            在左侧查找群聊或私聊
            <br />
            消息记录、实时回复与 Markdown 预览，尽在这里
          </p>
          <div class="empty-features">
            <span>
              <SvgIcon name="time" :size="14" />
              实时消息
            </span>
            <span>
              <SvgIcon name="code" :size="14" />
              Markdown
            </span>
            <span>
              <SvgIcon name="image" :size="14" />
              多媒体
            </span>
          </div>
        </div>
      </section>
    </div>

    <Teleport to="body">
      <div
        v-if="lightboxSrc"
        class="message-lightbox"
        role="dialog"
        aria-modal="true"
        aria-label="图片预览"
        @click="closeLightbox"
        @keydown.tab.prevent="lightboxCloseRef?.focus()"
      >
        <button
          ref="lightboxCloseRef"
          class="lightbox-close"
          aria-label="关闭图片预览"
          @click.stop="closeLightbox"
        >
          <SvgIcon name="x" :size="24" />
        </button>
        <img :src="lightboxSrc" alt="消息图片预览" referrerpolicy="no-referrer" @click.stop />
        <span>点击空白处或按 Esc 关闭</span>
      </div>
    </Teleport>

    <n-modal
      v-model:show="remarkModalVisible"
      preset="dialog"
      title="编辑群备注"
      positive-text="保存备注"
      negative-text="取消"
      :positive-button-props="{ loading: remarkSaving }"
      @positive-click="setRemark"
      @negative-click="cancelRemark"
      style="width: min(400px, calc(100vw - 32px))"
    >
      <div class="remark-form">
        <p v-if="remarkError" class="remark-error" role="alert">{{ remarkError }}</p>
        <p>群 OpenID：{{ remarkEditing }}</p>
        <label>
          备注名称
          <n-input v-model:value="remarkInput" placeholder="输入备注，留空可清除" />
        </label>
        <label>
          群号
          <n-input
            v-model:value="remarkQqInput"
            placeholder="选填，用于显示群头像"
            @keydown.enter="setRemark"
          />
        </label>
      </div>
    </n-modal>
    <n-modal v-model:show="groupMembersVisible" preset="card" title="该群用户"
      style="width: min(640px, calc(100vw - 32px))">
      <div class="remark-form">
        <strong>{{ groupMembersTarget?.group_name || groupMembersTarget?.chat_id }}</strong>
        <p>显示全部已记录成员，点击头像进入私聊。</p>
        <p v-if="groupMembersLoading" role="status">正在加载群用户…</p>
        <div v-else-if="groupMembersError" role="alert">
          <p class="remark-error">{{ groupMembersError }}</p>
          <button class="secondary-button" @click="openGroupMembers(groupMembersTarget)">重试</button>
        </div>
        <template v-else>
          <p v-if="!groupMembers.length">暂无该群的用户记录</p>
          <span v-else>共 {{ groupMembers.length }} 位用户</span>
          <div class="group-members-grid">
            <button v-for="member in groupMembers" :key="`${member.appid}:${member.user_id}`"
              class="group-member-card" :aria-label="`与 ${maskedOpenid(member.user_id)} 私聊`"
              @click="openMemberChat(member)">
              <MessageAvatar :src="messageAvatar(member)" :name="member.user_id" :size="48" />
              <span>{{ maskedOpenid(member.user_id) }}</span>
            </button>
          </div>
        </template>
      </div>
    </n-modal>
    <n-modal v-model:show="userGroupsVisible" preset="card" title="用户所在群聊"
      style="width: min(520px, calc(100vw - 32px))">
      <div class="remark-form">
        <strong>{{ userGroupsTarget?.nickname || userGroupsTarget?.user_id }}</strong>
        <span>{{ userGroupsTarget?.user_id }}</span>
        <p>仅显示当前机器人已记录的群成员关系，可能不完整或存在更新延迟。</p>
        <p v-if="userGroupsLoading" role="status">正在加载群聊…</p>
        <div v-else-if="userGroupsError" role="alert">
          <p class="remark-error">{{ userGroupsError }}</p>
          <button class="secondary-button" @click="openUserGroups(userGroupsTarget)">重试</button>
        </div>
        <template v-else>
          <p v-if="!userGroups.length">暂无该用户的群成员记录</p>
          <span v-else>已记录 {{ userGroups.length }} 个群聊</span>
          <div style="max-height: 50vh; overflow-y: auto; display: grid; gap: 8px">
            <button v-for="group in userGroups" :key="`${group.appid}:${group.chat_id}`"
              class="secondary-button" style="height: auto; white-space: normal; overflow-wrap: anywhere"
              @click="openUserGroup(group)">
              {{ group.group_name || '未命名群聊' }} · {{ group.chat_id }}
              {{ group.in_group === 0 ? '（机器人已退群）' : '' }}
            </button>
          </div>
        </template>
      </div>
    </n-modal>
    <n-modal
      v-model:show="startChatModalVisible"
      preset="dialog"
      title="发起对话"
      positive-text="发起对话"
      negative-text="取消"
      :positive-button-props="{ loading: startingChat, disabled: startingChat }"
      @positive-click="submitStartChat"
      style="width: min(400px, calc(100vw - 32px))"
    >
      <form class="remark-form" @submit.prevent="submitStartChat">
        <p v-if="startChatError" class="remark-error" role="alert">{{ startChatError }}</p>
        <label>
          对话类型
          <n-select v-model:value="startChatType" :options="[{ label: '群', value: 'group' }, { label: '好友', value: 'user' }]" />
        </label>
        <label>
          {{ startChatType === 'group' ? '群 OpenID' : '好友 OpenID' }}
          <n-input v-model:value="startChatOpenid" placeholder="输入 OpenID，无需已有消息记录" autofocus @keydown.enter.prevent="submitStartChat" />
        </label>
      </form>
    </n-modal>
    <n-modal
      v-model:show="addRemarkModalVisible"
      preset="dialog"
      title="添加群备注"
      positive-text="添加备注"
      negative-text="取消"
      :positive-button-props="{ loading: remarkSaving }"
      @positive-click="submitAddRemark"
      @negative-click="cancelAddRemark"
      style="width: min(400px, calc(100vw - 32px))"
    >
      <div class="remark-form">
        <p v-if="remarkError" class="remark-error" role="alert">{{ remarkError }}</p>
        <label>
          群 OpenID
          <n-input v-model:value="addRemarkOpenid" placeholder="输入群 OpenID" autofocus />
        </label>
        <label>
          备注名称
          <n-input v-model:value="addRemarkName" placeholder="为群聊取一个容易识别的名称" />
        </label>
        <label>
          群号
          <n-input
            v-model:value="addRemarkQq"
            placeholder="选填，用于显示群头像"
            @keydown.enter="submitAddRemark"
          />
        </label>
      </div>
    </n-modal>
    <n-modal
      v-model:show="muteModalVisible"
      preset="card"
      title="群成员禁言"
      :mask-closable="!muting"
      style="width: min(420px, calc(100vw - 32px))"
      @close="closeMuteModal"
    >
      <div v-if="muteTarget" class="mute-dialog">
        <div class="mute-member">
          <MessageAvatar :src="messageAvatar(muteTarget)" :name="muteTarget.nickname" :size="44" />
          <div>
            <strong>{{ muteTarget.nickname || '未知用户' }}</strong>
            <span>{{ muteTarget.user_id }}</span>
          </div>
        </div>
        <label class="mute-label">禁言时长</label>
        <div class="mute-presets">
          <button
            v-for="item in [
              { label: '10 分钟', value: 10 },
              { label: '30 分钟', value: 30 },
              { label: '1 小时', value: 60 },
              { label: '1 天', value: 1440 }
            ]"
            :key="item.value"
            :class="{ active: muteMinutes === item.value }"
            @click="muteMinutes = item.value"
          >
            {{ item.label }}
          </button>
        </div>
        <label class="mute-custom">
          <span>自定义分钟</span>
          <input v-model.number="muteMinutes" type="number" min="1" max="43200" />
        </label>
        <div v-if="muteError" class="send-error" role="alert">{{ muteError }}</div>
        <div class="mute-actions">
          <button class="secondary-button" :disabled="muting" @click="closeMuteModal">取消</button>
          <button
            class="send-button"
            :disabled="muting || !Number.isFinite(muteMinutes) || muteMinutes < 1 || muteMinutes > 43200"
            @click="submitMute"
          >
            {{ muting ? '处理中…' : '确认禁言' }}
          </button>
        </div>
      </div>
    </n-modal>
  </section>
</template>

<style scoped src="./messages.css"></style>
