<script setup>
import { ref, reactive, computed, nextTick, onMounted, onUnmounted } from 'vue'
import axios from '../../utils/axios'
import { copyText as copyToClipboard } from '../../utils/clipboard'
import { useThemeStore } from '../../stores/theme'
import AppIcon from './AppIcon.vue'
import OpenAPILoginDialog from './OpenAPILoginDialog.vue'
import OperationsDashboard from './OperationsDashboard.vue'
import FeedbackPanel from './FeedbackPanel.vue'
import QrCodeImage from './QrCodeImage.vue'
import ReportLineChart from './ReportLineChart.vue'
import './qqdash.css'
import './official-dashboard.css'
import './dashboard-overrides.css'
import './dark-theme.css'
import MenuCommandSettings from './MenuCommandSettings.vue'
import ServiceGuide from './ServiceGuide.vue'
import FeatureImages from './FeatureImages.vue'
import GroupAdminSettings from './GroupAdminSettings.vue'
import NotifyCenter from './NotifyCenter.vue'

const themeStore = useThemeStore()
const UID = 'web_user'
const HELPER_QR_URL = 'https://qq-ai.cdn-go.cn/web/neon/-/21fea1a6/q.qq.com_qqbot_dashboard/assets/qq-platform-assistant-qr.png'
const BOT_LIST_ART = 'https://qq-ai.cdn-go.cn/web/neon/-/aacd6e8b/q.qq.com_qqbot_dashboard/assets/'
const showHelperQR = ref(false)

/* ── 鉴权状态 ── */
const status = reactive({
  loggedIn: false,
  ready: false,
  developerId: '',
  uin: '',
  subjectName: '',
  subjectEmail: '',
  subjectType: 0,
  userType: 0,
  userName: '',
  userAvatar: '',
  maskedUin: '',
})
const statusLoaded = ref(false)
const toast = ref('')
let toastTimer = null
function pushToast(msg) {
  toast.value = msg
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = ''), 2600)
}

async function loadStatus() {
  try {
    const { data } = await axios.post('/api/openapi/v2/status', { user_id: UID })
    if (data.success) {
      status.loggedIn = !!data.logged_in
      status.ready = !!data.ready
      status.developerId = data.developer_id || ''
      status.uin = data.uin || ''
      if (status.ready) {
        await loadCurrentDeveloper()
      }
    }
  } catch (e) { /* ignore */ }
  statusLoaded.value = true
}

/* ── 开放平台登录 ── */
const showLogin = ref(false)
const loginMethod = ref('qr')
const mobileLoginUrl = ref('')
const mobileLogin = computed(() => loginMethod.value === 'mobile')
const loginId = ref('')
const qrImg = ref('')
const scanStatus = ref('preparing')
const scanMessage = ref('')
const scanDevelopers = ref([])
const selectedDeveloperId = ref('')
const scanLoading = ref(false)
let scanTimer = null
let scanRequestId = 0
const SCAN_TEXT = {
  preparing: '正在创建安全登录会话...',
  waiting: '等待扫码...',
  scanned: '已扫码，请在手机上确认',
  confirming: '确认中...',
  selecting: '请选择要登录的开发者主体',
  expired: '二维码已过期，请刷新',
  rejected: '已取消登录，请刷新重试',
  failed: '登录失败，请刷新重试',
  not_started: '登录任务已失效，请刷新重试',
  logged_in: '登录成功！',
}
const MOBILE_LOGIN_TEXT = {
  waiting: '等待在手机 QQ 中确认',
  scanned: '请在手机 QQ 中选择主体并确认',
  expired: '授权链接已过期，请重新获取',
}
const scanText = computed(() => scanMessage.value || (mobileLogin.value && MOBILE_LOGIN_TEXT[scanStatus.value]) || SCAN_TEXT[scanStatus.value] || '获取中...')
const scanEnded = computed(() => ['expired', 'rejected', 'failed', 'not_started'].includes(scanStatus.value))
const activeMobileUrl = computed(() => ['waiting', 'scanned'].includes(scanStatus.value) ? mobileLoginUrl.value : '')
const scanSteps = computed(() => [
  { label: '准备会话', active: scanStatus.value === 'preparing', done: scanStatus.value !== 'preparing' },
  { label: mobileLogin.value ? 'QQ 授权' : '扫码确认', active: ['waiting', 'scanned', 'confirming'].includes(scanStatus.value), done: ['selecting', 'logged_in'].includes(scanStatus.value) },
  ...(mobileLogin.value ? [] : [{ label: '选择主体', active: scanStatus.value === 'selecting', done: scanStatus.value === 'logged_in' }]),
  { label: '完成登录', active: scanStatus.value === 'logged_in', done: false },
])
const scanActions = computed(() => [
  ...(scanStatus.value === 'selecting' ? [{ key: 'select', label: scanLoading.value ? '登录中...' : '登录所选主体', primary: true, disabled: !selectedDeveloperId.value || scanLoading.value }] : []),
  ...(activeMobileUrl.value ? [{ key: 'copy', label: '复制授权链接' }] : []),
  ...(scanEnded.value ? [{ key: 'refresh', label: mobileLogin.value ? '重新获取授权链接' : '刷新二维码', primary: true }] : []),
  { key: 'close', label: '关闭' },
])

function stopScanPoll() { if (scanTimer) { clearInterval(scanTimer); scanTimer = null } }

async function startScan(method = 'qr') {
  stopScanPoll()
  const requestId = ++scanRequestId
  showLogin.value = true
  loginMethod.value = method
  loginId.value = ''
  mobileLoginUrl.value = ''
  qrImg.value = ''
  scanStatus.value = 'preparing'
  scanMessage.value = ''
  scanDevelopers.value = []
  selectedDeveloperId.value = ''
  scanLoading.value = true
  try {
    const { data } = await axios.post('/api/openapi/v2/start-login', { user_id: UID, method })
    if (requestId !== scanRequestId) return
    const mobile = method === 'mobile'
    if (data.success && (mobile ? data.login_url && data.login_id : data.qr_image)) {
      loginId.value = data.login_id || ''
      mobileLoginUrl.value = mobile ? data.login_url : ''
      qrImg.value = mobile ? '' : data.qr_image
      scanStatus.value = 'waiting'
      scanMessage.value = ''
      pollScan(requestId)
    } else {
      scanStatus.value = 'failed'
      scanMessage.value = data.message || (mobile ? '获取手机 QQ 授权链接失败' : '获取二维码失败')
    }
  } catch (e) {
    if (requestId !== scanRequestId) return
    scanStatus.value = 'failed'
    scanMessage.value = '登录服务请求失败，请稍后重试'
  }
  if (requestId === scanRequestId) scanLoading.value = false
}

let scanBusy = false
async function applyScanResponse(data, requestId) {
  if (requestId !== scanRequestId) return
  const st = data.status || (data.success === false ? 'failed' : 'waiting')
  scanStatus.value = st
  scanMessage.value = ['expired', 'rejected', 'failed', 'not_started'].includes(st) ? (data.message || '') : ''
  if (st === 'logged_in') {
    stopScanPoll()
    setTimeout(() => { if (requestId === scanRequestId) closeScan() }, 800)
    await loadStatus()
    if (status.ready) { pushToast('开放平台已授权'); loadBots() }
  } else if (st === 'selecting') {
    stopScanPoll()
    scanDevelopers.value = data.developers || []
    selectedDeveloperId.value = scanDevelopers.value.length === 1 ? scanDevelopers.value[0].id : ''
  } else if (st === 'waiting' && data.qr_image) {
    qrImg.value = data.qr_image
  } else if (['expired', 'rejected', 'failed', 'not_started'].includes(st)) {
    stopScanPoll()
  }
}

function pollScan(requestId) {
  scanTimer = setInterval(async () => {
    if (scanBusy) return
    scanBusy = true
    try {
      const { data } = await axios.post('/api/openapi/v2/check-login', {
        user_id: UID,
        login_id: loginId.value || undefined,
      })
      await applyScanResponse(data, requestId)
    } catch (e) { /* ignore */ } finally { scanBusy = false }
  }, 2000)
}

async function selectDeveloper() {
  if (!selectedDeveloperId.value || scanLoading.value) return
  const requestId = scanRequestId
  scanLoading.value = true
  scanStatus.value = 'confirming'
  try {
    const { data } = await axios.post('/api/openapi/v2/check-login', {
      user_id: UID,
      login_id: loginId.value || undefined,
      developer_id: selectedDeveloperId.value,
    })
    await applyScanResponse(data, requestId)
  } catch (e) {
    if (requestId !== scanRequestId) return
    scanStatus.value = 'failed'
    scanMessage.value = '选择开发者主体失败，请刷新重试'
  }
  if (requestId === scanRequestId) scanLoading.value = false
}

function subjectTypeText(type) {
  return ({ 0: '未认证', 1: '个人', 2: '企业', 3: '企业', 4: '未认证' })[type] || '开发者'
}

function firstCharacter(name) {
  const value = String(name || '').trim()
  if (!value) return ''
  if (typeof Intl !== 'undefined' && Intl.Segmenter) {
    return new Intl.Segmenter('zh-CN', { granularity: 'grapheme' }).segment(value)[Symbol.iterator]().next().value?.segment || ''
  }
  return Array.from(value)[0] || ''
}

function displaySubjectName(name) {
  const first = firstCharacter(name)
  return first ? `${first}*` : ''
}

function closeScan() {
  scanRequestId += 1
  stopScanPoll()
  scanLoading.value = false
  showLogin.value = false
}
async function handleScanAction(action) {
  if (action === 'select') selectDeveloper()
  else if (action === 'refresh') startScan(loginMethod.value)
  else if (action === 'copy') {
    const copied = await copyToClipboard(activeMobileUrl.value)
    pushToast(copied ? '授权链接已复制' : '复制失败，请长按「打开手机 QQ」复制链接')
  }
  else closeScan()
}

/* ── 通用 v2 代理 ── */
async function v2(path, payload, method = 'POST', params) {
  const { data } = await axios.post('/api/openapi/v2/proxy', { user_id: UID, path, payload, method, params })
  if (data.relogin === true) {
    closeScan()
    status.loggedIn = false
    status.ready = false
    await loadStatus()
  }
  const r = data || {}
  const code = [r.code, r.ret, r.retcode, r.common?.code, r.common?.retcode, r.res?.ret]
    .find(v => typeof v === 'number')
  if (r.success === false || code) {
    const error = new Error(r.message || r.msg || r.common?.msg || r.res?.msg || (code ? `code=${code}` : '请求失败'))
    if (code) error.platformCode = code
    throw error
  }
  return r.data && typeof r.data === 'object' ? { ...r, ...r.data } : r
}

async function loadCurrentDeveloper() {
  try {
    const r = await v2('/bopen/v2/get_audit_developer_info', {})
    const info = r.audit_developer_info || r.data?.audit_developer_info || {}
    const developer = info.developer_info || {}
    const user = info.user_profile || {}
    status.subjectName = developer.subject_name || ''
    status.subjectEmail = developer.subject_email || ''
    status.subjectType = developer.subject_type ?? 0
    status.userType = info.user_type ?? 0
    status.userName = user.nick_name || ''
    status.userAvatar = String(user.avatar_url || '').replace(/^http:\/\//i, 'https://')
    status.maskedUin = user.mask_uin || ''
  } catch (e) {
    status.subjectName = ''
    status.subjectEmail = ''
    status.subjectType = 0
    status.userType = 0
    status.userName = ''
    status.userAvatar = ''
    status.maskedUin = ''
  }
}

const developers = ref([])
const showDeveloperPicker = ref(false)
const developersLoading = ref(false)
const developerSwitching = ref(false)
const selectedSwitchDeveloperId = ref('')
const accountMenu = ref(null)

function developerMark(developer) {
  return firstCharacter(developer.name || developer.email || '主体')
}

async function loadDevelopers() {
  developersLoading.value = true
  try {
    const { data } = await axios.post('/api/openapi/v2/developers', { user_id: UID })
    if (data.success === false) throw new Error(data.message || '获取主体列表失败')
    developers.value = (data.developers || []).map(item => ({
      id: item.id || '',
      name: item.name === '快速注册开发者' ? '未认证主体' : (item.name || '开发者主体'),
      email: item.email || '',
      subjectType: item.subject_type ?? 0,
    })).filter(item => item.id)
    selectedSwitchDeveloperId.value = data.current_developer_id || status.developerId
  } catch (e) { pushToast(e.message) }
  developersLoading.value = false
}

async function openDeveloperPicker() {
  if (accountMenu.value) accountMenu.value.open = false
  showDeveloperPicker.value = true
  await loadDevelopers()
}

async function logoutPlatform() {
  if (accountMenu.value) accountMenu.value.open = false
  try {
    const { data } = await axios.post('/api/openapi/logout', { user_id: UID })
    if (!data.success) throw new Error(data.message || '退出登录失败')
    closeScan()
    closeCreate()
    bots.value = []
    developers.value = []
    await loadStatus()
  } catch (error) {
    pushToast(error.message || '退出登录失败')
  }
}

async function switchDeveloper() {
  if (!selectedSwitchDeveloperId.value || selectedSwitchDeveloperId.value === status.developerId || developerSwitching.value) return
  developerSwitching.value = true
  try {
    const { data } = await axios.post('/api/openapi/v2/switch-developer', {
      user_id: UID,
      developer_id: selectedSwitchDeveloperId.value,
    })
    if (data.success === false) throw new Error(data.message || '切换主体失败')
    status.developerId = data.developer_id || selectedSwitchDeveloperId.value
    await loadCurrentDeveloper()
    closeCreate()
    activeSec.value = 'account-info'
    officialAvatars.value = []
    createDraft.avatarId = ''
    await loadBots()
    showDeveloperPicker.value = false
    pushToast(`已切换到「${displaySubjectName(status.subjectName) || '当前主体'}」`)
  } catch (e) { pushToast(e.message) }
  developerSwitching.value = false
}

/* ── 视图路由 ── */
const view = ref('list')
const activeSec = ref('account-info')
const activeSub = ref('')
const showNotifications = ref(false)
const SECS = [
  { key: 'account-info', label: '账号信息' },
  { key: 'usage-scope', label: '服务范围' },
  { key: 'data', label: '运营数据' },
  { key: 'feedback', label: '用户反馈' },
  { key: 'advanced', label: '高级设置' },
  { key: 'dev-settings', label: '开发设置' },
]

/* ── 机器人列表 ── */
const bots = ref([])
const createRemain = ref(0)
const botsLoading = ref(false)
const PALETTE = ['#5b8def', '#7c6cf0', '#f0794b', '#25b47e', '#e05a8c', '#3aa1d8']
const botCountFormat = new Intl.NumberFormat('zh-CN')
const isMember = computed(() => status.userType === 2)

function botStatusText(bot) {
  if (bot.revoking) return '注销中'
  if (bot.status === 3) return '已删除'
  if (bot.status === 4) return '已封禁'
  if (bot.status !== 1) return '未知'
  return bot.online ? '在线' : '离线（服务不可用）'
}

function openBotCard(bot) {
  if (bot.revoking) {
    pushToast('该机器人正在注销中，撤回后可恢复正常')
    return
  }
  if (bot.status === 4) {
    pushToast('该机器人已被封禁')
    return
  }
  openBot(bot)
}

async function loadBots() {
  if (!status.ready) return
  botsLoading.value = true
  try {
    const r = await v2('/cgi-bin/v2/info/list_bots', {
      developer_id: status.developerId,
      filter: { create_remain: 1, online_state: 1, page_limit: 100 },
    })
    const list = r.bots || (r.data && r.data.bots) || []
    createRemain.value = r.create_remain ?? 0
    bots.value = list.map((b, i) => ({
      appid: b.bot_appid === undefined ? '' : String(b.bot_appid),
      name: b.bot_name || '',
      avatar: b.bot_avatar || '',
      desc: b.bot_desc || '',
      uin: b.bot_uin === undefined ? '' : String(b.bot_uin),
      online: ((b.online_state && b.online_state.online_state) || 0) === 1,
      status: b.bot_status ?? 0,
      revoking: (b.bot_status ?? 0) === 2,
      finalDeleteTime: Number(b.final_delete_time || 0),
      color: PALETTE[i % PALETTE.length],
      role: Number(b.user_type ?? status.userType),
      serviceNames: (b.online_state?.links || []).map(link => link.service_name).filter(Boolean),
      serviceConnected: Boolean(b.online_state?.links?.length),
      existingGroups: b.statistics_data?.existing_groups ?? 0,
      stockAddedFriends: b.statistics_data?.stock_added_friends ?? 0,
    }))
  } catch (e) { pushToast(e.message) }
  botsLoading.value = false
}

/* ── 机器人详情 ── */
const cur = reactive({
  appid: '',
  name: '',
  avatar: '',
  desc: '',
  uin: '',
  online: false,
  status: 0,
  revoking: false,
  serviceConnected: false,
  finalDeleteTime: 0,
  color: '#5b8def',
})
const detailLoading = ref(false)
const baseInfo = reactive({ name: '', desc: '', avatar: '', uin: '' })
const developerInfo = reactive({ developerId: '', subjectName: '', subjectType: 0, adminUin: '', adminNickName: '' })
const privateProto = reactive({ status: 0, url: '', effectiveDate: '', email: '' })
const isCertified = computed(() => [1, 2].includes(developerInfo.subjectType))
const certificationDescription = computed(() => {
  if (developerInfo.subjectType === 2) return '当前主体为企业认证，可开放好友（私聊）、群聊、频道全部场景。'
  if (developerInfo.subjectType === 1) return ''
  return '未认证账号状态下的机器人，仅限账号管理员使用，无法提供公开服务。'
})
const editor = reactive({
  visible: false,
  field: 'name',
  value: '',
  loading: false,
})
const showAvatarPicker = ref(false)
const selectedAvatarId = ref('')
const avatarSaving = ref(false)
const avatarFileInput = ref(null)
const customAvatarFile = ref(null)
const customAvatarUrl = ref('')
const cropper = reactive({
  visible: false,
  source: '',
  fileName: 'avatar.png',
  fileType: 'image/png',
  naturalWidth: 0,
  naturalHeight: 0,
  baseScale: 1,
  zoom: 1,
  offsetX: 0,
  offsetY: 0,
})
const CROP_VIEW_SIZE = 320
let cropImage = null
const cropImageStyle = computed(() => {
  const scale = cropper.baseScale * cropper.zoom
  const width = cropper.naturalWidth * scale
  const height = cropper.naturalHeight * scale
  return {
    width: `${width}px`,
    height: `${height}px`,
    left: `${(CROP_VIEW_SIZE - width) / 2 + cropper.offsetX}px`,
    top: `${(CROP_VIEW_SIZE - height) / 2 + cropper.offsetY}px`,
  }
})
const cropPreviewStyle = computed(() => {
  const previewScale = 96 / CROP_VIEW_SIZE
  const style = cropImageStyle.value
  return {
    width: `${parseFloat(style.width) * previewScale}px`,
    height: `${parseFloat(style.height) * previewScale}px`,
    left: `${parseFloat(style.left) * previewScale}px`,
    top: `${parseFloat(style.top) * previewScale}px`,
  }
})
const privacyDialog = reactive({
  visible: false,
  editing: false,
  email: '',
  loading: false,
})

const botShareTarget = computed(() => {
  const params = new URLSearchParams({
    robot_appid: cur.appid,
    robot_uin: cur.uin,
    biz_type: '0',
    jumpsource: 'shorturl',
  })
  return `https://qun.qq.com/qunpro/robot/qunshare?${params.toString()}`
})
const friendShareTarget = computed(() => {
  const params = new URLSearchParams({ robot_appid: cur.appid })
  return `https://qun.qq.com/qunpro/robot/share?${params.toString()}`
})
const groupShareTarget = computed(() => {
  const params = new URLSearchParams({ robot_appid: cur.appid, robot_uin: cur.uin })
  return `https://qun.qq.com/qunpro/robot/qunshare?${params.toString()}`
})
const channelShareTarget = computed(() => {
  const params = new URLSearchParams({ robot_appid: cur.appid })
  return `https://qun.qq.com/qunpro/robot/share?${params.toString()}`
})

function openBot(b) {
  if (b.appid !== cur.appid) {
    dev.secret = '****************'
    dev.secretShown = false
    secretResetDialog.value = false
  }
  Object.assign(cur, b)
  createdSecret.value = ''
  showNotifications.value = false
  view.value = 'manage'
  activeSec.value = 'account-info'
  activeSub.value = ''
  loadSection()
}
function backToList() {
  createdSecret.value = ''
  showNotifications.value = false
  view.value = 'list'
}

const showBotSwitcher = ref(false)
function switchBot(b) {
  showBotSwitcher.value = false
  if (b.appid === cur.appid) return
  openBotCard(b)
}

function selectSec(k) {
  activeSec.value = k
  activeSub.value = ''
  loadSection()
}

async function loadSection() {
  const k = activeSec.value
  if (k === 'account-info') await loadAccount()
  else if (k === 'usage-scope') await loadScope()
  else if (k === 'data-details') await loadAllReports()
  else if (k === 'dev-settings') await loadDevSettings()
  else if (k === 'advanced') { await loadDevSettings(); await loadFeaturePreview() }
}

async function loadAccount() {
  detailLoading.value = true
  try {
    const r = await v2('/cgi-bin/v2/info/query', {
      bot_appid: Number(cur.appid),
      filter: { base_info: 1, private_proto: 1, developer_info: 1, online_state: 1 },
    })
    const bi = r.base_info || {}
    baseInfo.name = bi.bot_name || cur.name
    baseInfo.desc = bi.bot_desc || ''
    baseInfo.avatar = bi.bot_avatar || ''
    baseInfo.uin = bi.bot_uin ? String(bi.bot_uin) : ''
    cur.name = baseInfo.name
    cur.avatar = baseInfo.avatar
    cur.uin = baseInfo.uin
    cur.online = bi.online_state?.online_state === 1
    cur.serviceConnected = Boolean(bi.online_state?.links?.length)
    cur.serviceNames = (bi.online_state?.links || []).map(link => link.service_name).filter(Boolean)
    cur.status = bi.bot_status ?? cur.status
    cur.revoking = cur.status === 2
    cur.desc = baseInfo.desc
    const di = r.developer_info || {}
    developerInfo.developerId = di.developer_id || status.developerId
    developerInfo.subjectName = status.subjectName
    developerInfo.subjectType = di.subject_type ?? status.subjectType
    developerInfo.adminUin = di.admin_uin === undefined ? '' : String(di.admin_uin)
    developerInfo.adminNickName = di.admin_nick_name || ''
    const pp = r.private_proto || {}
    privateProto.status = pp.status ?? 0
    privateProto.url = pp.private_proto_url || ''
    privateProto.effectiveDate = pp.effective_date || ''
    privateProto.email = pp.private_proto_template?.email || ''
    await loadAssistantMembers()
  } catch (e) { pushToast(e.message) }
  detailLoading.value = false
}

const ASSISTANT_ROLE_OPTIONS = [
  { key: 'ops', id: 5001, label: '运营权限' },
  { key: 'dev', id: 5002, label: '开发权限' },
]
const assistantMembers = ref([])
const assistantState = reactive({ loading: false, error: '' })
const showAssistantManager = ref(false)
const assistantDialog = reactive({ visible: false, uin: '', note: '', roles: ['dev'], saving: false })
const assistantRoleDialog = reactive({ visible: false, member: null, roles: [], saving: false })

function assistantHeader() {
  return { sub_platform_id: 1904619620, appid: Number(cur.appid) }
}

function normalizeAssistant(profile) {
  const roles = (profile.roles || []).map(role => Number(role) === 5002 ? 'dev' : Number(role) === 5001 ? 'ops' : '').filter(Boolean)
  return {
    uin: String(profile.uin || ''),
    name: profile.nickname || 'QQ 用户',
    avatar: profile.avatar_url || '',
    roles,
  }
}

async function loadAssistantMembers() {
  assistantState.loading = true
  assistantState.error = ''
  try {
    const result = await v2('/bopen/v2/member/getlist', { header: assistantHeader() })
    assistantMembers.value = (result.profiles || []).map(normalizeAssistant).filter(member => member.uin)
  } catch (e) {
    assistantMembers.value = []
    assistantState.error = e.message || '协助者列表加载失败'
  }
  assistantState.loading = false
}

function openAssistantDialog() {
  assistantDialog.uin = ''
  assistantDialog.note = ''
  assistantDialog.roles = ['dev']
  assistantDialog.visible = true
}

async function addAssistant() {
  const uin = assistantDialog.uin.trim()
  if (!/^\d{5,11}$/.test(uin)) return pushToast('QQ 号需为 5~11 位纯数字')
  if (!assistantDialog.roles.length) return pushToast('请至少选择一种协助权限')
  assistantDialog.saving = true
  try {
    await v2('/bopen/v2/member/add', {
      id: { uin },
      roles: assistantDialog.roles.map(key => ASSISTANT_ROLE_OPTIONS.find(role => role.key === key)?.id).filter(Boolean),
      header: assistantHeader(),
    })
    assistantDialog.visible = false
    pushToast('已添加协助者')
    await loadAssistantMembers()
  } catch (e) { pushToast(e.message) }
  assistantDialog.saving = false
}

function openAssistantRoleDialog(member) {
  assistantRoleDialog.member = member
  assistantRoleDialog.roles = [...member.roles]
  assistantRoleDialog.visible = true
}

async function saveAssistantRoles() {
  const member = assistantRoleDialog.member
  if (!member || !assistantRoleDialog.roles.length) return pushToast('请至少保留一种协助权限')
  assistantRoleDialog.saving = true
  const before = new Set(member.roles)
  const after = new Set(assistantRoleDialog.roles)
  const add = [...after].filter(role => !before.has(role))
  const remove = [...before].filter(role => !after.has(role))
  const tasks = []
  if (add.length) tasks.push(v2('/bopen/v2/role/add', {
    id: { uin: member.uin },
    roles: add.map(key => ASSISTANT_ROLE_OPTIONS.find(role => role.key === key)?.id).filter(Boolean),
    header: assistantHeader(),
  }))
  if (remove.length) tasks.push(v2('/bopen/v2/role/delete', {
    id: { uin: member.uin },
    roles: remove.map(key => ASSISTANT_ROLE_OPTIONS.find(role => role.key === key)?.id).filter(Boolean),
    header: assistantHeader(),
  }))
  try {
    await Promise.all(tasks)
    assistantRoleDialog.visible = false
    pushToast('协助者权限已更新')
    await loadAssistantMembers()
  } catch (e) { pushToast(e.message) }
  assistantRoleDialog.saving = false
}

async function removeAssistant(member) {
  if (!confirm(`确认移除协助者 ${member.name}（${member.uin}）？`)) return
  try {
    await v2('/bopen/v2/member/del', { id: { uin: member.uin }, header: assistantHeader() })
    pushToast('已移除协助者')
    await loadAssistantMembers()
  } catch (e) { pushToast(e.message) }
}

const editorInput = ref(null)
async function openBotEditor(field) {
  editor.field = field
  editor.value = field === 'name' ? baseInfo.name : baseInfo.desc
  editor.visible = true
  await nextTick()
  editorInput.value?.focus()
}

async function submitBotEditor() {
  if (editor.loading) return
  const field = editor.field
  const value = editor.value.trim()
  if (field === 'name' && !value) {
    pushToast('昵称不能为空')
    return
  }
  editor.loading = true
  try {
    await v2('/cgi-bin/v2/info/modify', {
      bot_appid: Number(cur.appid),
      filter: { name: field === 'name' ? 1 : 0, avatar: 0, desc: field === 'desc' ? 1 : 0 },
      name: field === 'name' ? value : '',
      avatar_id: '',
      desc: field === 'desc' ? value : '',
    })
    if (field === 'name') {
      baseInfo.name = value
      cur.name = value
    } else {
      baseInfo.desc = value
      cur.desc = value
    }
    editor.visible = false
    pushToast(field === 'name' ? '昵称已更新' : '简介已更新')
  } catch (e) { pushToast(e.message) }
  editor.loading = false
}

async function openAvatarEditor() {
  clearCustomAvatar()
  showAvatarPicker.value = true
  const creating = view.value === 'create'
  if (!officialAvatars.value.length) await loadOfficialAvatars()
  selectedAvatarId.value = (creating ? createDraft.avatarId : officialAvatars.value.find(item => item.url === baseInfo.avatar)?.id)
    || officialAvatars.value[0]?.id
    || ''
}

function clearCustomAvatar() {
  if (customAvatarUrl.value) URL.revokeObjectURL(customAvatarUrl.value)
  customAvatarFile.value = null
  customAvatarUrl.value = ''
}

function closeAvatarEditor() {
  showAvatarPicker.value = false
  clearCustomAvatar()
}

function selectOfficialAvatar(avatar) {
  clearCustomAvatar()
  selectedAvatarId.value = avatar.id
}

function clampCropOffset() {
  const scale = cropper.baseScale * cropper.zoom
  const maxX = Math.max((cropper.naturalWidth * scale - CROP_VIEW_SIZE) / 2, 0)
  const maxY = Math.max((cropper.naturalHeight * scale - CROP_VIEW_SIZE) / 2, 0)
  cropper.offsetX = Math.min(Math.max(cropper.offsetX, -maxX), maxX)
  cropper.offsetY = Math.min(Math.max(cropper.offsetY, -maxY), maxY)
}

function closeCropper() {
  cropper.visible = false
  cropImage = null
  if (cropper.source) URL.revokeObjectURL(cropper.source)
  cropper.source = ''
}

function resetCropper() {
  cropper.zoom = 1
  cropper.offsetX = 0
  cropper.offsetY = 0
}

function openCropper(file) {
  closeCropper()
  const source = URL.createObjectURL(file)
  const image = new Image()
  image.onload = () => {
    cropImage = image
    cropper.source = source
    cropper.fileName = file.name || 'avatar.png'
    cropper.fileType = file.type || 'image/png'
    cropper.naturalWidth = image.naturalWidth
    cropper.naturalHeight = image.naturalHeight
    cropper.baseScale = Math.max(CROP_VIEW_SIZE / image.naturalWidth, CROP_VIEW_SIZE / image.naturalHeight)
    resetCropper()
    cropper.visible = true
  }
  image.onerror = () => {
    URL.revokeObjectURL(source)
    pushToast('图片读取失败')
  }
  image.src = source
}

function startCropDrag(event) {
  if (!cropImage) return
  event.preventDefault()
  const startX = event.clientX
  const startY = event.clientY
  const originX = cropper.offsetX
  const originY = cropper.offsetY
  const move = moveEvent => {
    cropper.offsetX = originX + moveEvent.clientX - startX
    cropper.offsetY = originY + moveEvent.clientY - startY
    clampCropOffset()
  }
  const stop = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', stop)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', stop)
}

function confirmCropper() {
  if (!cropImage) return
  const scale = cropper.baseScale * cropper.zoom
  const width = cropper.naturalWidth * scale
  const height = cropper.naturalHeight * scale
  const left = (CROP_VIEW_SIZE - width) / 2 + cropper.offsetX
  const top = (CROP_VIEW_SIZE - height) / 2 + cropper.offsetY
  const sourceX = -left / scale
  const sourceY = -top / scale
  const sourceSize = CROP_VIEW_SIZE / scale
  const canvas = document.createElement('canvas')
  canvas.width = 512
  canvas.height = 512
  const context = canvas.getContext('2d')
  if (!context) {
    pushToast('头像处理失败')
    return
  }
  context.drawImage(cropImage, sourceX, sourceY, sourceSize, sourceSize, 0, 0, 512, 512)
  canvas.toBlob(blob => {
    if (!blob) {
      pushToast('头像处理失败')
      return
    }
    const cropped = new File([blob], 'avatar.png', { type: 'image/png' })
    clearCustomAvatar()
    customAvatarFile.value = cropped
    customAvatarUrl.value = URL.createObjectURL(cropped)
    selectedAvatarId.value = ''
    closeCropper()
  }, 'image/png', .92)
}

function selectCustomAvatar(event) {
  const input = event.target
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!file.type.startsWith('image/')) {
    pushToast('请选择图片文件')
    return
  }
  if (file.size > 2 * 1024 * 1024) {
    pushToast('图片需小于 2MB')
    return
  }
  openCropper(file)
}

async function submitAvatar() {
  if (!selectedAvatarId.value && !customAvatarFile.value) return
  const creating = view.value === 'create'
  avatarSaving.value = true
  try {
    let avatarId = selectedAvatarId.value
    if (customAvatarFile.value) {
      const form = new FormData()
      form.append('user_id', UID)
      form.append('bot_appid', creating ? '0' : cur.appid)
      form.append('type', creating ? '1' : '2')
      form.append('file', customAvatarFile.value)
      const response = await axios.post('/api/openapi/v2/upload-avatar', form)
      if (response.data?.success === false || !response.data?.upload_id) {
        throw new Error(response.data?.message || '头像上传失败')
      }
      avatarId = response.data.upload_id
    }
    if (creating) {
      clearCreateAvatarPreview()
      createDraft.avatarId = avatarId
      if (customAvatarFile.value) createAvatarPreview.value = URL.createObjectURL(customAvatarFile.value)
      createDraft.avatarUrl = createAvatarPreview.value || officialAvatars.value.find(item => item.id === avatarId)?.url || ''
      createDraft.avatarChanged = true
      closeAvatarEditor()
    } else {
      await v2('/cgi-bin/v2/info/modify', {
        bot_appid: Number(cur.appid),
        filter: { name: 0, avatar: 1, desc: 0 },
        name: '',
        avatar_id: avatarId,
        desc: '',
      })
      closeAvatarEditor()
      await loadAccount()
      pushToast('头像已更新')
    }
  } catch (e) { pushToast(e.message) }
  avatarSaving.value = false
}

function openPrivateProtocol() {
  privacyDialog.email = privateProto.email || status.subjectEmail
  privacyDialog.editing = privateProto.status !== 0
  privacyDialog.visible = true
}

async function updatePrivateProtocol() {
  const email = privacyDialog.email.trim()
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    pushToast('请输入有效邮箱')
    return
  }
  privacyDialog.loading = true
  try {
    await v2('/cgi-bin/v2/info/private_proto/modify', {
      bot_appid: Number(cur.appid),
      contact_email: email,
    })
    privateProto.email = email
    privacyDialog.visible = false
    pushToast('隐私协议已提交')
    await loadAccount()
  } catch (e) { pushToast(e.message) }
  privacyDialog.loading = false
}

async function recoverBot(bot) {
  if (isMember.value) return
  try {
    await v2('/cgi-bin/v2/info/delete', { op: 2, bot_appid: Number(bot.appid) })
    pushToast('已撤回注销')
    await loadBots()
  } catch (e) { pushToast(e.message) }
}

function formatDeleteDate(value) {
  if (!value) return ''
  const date = new Date(value < 1e12 ? value * 1000 : value)
  return Number.isNaN(date.getTime()) ? '' : date.toISOString().slice(0, 10)
}

/* ── 服务范围 ── */
const scope = reactive({
  allowFriend: false,
  allowGroup: false,
  allowChannel: false,
  friendRange: 'all',
  searchable: false,
  friendInfos: [],
  adminGroups: [],
  adminPermissionTarget: 'all',
  allGroupSwitches: {},
  whitelistGroupSwitches: {},
  loaded: false,
})
const GROUP_PERMISSION_ITEMS = [
  { id: 101, label: '进群申请管理', description: '处理机器人加入群聊的申请' },
  { id: 104, label: '群黑名单管理', description: '管理群聊黑名单' },
  { id: 201, label: '发言管理', description: '禁言或解除禁言群成员' },
  { id: 202, label: '撤回消息', description: '撤回群成员发送的消息' },
  { id: 203, label: '@全体成员', description: '在群聊中提醒全体成员' },
  { id: 102, label: '移除群成员', description: '将成员移出群聊' },
]
const groupPicker = reactive({ visible: false, loading: false, purpose: 'admin', groups: [], saving: false })
const groupPermissionSaving = ref(false)
const authExpanded = ref(false)
async function loadScope() {
  detailLoading.value = true
  try {
    const r = await v2('/cgi-bin/v2/bot_scope_manager/scope_cfg/query', {
      bot_appid: Number(cur.appid),
      query_filter: { scope_config: 1, white_list: 1, search_config: 1, group_admin_config: 1 },
    })
    const f = r.friend_scope_config || {}
    const g = r.group_scope_config || {}
    const c = r.channel_scope_config || {}
    const search = r.bot_search_config || {}
    const admin = r.group_admin_config || {}
    scope.allowFriend = (f.allow_others_add_friend || 0) === 2
    scope.friendRange = (f.friend_add_scope || 0) === 2 ? 'all' : 'friend'
    scope.allowGroup = (g.allow_be_added_to_group || 0) === 2
    scope.allowChannel = (c.allow_be_added_to_channel || 0) === 2
    scope.searchable = Number(search.bot_search_status || 0) === 1
    scope.friendInfos = (r.friend_infos || []).map(item => ({
      uin: item.uin === undefined ? '' : String(item.uin),
      name: decodeNickname(item.nick_name),
    }))
    const whitelistRules = admin.whitelist_groups || []
    scope.allGroupSwitches = groupSwitchMap(admin.switches?.length ? admin.switches : [101, 201, 202, 203].map(switch_id => ({ switch_id, current_value: 1 })))
    const whiteSwitches = whitelistRules.flatMap(rule => Array.isArray(rule.switches) ? rule.switches : [])
    scope.whitelistGroupSwitches = groupSwitchMap(whiteSwitches.length ? whiteSwitches : [101, 102, 201, 202, 203].map(switch_id => ({ switch_id, current_value: 1 })))
    scope.adminGroups = normalizeAdminGroups(whitelistRules, r.whitelist_groups_info || {})
    scope.loaded = true
  } catch (e) { pushToast(e.message) }
  detailLoading.value = false
}

function groupSwitchMap(switches) {
  const result = Object.fromEntries(GROUP_PERMISSION_ITEMS.map(item => [item.id, false]))
  for (const item of switches || []) {
    const id = Number(item.switch_id)
    if (Object.prototype.hasOwnProperty.call(result, id)) result[id] = Number(item.current_value || item.default_value) === 1
  }
  return result
}

function normalizeAdminGroups(rules, groupInfo) {
  const ids = [...new Set((rules || []).flatMap(rule => rule.group_ids || []).map(String))]
  return ids.map(id => {
    const info = Array.isArray(groupInfo) ? groupInfo.find(item => String(item.group_id || item.group_code) === id) : groupInfo[id]
    return { id, name: info?.group_name || info?.name || `群 ${id}` }
  })
}

async function toggleSearchable() {
  const searchable = !scope.searchable
  if (searchable && privateProto.status !== 2) {
    openPrivateProtocol()
    pushToast('请先提交并通过隐私协议审核')
    return
  }
  try {
    await v2('/cgi-bin/v2/bot_scope_manager/dev_capability/modify', {
      bot_appid: Number(cur.appid),
      set_dev_filter: { set_search_config: 1 },
      bot_search_config: { bot_search_status: searchable ? 1 : 2 },
    })
    scope.searchable = searchable
    pushToast(searchable ? '已允许被搜索到' : '已关闭搜索展示')
  } catch (e) { pushToast(e.message) }
}

function setGroupPermissionTarget(target) {
  scope.adminPermissionTarget = target
}

function toggleGroupPermission(id) {
  const target = scope.adminPermissionTarget === 'all' ? scope.allGroupSwitches : scope.whitelistGroupSwitches
  target[id] = !target[id]
}

async function saveGroupPermissions() {
  const isAll = scope.adminPermissionTarget === 'all'
  const switches = isAll ? scope.allGroupSwitches : scope.whitelistGroupSwitches
  groupPermissionSaving.value = true
  try {
    await v2('/cgi-bin/v2/bot_scope_manager/dev_capability/modify', {
      bot_appid: Number(cur.appid),
      set_dev_filter: { set_group_admin_config: 1 },
      group_admin_action: {
        admin_scope: isAll ? 2 : 1,
        add_group_ids: [],
        remove_group_ids: [],
        switches: GROUP_PERMISSION_ITEMS.map(item => ({ switch_id: item.id, current_value: switches[item.id] ? 1 : 2 })),
      },
    })
    pushToast('群管理权限已保存')
    await loadScope()
  } catch (e) { pushToast(e.message) }
  groupPermissionSaving.value = false
}

async function loadBotGroups() {
  const groups = []
  let pagingCookie = ''
  let hasMore = true
  let pages = 0
  while (hasMore && pages < 20) {
    const r = await v2('/cgi-bin/v2/bot_scope_manager/bot_group_list/query', {
      bot_appid: Number(cur.appid),
      paging_cookie: pagingCookie,
      page_size: 20,
    })
    const result = r.groups || r.data?.groups || []
    groups.push(...result.map(group => ({
      id: String(group.group_id || group.group_code || ''),
      name: group.group_name || `群 ${group.group_id || group.group_code || ''}`,
      inGroup: group.in_group !== false,
    })).filter(group => group.id))
    pagingCookie = r.next_paging_cookie || r.paging_cookie || ''
    hasMore = !!r.has_more && !!pagingCookie
    pages += 1
  }
  return groups
}

async function openGroupPicker(purpose = 'admin') {
  groupPicker.visible = true
  groupPicker.purpose = purpose
  groupPicker.groups = []
  groupPicker.loading = true
  try {
    groupPicker.groups = (await loadBotGroups()).filter(group => group.inGroup)
  } catch (e) { pushToast(e.message) }
  groupPicker.loading = false
}

function groupAlreadySelected(group) {
  if (groupPicker.purpose === 'admin') return scope.adminGroups.some(item => item.id === group.id)
  return dev.sensitiveGroups.some(item => item.groupCode === group.id)
}

async function selectBotGroup(group) {
  if (groupAlreadySelected(group) || groupPicker.saving) return
  groupPicker.saving = true
  try {
    if (groupPicker.purpose === 'admin') {
      if (scope.adminGroups.length >= 20) throw new Error('白名单群最多添加 20 个')
      await v2('/cgi-bin/v2/bot_scope_manager/dev_capability/modify', {
        bot_appid: Number(cur.appid),
        set_dev_filter: { set_group_admin_config: 1 },
        group_admin_action: { admin_scope: 1, add_group_ids: [Number(group.id)], remove_group_ids: [] },
      })
      await loadScope()
      pushToast('已添加白名单群')
    } else {
      await modifySensitiveGroup(group.id, 1, group.name)
    }
    groupPicker.visible = false
  } catch (e) { pushToast(e.message) }
  groupPicker.saving = false
}

async function removeAdminGroup(group) {
  if (!confirm(`确认移除白名单群「${group.name}」？`)) return
  try {
    await v2('/cgi-bin/v2/bot_scope_manager/dev_capability/modify', {
      bot_appid: Number(cur.appid),
      set_dev_filter: { set_group_admin_config: 1 },
      group_admin_action: { admin_scope: 1, add_group_ids: [], remove_group_ids: [Number(group.id)] },
    })
    pushToast('已移除白名单群')
    await loadScope()
  } catch (e) { pushToast(e.message) }
}
async function toggleScope(which) {
  if (!isCertified.value) {
    pushToast('当前主体未认证，无法开启公开服务范围')
    return
  }
  const currentlyEnabled = which === 'friend' ? scope.allowFriend : which === 'group' ? scope.allowGroup : scope.allowChannel
  if (!currentlyEnabled && privateProto.status !== 2) {
    openPrivateProtocol()
    pushToast('请先提交并通过隐私协议审核')
    return
  }
  const payload = { bot_appid: Number(cur.appid), set_scope_filter: { set_friend_scope: 0, set_group_scope: 0, set_channel_scope: 0 } }
  if (which === 'friend') {
    payload.set_scope_filter.set_friend_scope = 1
    payload.friend_scope_config = {
      allow_others_add_friend: scope.allowFriend ? 1 : 2,
      friend_add_scope: scope.friendRange === 'all' ? 2 : 1,
    }
  } else if (which === 'group') {
    payload.set_scope_filter.set_group_scope = 1
    payload.group_scope_config = { allow_be_added_to_group: scope.allowGroup ? 1 : 2 }
  } else {
    payload.set_scope_filter.set_channel_scope = 1
    payload.channel_scope_config = { allow_be_added_to_channel: scope.allowChannel ? 1 : 2 }
  }
  try {
    await v2('/cgi-bin/v2/bot_scope_manager/usage_scope/modify', payload)
    if (which === 'friend') scope.allowFriend = !scope.allowFriend
    else if (which === 'group') scope.allowGroup = !scope.allowGroup
    else scope.allowChannel = !scope.allowChannel
    pushToast('操作成功')
  } catch (e) { pushToast(e.message) }
}

async function saveFriendRange(range = scope.friendRange) {
  try {
    await v2('/cgi-bin/v2/bot_scope_manager/usage_scope/modify', {
      bot_appid: Number(cur.appid),
      set_scope_filter: { set_friend_scope: 1, set_group_scope: 0, set_channel_scope: 0 },
      friend_scope_config: {
        allow_others_add_friend: scope.allowFriend ? 2 : 1,
        friend_add_scope: range === 'all' ? 2 : 1,
      },
    })
    scope.friendRange = range
    pushToast('好友添加范围已更新')
    return true
  } catch (e) {
    pushToast(e.message)
    await loadScope()
    return false
  }
}

function decodeNickname(value) {
  if (!value) return ''
  try {
    const bytes = Uint8Array.from(atob(value), c => c.charCodeAt(0))
    return new TextDecoder('utf-8').decode(bytes)
  } catch (e) {
    return value
  }
}

async function addDeveloperTester() {
  const uin = testerInput.value.trim()
  if (!/^\d{5,11}$/.test(uin)) {
    pushToast('QQ 号需为 5~11 位纯数字')
    return
  }
  try {
    await v2('/cgi-bin/v2/bot_scope_manager/white_list/modify', {
      bot_appid: Number(cur.appid),
      whitelist_filter: { set_friend_whitelist: 1 },
      friend_whitelist: { white_ids: [Number(uin)] },
    })
    showAddTester.value = false
    testerInput.value = ''
    pushToast('已添加用户')
    await loadScope()
  } catch (e) { pushToast(e.message) }
}

async function removeDeveloperTester(uin) {
  if (!confirm(`确认移除用户 ${uin}？`)) return
  try {
    await v2('/cgi-bin/v2/bot_scope_manager/white_list/modify', {
      bot_appid: Number(cur.appid),
      whitelist_filter: { set_friend_whitelist: 2 },
      friend_whitelist: { white_ids: [Number(uin)] },
    })
    pushToast('已删除用户')
    await loadScope()
  } catch (e) { pushToast(e.message) }
}

/* ── 运营数据 ── */
const DATA_TYPES = [
  { key: 'msg', title: '消息数据', type: 1 },
  { key: 'group', title: '群数据', type: 2 },
  { key: 'friend', title: '单聊数据', type: 3 },
  { key: 'guild', title: '频道数据', type: 0 },
]
const RANGES = [{ v: 0, label: '最近7天' }, { v: 1, label: '最近14天' }, { v: 2, label: '最近30天' }]
const reports = reactive({})
const reportChartMode = ref(false)
const REPORT_PAGE_SIZE = 10
DATA_TYPES.forEach(d => (reports[d.key] = { range: 2, sceneId: 1, page: 1, rows: [], loading: false, version: 0 }))

const msgSummary = computed(() => {
  const rows = reports.msg?.rows || []
  const days = rows.length
  const totalUp = rows.reduce((sum, row) => sum + Number(row.upMsgCnt || 0), 0)
  const totalUv = rows.reduce((sum, row) => sum + Number(row.upMsgUv || 0), 0)
  return {
    days,
    totalUp,
    avgDau: days ? Math.round((totalUv / days) * 100) / 100 : 0,
  }
})

const REPORT_COLS = {
  friend: [
    { key: 'date', label: '时间' }, { key: 'stock', label: '存量添加用户数' },
    { key: 'active', label: '使用用户数' }, { key: 'newAdd', label: '新添加用户数' }, { key: 'newRemove', label: '新移除用户数' },
  ],
  guild: [
    { key: 'date', label: '时间' }, { key: 'stock', label: '存在频道数' },
    { key: 'active', label: '使用频道数' }, { key: 'newAdd', label: '新增频道数' }, { key: 'newRemove', label: '减少频道数' },
  ],
  group: [
    { key: 'date', label: '时间' }, { key: 'stock', label: '存在群数' },
    { key: 'active', label: '使用群数' }, { key: 'newAdd', label: '新添加群数' }, { key: 'newRemove', label: '新移除群数' },
  ],
  msg: [
    { key: 'date', label: '时间' }, { key: 'scene', label: '场景' }, { key: 'upMsgCnt', label: '上行消息量' },
    { key: 'upMsgUv', label: '上行消息人数' }, { key: 'downMsgCnt', label: '下行消息量' },
    { key: 'botMsgCnt', label: '总消息量' }, { key: 'nextDayRetention', label: '对话用户次日留存' },
  ],
}
function fmtDate(s) { const t = String(s); return t.length === 8 ? `${t.slice(0, 4)}-${t.slice(4, 6)}-${t.slice(6, 8)}` : t }
function mapRows(key, data) {
  if (key === 'friend') return (data.friend_data || []).map(i => ({
    date: fmtDate(i.report_date ?? i.reportDate),
    stock: i.stock_added_friends ?? i.stockAddedFriends ?? 0,
    active: i.used_friends ?? i.usedFriends ?? 0,
    newAdd: i.new_added_friends ?? i.newAddedFriends ?? 0,
    newRemove: i.new_removed_friends ?? i.newRemovedFriends ?? 0,
  }))
  if (key === 'guild') return (data.guild_data || []).map(i => ({
    date: fmtDate(i.report_date ?? i.reportDate),
    stock: i.in_guild_cnt ?? i.inGuildCnt ?? 0,
    active: i.used_guild_cnt ?? i.usedGuildCnt ?? 0,
    newAdd: i.add_guild_cnt ?? i.addGuildCnt ?? 0,
    newRemove: i.removed_guild_cnt ?? i.removedGuildCnt ?? 0,
  }))
  if (key === 'group') return (data.group_data || []).map(i => ({
    date: fmtDate(i.report_date ?? i.reportDate),
    stock: i.existing_groups ?? i.existingGroups ?? 0,
    active: i.used_groups ?? i.usedGroups ?? 0,
    newAdd: i.added_groups ?? i.addedGroups ?? 0,
    newRemove: i.removed_groups ?? i.removedGroups ?? 0,
  }))
  return (data.msg_data || []).map(i => ({
    date: fmtDate(i.report_date ?? i.reportDate),
    scene: i.scene_name ?? i.sceneName ?? '',
    upMsgCnt: i.up_msg_cnt ?? i.upMsgCnt ?? 0,
    upMsgUv: i.up_msg_uv ?? i.upMsgUv ?? 0,
    downMsgCnt: i.down_msg_cnt ?? i.downMsgCnt ?? 0,
    botMsgCnt: i.bot_msg_cnt ?? i.botMsgCnt ?? 0,
    nextDayRetention: i.next_day_retention ?? i.nextDayRetention ?? 0,
  }))
}
async function loadReport(d) {
  const st = reports[d.key]
  st.loading = true
  try {
    const r = await v2('/cgi-bin/v2/datareport/query', { bot_appid: Number(cur.appid), data_range: st.range, data_type: d.type, scene_id: st.sceneId })
    st.rows = mapRows(d.key, r.data && typeof r.data === 'object' ? r.data : r)
    st.page = 1
    st.version++
  } catch (e) { pushToast(e.message) }
  st.loading = false
}
async function loadAllReports() { await Promise.all(DATA_TYPES.map(loadReport)) }
function changeRange(d, v) { reports[d.key].range = v; loadReport(d) }
function changeScene(d, v) { reports[d.key].sceneId = v; loadReport(d) }
function reportPageCount(key) { return Math.max(1, Math.ceil(reports[key].rows.length / REPORT_PAGE_SIZE)) }
function reportPageRows(key) {
  const start = (reports[key].page - 1) * REPORT_PAGE_SIZE
  return reports[key].rows.slice(start, start + REPORT_PAGE_SIZE)
}
async function downloadPlatformReport(type, range, scene, title) {
  const resp = await axios.post('/api/openapi/v2/proxy', {
    user_id: UID, method: 'GET', path: '/cgi-bin/v2/datareport/export',
    params: { bot_appid: Number(cur.appid), data_type: type, data_range: range, scene_id: scene },
  }, { responseType: 'blob' })
  let blob = resp.data
  if ((resp.headers['content-type'] || blob.type || '').includes('application/json')) {
    const result = JSON.parse(await blob.text())
    if (result.retcode !== 0 || typeof result.data !== 'string') {
      throw new Error(result.message || result.msg || '下载失败')
    }
    const content = result.data.startsWith('\uFEFF') ? result.data : `\uFEFF${result.data}`
    blob = new Blob([content], { type: 'text/csv;charset=utf-8' })
  }
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${title}.csv`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

async function downloadReport(report) {
  const { range, sceneId } = reports[report.key]
  try {
    await downloadPlatformReport(report.type, range, sceneId, report.title)
  } catch (error) {
    pushToast(error.message || '下载失败')
  }
}

/* ── 开发设置 ── */
const dev = reactive({
  appid: '',
  secret: '****************',
  secretShown: false,
  mode: 'WebSocket',
  webhook: '',
  ips: [],
  events: [],
  allSubscriptions: false,
  sensitiveUsers: [],
  sensitiveGroups: [],
  helperFriend: null,
  loaded: false,
})
const connectionDraft = reactive({
  mode: 'WebSocket',
  webhook: '',
  saving: false,
})
const eventsSaving = ref(false)
const savedEvents = ref({ all: false, ids: [] })
const connectionChanged = computed(() => connectionDraft.mode !== dev.mode || (connectionDraft.mode === 'Webhook' && connectionDraft.webhook.trim() !== dev.webhook))
const eventsChanged = computed(() => dev.allSubscriptions !== savedEvents.value.all || JSON.stringify(dev.events.filter(event => event.selected).map(event => event.id).sort()) !== JSON.stringify(savedEvents.value.ids))
function snapshotEvents() { savedEvents.value = { all: dev.allSubscriptions, ids: dev.events.filter(event => event.selected).map(event => event.id).sort() } }
function resetEvents() {
  dev.allSubscriptions = savedEvents.value.all
  dev.events.forEach(event => { event.selected = savedEvents.value.ids.includes(event.id) })
}
function resetConnection() {
  connectionDraft.mode = dev.mode
  connectionDraft.webhook = dev.webhook
  webhookCheck.msg = ''
}
const showAddTester = ref(false)
const testerInput = ref('')
const secretResetDialog = ref(false)
const sensitiveDialog = reactive({ visible: false, type: 'user', value: '', saving: false })

async function loadDevSettings() {
  detailLoading.value = true
  dev.appid = cur.appid
  try {
    const r = await v2('/cgi-bin/v2/bot_dev_setting/query', { bot_appid: Number(cur.appid), filter: { ip_whitelist_info: 1, connect_info: 1, event: 1, sensitive_alert: 1 } })
    const s = r.data && typeof r.data === 'object' ? r.data : r
    dev.ips = (s.ip_whitelist_info && s.ip_whitelist_info.ip_addresses) || []
    const alerts = s.sensitive_alert_info || {}
    dev.sensitiveUsers = (alerts.users || []).map(user => ({ uin: String(user.uin || ''), name: user.nick_name || 'QQ 用户' })).filter(user => user.uin)
    dev.sensitiveGroups = (alerts.groups || []).map(group => ({ groupCode: String(group.group_code || ''), groupName: group.group_name || 'QQ群' })).filter(group => group.groupCode)
    const ci = s.connect_info || {}
    dev.mode = (ci.mode || 0) === 1 ? 'WebSocket' : 'Webhook'
    dev.webhook = ci.webhook_url || ''
    connectionDraft.mode = dev.mode
    connectionDraft.webhook = dev.webhook
    const eventResult = s.event?.events ? s.event : await v2('/cgi-bin/v2/event_subscirption/list', { bot_appid: Number(cur.appid) })
    const eventData = eventResult.data && typeof eventResult.data === 'object' ? eventResult.data : eventResult
    dev.allSubscriptions = !!eventData.all_subscription
    dev.events = (eventData.events || []).map(event => ({
      id: event.id || '',
      name: event.name || '',
      description: event.description || '',
      type: event.type || '其他',
      selected: !!event.is_subscribed,
      weight: event.weight || 0,
    })).sort((a, b) => a.weight - b.weight)
    snapshotEvents()
    const whitelistResult = await v2('/cgi-bin/v2/bot_scope_manager/scope_cfg/query', {
      bot_appid: Number(cur.appid),
      query_filter: { scope_config: 0, white_list: 1, search_config: 0 },
    })
    scope.friendInfos = (whitelistResult.friend_infos || []).map(item => ({
      uin: item.uin === undefined ? '' : String(item.uin),
      name: decodeNickname(item.nick_name),
      addedAt: item.create_time || item.add_time || 0,
    }))
    dev.loaded = true
    await checkBotHelper(false)
  } catch (e) { pushToast(e.message) }
  detailLoading.value = false
}

async function addBotHelper() {
  try {
    await v2('/cgi-bin/v2/bot_dev_setting/helper/add', {})
    pushToast('已发送添加官方助手请求')
    await checkBotHelper()
  } catch (e) { pushToast(e.message) }
}

async function checkBotHelper(showToast = true) {
  try {
    const result = await v2('/cgi-bin/v2/bot_dev_setting/helper/check', {})
    const data = result.data && typeof result.data === 'object' ? result.data : result
    dev.helperFriend = !!data.is_friend
    if (showToast) pushToast(dev.helperFriend ? '官方助手已添加' : '尚未添加官方助手')
  } catch (e) { if (showToast) pushToast(e.message) }
}

function openSensitiveDialog(type) {
  sensitiveDialog.type = type
  sensitiveDialog.value = ''
  sensitiveDialog.visible = true
}

async function addSensitiveUser() {
  const uin = sensitiveDialog.value.trim()
  if (!/^\d{5,11}$/.test(uin)) return pushToast('QQ 号需为 5~11 位纯数字')
  if (dev.sensitiveUsers.length >= 20) return pushToast('敏感提醒用户最多添加 20 位')
  if (dev.sensitiveUsers.some(user => user.uin === uin)) return pushToast('该用户已添加')
  sensitiveDialog.saving = true
  try {
    await v2('/cgi-bin/v2/bot_dev_setting/sensitive_alert/user/modify', {
      bot_appid: Number(cur.appid), action: 1, uins: [Number(uin)],
    })
    dev.sensitiveUsers.push({ uin, name: 'QQ 用户' })
    sensitiveDialog.visible = false
    pushToast('已添加提醒用户')
  } catch (e) { pushToast(e.message) }
  sensitiveDialog.saving = false
}

async function modifySensitiveGroup(groupCode, action, groupName = 'QQ群') {
  if (action === 1 && dev.sensitiveGroups.length >= 20) throw new Error('敏感提醒群最多添加 20 个')
  await v2('/cgi-bin/v2/bot_dev_setting/sensitive_alert/group/modify', {
    bot_appid: Number(cur.appid), action, group_codes: [Number(groupCode)],
  })
  if (action === 1) {
    if (!dev.sensitiveGroups.some(group => group.groupCode === String(groupCode))) {
      dev.sensitiveGroups.push({ groupCode: String(groupCode), groupName })
    }
    pushToast('已添加提醒群')
  } else {
    dev.sensitiveGroups = dev.sensitiveGroups.filter(group => group.groupCode !== String(groupCode))
    pushToast('已移除提醒群')
  }
}

async function saveSensitiveDialog() {
  if (sensitiveDialog.type === 'user') return addSensitiveUser()
  const groupCode = sensitiveDialog.value.trim()
  if (!/^\d{5,15}$/.test(groupCode)) return pushToast('请输入有效的群号')
  sensitiveDialog.saving = true
  try {
    await modifySensitiveGroup(groupCode, 1)
    sensitiveDialog.visible = false
  } catch (e) { pushToast(e.message) }
  sensitiveDialog.saving = false
}

async function removeSensitiveUser(user) {
  if (!confirm(`确认移除提醒用户 ${user.uin}？`)) return
  try {
    await v2('/cgi-bin/v2/bot_dev_setting/sensitive_alert/user/modify', {
      bot_appid: Number(cur.appid), action: 2, uins: [Number(user.uin)],
    })
    dev.sensitiveUsers = dev.sensitiveUsers.filter(item => item.uin !== user.uin)
    pushToast('已移除提醒用户')
  } catch (e) { pushToast(e.message) }
}

async function removeSensitiveGroup(group) {
  if (!confirm(`确认移除提醒群「${group.groupName}」？`)) return
  try { await modifySensitiveGroup(group.groupCode, 2) } catch (e) { pushToast(e.message) }
}
const ipInput = ref('')
const showAddIp = ref(false)
async function addIp() {
  const ip = ipInput.value.trim()
  if (!/^(?:\d{1,3}\.){3}\d{1,3}$/.test(ip) || ip.split('.').some(v => Number(v) > 255)) {
    pushToast('请输入有效 IPv4 地址')
    return
  }
  if (dev.ips.includes(ip)) {
    pushToast('该 IP 已存在')
    return
  }
  try {
    await v2('/cgi-bin/v2/bot_dev_setting/ip_whitelist/modify', {
      bot_appid: Number(cur.appid),
      action: 1,
      ip_addresses: [ip],
    })
    dev.ips.push(ip)
    ipInput.value = ''
    showAddIp.value = false
    pushToast('添加成功')
  } catch (e) { pushToast(e.message) }
}
async function removeIp(ip) {
  try {
    await v2('/cgi-bin/v2/bot_dev_setting/ip_whitelist/modify', {
      bot_appid: Number(cur.appid),
      action: 2,
      ip_addresses: [ip],
    })
    dev.ips = dev.ips.filter(i => i !== ip)
    pushToast('删除成功')
  } catch (e) { pushToast(e.message) }
}

function openConnectionSettings() {
  connectionDraft.mode = dev.mode
  connectionDraft.webhook = dev.webhook
  webhookCheck.ok = null
  webhookCheck.msg = ''
  resetEvents()
  activeSub.value = 'event-callback'
  loadWebhookSuggest()
}

const webhookSuggest = reactive({ available: false, url: '' })
const webhookCheck = reactive({ checking: false, ok: null, msg: '' })
const connectionBusy = computed(() => connectionDraft.saving || eventsSaving.value || webhookCheck.checking)

async function loadWebhookSuggest() {
  const appid = cur.appid
  webhookSuggest.available = false
  webhookSuggest.url = ''
  try {
    const { data } = await axios.post('/api/openapi/v2/webhook-suggest', { user_id: UID, appid })
    if (data.success && cur.appid === appid) {
      webhookSuggest.available = !!data.available
      webhookSuggest.url = data.url || ''
    }
  } catch (e) { /* ignore */ }
}

function fillSuggestedWebhook() {
  if (!webhookSuggest.url) return
  connectionDraft.webhook = webhookSuggest.url
  webhookCheck.msg = ''
}

function webhookUrlError(url) {
  if (!url) return '请输入回调地址'
  if (/^https?:\/\/[^/\\\s]/i.test(url)) {
    try {
      if (new URL(url).hostname) return ''
    } catch { /* 地址格式无效 */ }
  }
  return '请输入有效的 HTTP 或 HTTPS 回调地址，支持直接使用 IP'
}

function webhookFailureMessage(error, fallback) {
  if (error.platformCode) {
    const message = error.message === `code=${error.platformCode}` ? fallback : error.message
    return `${message}（错误码 ${error.platformCode}）`
  }
  if (error.code === 'ECONNABORTED') return '请求超时，请稍后重试'
  if (error.code === 'ERR_NETWORK') return '无法连接 Web 面板后端，请检查网络连接'
  return error.response?.data?.message || error.response?.data?.msg || error.message || fallback
}

async function checkWebhookUrl() {
  if (connectionBusy.value) return
  const appid = cur.appid
  const url = connectionDraft.webhook.trim()
  const inputError = webhookUrlError(url)
  if (inputError) {
    Object.assign(webhookCheck, { ok: false, msg: inputError })
    return
  }
  Object.assign(webhookCheck, { checking: true, ok: null, msg: '' })
  let ok = false
  let msg = '地址校验未通过'
  try {
    await v2('/cgi-bin/callback/check_webhook', { bot_appid: String(appid), webhook_url: url })
    ok = true
    msg = '地址校验通过'
  } catch (e) {
    msg = webhookFailureMessage(e, msg)
  } finally {
    webhookCheck.checking = false
    if (cur.appid === appid && connectionDraft.mode === 'Webhook' && connectionDraft.webhook.trim() === url) {
      Object.assign(webhookCheck, { ok, msg })
    }
  }
}

async function saveConnection() {
  if (connectionBusy.value || !connectionChanged.value) return false
  if (connectionDraft.mode === 'Webhook') {
    const url = connectionDraft.webhook.trim()
    const inputError = webhookUrlError(url)
    if (inputError) {
      Object.assign(webhookCheck, { ok: false, msg: inputError })
      return false
    }
    connectionDraft.saving = true
    try {
      await v2('/cgi-bin/v2/bot_dev_setting/connect_info/modify', {
        bot_appid: Number(cur.appid),
        action: 1,
        webhook_url: url,
      })
      dev.webhook = url
      dev.mode = 'Webhook'
      webhookCheck.msg = ''
      pushToast('Webhook 接入配置已保存')
      return true
    } catch (e) {
      Object.assign(webhookCheck, { ok: false, msg: webhookFailureMessage(e, '回调地址保存失败') })
      return false
    }
    finally { connectionDraft.saving = false }
  }
  connectionDraft.saving = true
  try {
    await v2('/cgi-bin/v2/bot_dev_setting/connect_info/modify', {
      bot_appid: Number(cur.appid),
      action: 3,
    })
    dev.mode = 'WebSocket'
    pushToast('已切换为 WebSocket')
    return true
  } catch (e) { pushToast(e.message); return false }
  finally { connectionDraft.saving = false }
}

async function saveEvents() {
  if (connectionBusy.value || connectionChanged.value || dev.mode !== 'Webhook' || !eventsChanged.value) return
  const url = connectionDraft.webhook.trim()
  const inputError = webhookUrlError(url)
  if (inputError) {
    Object.assign(webhookCheck, { ok: false, msg: inputError })
    return
  }
  eventsSaving.value = true
  try {
    const action = connectionDraft.mode === 'WebSocket' ? 3 : 1
    await v2('/cgi-bin/v2/bot_dev_setting/connect_info/modify', {
      bot_appid: Number(cur.appid),
      action,
      ...(action === 1 ? { webhook_url: url } : {}),
      filter: { event: 1 },
      event: {
        all_subscription: dev.allSubscriptions,
        event_ids: dev.allSubscriptions ? [] : dev.events.filter(event => event.selected).map(event => event.id),
      },
    })
    dev.mode = connectionDraft.mode
    dev.webhook = url
    snapshotEvents()
    pushToast(dev.allSubscriptions ? '已设置接收全部事件' : `接收事件配置已保存（共 ${dev.events.filter(event => event.selected).length} 个）`)
  } catch (e) { pushToast(e.message) }
  finally { eventsSaving.value = false }
}
const eventGroups = computed(() => {
  const groups = new Map()
  dev.events.forEach(event => {
    if (!groups.has(event.type)) groups.set(event.type, [])
    groups.get(event.type).push(event)
  })
  return Array.from(groups, ([name, items]) => ({ name, items }))
})
const selectedEventCount = computed(() => dev.allSubscriptions ? dev.events.length : dev.events.filter(event => event.selected).length)
function selectAllEvents(selected) {
  dev.allSubscriptions = false
  dev.events.forEach(event => { event.selected = selected })
}

const featurePreview = ref({})
const showFeatureImages = ref(false)
const featurePreviewUrl = ref('')
const featurePreviewItems = computed(() => featurePreview.value.medias || [])
const featurePreviewText = computed(() => ({ 1: '审核中', 2: '已通过', 3: '审核未通过' })[featurePreview.value.status] || '未提交')
const featurePreviewClass = computed(() => ({ 1: 'reviewing', 2: 'approved', 3: 'rejected' })[featurePreview.value.status] || 'none')
async function loadFeaturePreview() {
  try {
    const result = await v2('/cgi-bin/v2/info/query', { bot_appid: Number(cur.appid), filter: { base_info: 1 } })
    featurePreview.value = result.base_info?.preview_medias_info || {}
    if (result.base_info?.online_state) cur.online = result.base_info.online_state.online_state === 1
  } catch (e) { pushToast(e.message) }
}
const showAdminGroups = ref(false)
const friendRangeDialog = reactive({ visible: false, value: 'all', saving: false })
function openFriendRange() { friendRangeDialog.value = scope.friendRange; friendRangeDialog.visible = true }
async function applyFriendRange() {
  friendRangeDialog.saving = true
  try {
    if (await saveFriendRange(friendRangeDialog.value)) friendRangeDialog.visible = false
  } finally { friendRangeDialog.saving = false }
}

/* ── 高级设置入口 ── */
function openFunctionConfig() { activeSub.value = 'menu-command' }

function openAIServiceSettings() { activeSub.value = 'switch-service' }

async function resetSecret() {
  if (isMember.value) return
  const appid = cur.appid
  try {
    const r = await v2('/cgi-bin/v2/bot_dev_setting/secret/reset', { bot_appid: Number(appid) })
    if (appid !== cur.appid || !['manage', 'create-connect'].includes(view.value)) return
    dev.secret = r.secret || r.app_secret || '重置成功, 请在弹窗查看'
    dev.secretShown = true
    secretResetDialog.value = false
    pushToast('AppSecret 已重置')
  } catch (e) { pushToast(e.message) }
}

/* ── 创建机器人 ── */
const createLoading = ref(false)
const checkingCreatedConnection = ref(false)
const showDiscardCreate = ref(false)
const officialAvatars = ref([])
const createAvatarPreview = ref('')
const createdSecret = ref('')
const createDraft = reactive({ name: '', desc: '', avatarId: '', avatarUrl: '', avatarChanged: false })

function clearCreateAvatarPreview() {
  if (createAvatarPreview.value) URL.revokeObjectURL(createAvatarPreview.value)
  createAvatarPreview.value = ''
}

function closeCreate() {
  showDiscardCreate.value = false
  clearCreateAvatarPreview()
  backToList()
}

function cancelCreate() {
  if (createLoading.value) return
  if (createDraft.name || createDraft.desc || createDraft.avatarChanged) showDiscardCreate.value = true
  else closeCreate()
}

async function loadOfficialAvatars() {
  if (officialAvatars.value.length) return
  try {
    const r = await v2('/cgi-bin/v2/info/official/query', {})
    officialAvatars.value = (r.avatars || []).map(item => ({
      id: item.avatar_id || '',
      url: item.avatar_url || '',
    })).filter(item => item.id && item.url)
  } catch (e) { pushToast(e.message) }
}

async function openCreate() {
  if (isMember.value) {
    pushToast('你不是主体账号的管理员，无法创建机器人')
    return
  }
  if (createRemain.value <= 0) {
    pushToast('当前主体机器人数量已达上限')
    return
  }
  clearCreateAvatarPreview()
  Object.assign(createDraft, { name: '', desc: '', avatarId: '', avatarUrl: '', avatarChanged: false })
  view.value = 'create'
  await loadOfficialAvatars()
  if (view.value === 'create' && !createDraft.avatarId && officialAvatars.value.length) {
    const avatar = officialAvatars.value[Math.floor(Math.random() * officialAvatars.value.length)]
    Object.assign(createDraft, { avatarId: avatar.id, avatarUrl: avatar.url })
  }
}

async function submitCreate() {
  if (isMember.value) return
  if (!createDraft.name.trim() || !createDraft.avatarId || createLoading.value) return
  createLoading.value = true
  try {
    const result = await v2('/cgi-bin/v2/bot/create', {
      name: createDraft.name.trim(),
      avatar_id: createDraft.avatarId,
      desc: createDraft.desc.trim(),
    })
    const createId = result.create_id || ''
    const interval = Math.max(Number(result.poll_interval_ms || 1000), 500)
    const maxTimes = Math.max(Number(result.poll_max_times || 10), 1)
    let created = null
    for (let i = 0; i < maxTimes; i += 1) {
      await new Promise(resolve => setTimeout(resolve, interval))
      const query = await v2('/cgi-bin/v2/bot/create/query_result', { create_id: createId })
      if (query.ret_code === 3) {
        created = query
        break
      }
      if ([0, 2].includes(query.ret_code)) {
        throw new Error(query.msg || '创建机器人失败')
      }
    }
    if (!created) throw new Error('创建机器人超时，请稍后刷新查看')
    pushToast('机器人创建成功')
    await loadBots()
    const appid = String(created.bot_appid || '')
    const bot = bots.value.find(item => item.appid === appid)
    if (appid && status.ready) {
      Object.assign(cur, bot || {
        appid, name: createDraft.name.trim(), avatar: createAvatarPreview.value ? '' : createDraft.avatarUrl,
        desc: createDraft.desc.trim(), uin: '', online: false, status: 1, revoking: false, serviceConnected: false,
        finalDeleteTime: 0, color: PALETTE[0],
      })
      createdSecret.value = created.bot_secret || ''
      dev.secret = '****************'
      dev.secretShown = false
      view.value = 'create-connect'
    } else backToList()
    clearCreateAvatarPreview()
  } catch (e) { pushToast(e.message) }
  createLoading.value = false
}

function skipCreatedConnection() {
  backToList()
  pushToast('机器人已创建，可稍后在管理页连接服务')
}

async function checkCreatedConnection() {
  if (checkingCreatedConnection.value) return
  const appid = cur.appid
  checkingCreatedConnection.value = true
  try {
    const result = await v2('/cgi-bin/v2/info/query', { bot_appid: Number(appid), filter: { base_info: 1, online_state: 1 } })
    if (view.value !== 'create-connect' || appid !== cur.appid) return
    cur.serviceConnected = Boolean((result.base_info?.online_state || result.online_state)?.links?.length)
    if (!cur.serviceConnected) {
      pushToast('暂未检测到连接，请在手机 QQ 完成授权后重试')
      return
    }
    backToList()
    pushToast('机器人已连接')
    await loadBots()
  } catch (error) {
    pushToast(error.message || '无法查询连接状态，请重试')
  } finally {
    checkingCreatedConnection.value = false
  }
}

function copyText(t) {
  if (!t) return
  navigator.clipboard?.writeText(t).then(() => pushToast('已复制')).catch(() => {})
}

onMounted(async () => {
  await loadStatus()
  if (status.ready) loadBots()
})
onUnmounted(() => {
  closeScan()
  closeCropper()
  clearCustomAvatar()
  clearCreateAvatarPreview()
})
defineExpose({ reload: loadStatus })
</script>

<template>
  <div class="qqdash" :data-theme="themeStore.darkMode ? 'dark' : 'light'">
    <div :class="['app-shell', { 'platform-content': status.ready }]">
      <!-- 主区 -->
      <main class="main">
        <!-- 状态加载中 -->
        <div v-if="!statusLoaded" class="page">
          <div class="empty-hint">加载中...</div>
        </div>

        <!-- 未授权提示 -->
        <div v-else-if="!status.ready" class="page">
          <div class="v2-gate">
            <div class="v2-gate-hero">
              <div class="v2-gate-icon"><AppIcon name="robot" :size="34" /></div>
              <div>
                <h2 class="page-title">QQ 机器人管理面板</h2>
                <p class="page-sub">连接 QQ 开放平台账号后，可在 Elaina 中管理机器人、服务范围、运营数据与开发设置。</p>
              </div>
            </div>
            <div class="v2-login-overview">
              <div class="v2-login-step">
                <span class="v2-login-step-num">1</span>
                <div><b>创建登录会话</b><span>由 Elaina 生成一次性授权入口</span></div>
              </div>
              <div class="v2-login-step">
                <span class="v2-login-step-num">2</span>
                <div><b>在 QQ 中确认</b><span>通过扫码或授权链接登录，并选择开发者主体</span></div>
              </div>
              <div class="v2-login-step">
                <span class="v2-login-step-num">3</span>
                <div><b>自动完成授权</b><span>登录凭证仅在服务端内部保存，不在页面展示</span></div>
              </div>
            </div>
            <div class="v2-gate-actions">
              <button class="btn primary large" :disabled="scanLoading" @click="startScan()">
                <AppIcon name="qr" :size="17" />
                使用 QQ 扫码登录
              </button>
              <button class="btn primary outline large" :disabled="scanLoading" @click="startScan('mobile')">
                <AppIcon name="external" :size="17" />
                在手机 QQ 中授权
              </button>
            </div>
          </div>
        </div>

        <!-- 机器人列表 -->
        <div v-else-if="view === 'list'" class="page page-bots">
          <div class="page-head bots-page-head">
            <div>
              <h1 class="page-title">我的机器人</h1>
              <div class="platform-list-meta">
                <p class="page-sub">管理当前开发者主体下关联的 QQ 机器人账号</p>
                <span v-if="!botsLoading" class="limit-hint" :class="{ warn: isMember || createRemain <= 0 }">
                  <span class="dot-g" :class="{ warn: isMember || createRemain <= 0 }"></span>
                  <template v-if="isMember">当前为成员身份，无法创建机器人</template>
                  <template v-else-if="createRemain <= 0">可创建的机器人已达上限</template>
                  <template v-else>还可创建 <b>{{ createRemain }}</b> 个机器人</template>
                </span>
              </div>
            </div>
            <div class="page-actions">
              <details ref="accountMenu" class="platform-account" @keydown.esc="accountMenu.open = false">
                <summary class="btn" aria-label="打开用户菜单">
                  <img v-if="status.userAvatar" :src="status.userAvatar" alt="" />
                  <AppIcon v-else name="user" :size="16" />
                  <span>{{ displaySubjectName(status.subjectName) || '未认证主体' }}</span>
                  <AppIcon name="chevron" :size="13" />
                </summary>
                <div class="platform-account-menu">
                  <div class="platform-account-info">
                    <b>{{ status.userName || 'QQ 账号' }}</b>
                    <small v-if="status.maskedUin">QQ 号 {{ status.maskedUin }}</small>
                    <small v-if="status.subjectEmail">{{ status.subjectEmail }}</small>
                  </div>
                  <button class="btn ghost" type="button" @click="openDeveloperPicker">切换主体</button>
                  <button class="btn ghost" type="button" @click="logoutPlatform">退出登录</button>
                </div>
              </details>
              <button type="button" class="btn primary" :disabled="isMember || createRemain <= 0" @click="openCreate"><AppIcon name="plus" :size="15" /> 创建机器人</button>
            </div>
          </div>
          <div v-if="botsLoading" class="empty-hint">加载中...</div>
          <div v-else class="bots-grid">
            <article v-for="b in bots" :key="b.appid" :class="['bot-card', { 'revoke-card': b.revoking, 'banned-card': b.status === 4 }]" @click="openBotCard(b)">
              <div class="bot-head">
                <div class="bot-avatar" :style="{ background: b.avatar ? 'transparent' : b.color }">
                  <img v-if="b.avatar" :src="b.avatar" alt="" />
                  <span v-else>{{ (b.name || 'B').charAt(0) }}</span>
                </div>
                <div class="bot-info">
                  <div class="bot-name-row">
                    <span class="bot-name">{{ b.name }}</span>
                    <span v-if="[1, 2].includes(b.role)" :class="['bot-role-tag', b.role === 2 ? 'role-member' : 'role-admin']">
                      <AppIcon :name="b.role === 2 ? 'group' : 'safe'" :size="12" class="role-icon" />
                      <span class="role-text">{{ b.role === 2 ? '成员' : '管理员' }}</span>
                    </span>
                  </div>
                  <div class="bot-status">
                    <span class="status-dot" :class="{ offline: !b.online && !b.revoking && b.status !== 4, revoking: b.revoking || b.status === 4 }"></span>
                    <span :class="{ dangerText: b.revoking || b.status === 4 }">{{ botStatusText(b) }}</span>
                    <span v-if="b.online && !b.revoking && b.serviceNames.length" class="bot-service-names">
                      · {{ b.serviceNames.slice(0, 3).join('、') }}{{ b.serviceNames.length > 3 ? '…' : '' }}
                    </span>
                  </div>
                </div>
                <button v-if="!b.revoking && b.status !== 4" type="button" class="bot-action" :aria-label="`管理 ${b.name}`"><AppIcon name="chevron" :size="18" /></button>
              </div>
              <div v-if="b.revoking" class="bot-card-foot">
                <span>将于 <b>{{ formatDeleteDate(b.finalDeleteTime) }}</b> 永久删除</span>
                <button v-if="!isMember" class="btn ghost sm" @click.stop="recoverBot(b)">撤回注销</button>
              </div>
              <div v-else-if="b.status === 1" class="platform-bot-stats">
                <span><AppIcon name="group" :size="13" />累计进群 <b>{{ botCountFormat.format(b.existingGroups) }}</b></span>
                <span><AppIcon name="user" :size="13" />累计用户 <b>{{ botCountFormat.format(b.stockAddedFriends) }}</b></span>
              </div>
            </article>
            <div v-if="!bots.length" class="empty-state platform-empty">
              <img class="platform-empty-hero" :src="`${BOT_LIST_ART}empty-hero-shot.png`" alt="" />
              <div class="empty-copy">
                <h2 class="empty-title">创建你的 QQ 机器人</h2>
                <p class="empty-desc">个性化创建，灵活使用管理</p>
              </div>
              <img class="platform-empty-features" :src="`${BOT_LIST_ART}empty-intro-cards-shot.png`" alt="机器人能力介绍" loading="lazy" />
            </div>
          </div>
        </div>

        <section v-else-if="view === 'create'" class="page platform-create">
          <div class="platform-create-shell">
            <button type="button" class="btn ghost platform-create-back" :disabled="createLoading" @click="cancelCreate"><AppIcon name="back" :size="16" />返回机器人列表</button>
            <header class="platform-create-heading">
              <h1>创建机器人账号</h1>
              <p>填写基本信息，创建后即可连接服务。头像、昵称和简介可随时修改。</p>
            </header>
            <form class="platform-create-form" @submit.prevent="submitCreate">
              <div class="platform-create-avatar">
                <img v-if="createDraft.avatarUrl" :src="createDraft.avatarUrl" alt="机器人头像" />
                <span v-else><AppIcon name="user" :size="32" /></span>
                <div><h2>机器人头像</h2><p>选择官方头像，或上传自定义图片</p><button type="button" class="btn small" :disabled="createLoading" @click="openAvatarEditor">更换头像</button></div>
              </div>
              <label class="platform-create-field">
                <span>机器人昵称 <em>*</em></span>
                <div class="platform-create-input"><input v-model="createDraft.name" required maxlength="20" :disabled="createLoading" placeholder="为机器人起一个名字" /><small>{{ createDraft.name.length }} / 20</small></div>
                <small>昵称将公开展示于 QQ 内。</small>
              </label>
              <label class="platform-create-field">
                <span>机器人简介 <small>（选填）</small></span>
                <div class="platform-create-input is-textarea"><textarea v-model="createDraft.desc" maxlength="100" rows="3" :disabled="createLoading" placeholder="介绍机器人能做什么，例如查询天气或提醒日程"></textarea><small>{{ createDraft.desc.length }} / 100</small></div>
              </label>
              <footer class="platform-create-footer">
                <p>请勿使用侵权、违法或不当信息。</p>
                <div><button type="button" class="btn" :disabled="createLoading" @click="cancelCreate">取消</button><button type="submit" class="btn primary" :disabled="createLoading || !createDraft.name.trim() || !createDraft.avatarId">{{ createLoading ? '创建中…' : '创建机器人' }}</button></div>
              </footer>
            </form>
          </div>
        </section>

        <section v-else-if="view === 'create-connect'" class="page platform-connect">
          <header class="page-head platform-connect-head">
            <div class="page-head-main">
              <h1 class="page-title">设置 AI 服务</h1>
              <div class="platform-connect-bot">
                <img v-if="cur.avatar" :src="cur.avatar" alt="" />
                <span v-else class="platform-connect-avatar"><AppIcon name="robot" :size="20" /></span>
                <div><b>{{ cur.name }}</b><small>AppID {{ cur.appid }}</small></div>
                <button type="button" class="btn ghost small" aria-label="复制 AppID" @click="copyText(cur.appid)"><AppIcon name="copy" :size="15" /></button>
                <span class="manage-status-pill" :data-state="cur.serviceConnected ? 'online' : 'offline'">{{ cur.serviceConnected ? '已连接' : '未连接' }}</span>
              </div>
              <div class="platform-connect-description">
                <p class="page-sub">连接已有 AI 服务后，即可通过机器人使用该服务。自行开发机器人可以跳过这一步。</p>
                <div class="page-actions">
                  <button type="button" class="btn primary outline small" :disabled="checkingCreatedConnection" @click="skipCreatedConnection">稍后连接</button>
                  <button type="button" class="btn primary small" :disabled="checkingCreatedConnection" @click="checkCreatedConnection">{{ checkingCreatedConnection ? '检查中…' : '我已连接' }}</button>
                </div>
              </div>
            </div>
          </header>
          <ServiceGuide :appid="cur.appid" :secret="dev.secretShown ? dev.secret : createdSecret" :show-breadcrumb="false"
            @reset-secret="secretResetDialog = true" @toast="pushToast" />
        </section>

        <!-- 管理页 -->
        <div v-else class="page page-manage">
          <div class="page-head has-back">
            <div class="page-head-main">
              <div class="page-title-row">
                <button class="back-link-inline" type="button" aria-label="返回" @click="backToList"><AppIcon name="back" :size="18" /></button>
                <div class="manage-title-avatar" :style="{ background: cur.avatar ? 'transparent' : cur.color }">
                  <img v-if="cur.avatar" :src="cur.avatar" alt="" /><span v-else>{{ (cur.name || 'B').charAt(0) }}</span>
                </div>
                <h1 class="page-title">{{ cur.name }}</h1><button class="bot-link-copy" type="button" aria-label="复制机器人链接" @click="copyText(botShareTarget)"><AppIcon name="copy" :size="16" /></button>
                <button v-if="bots.length > 1" class="bot-switch-trigger" type="button" aria-label="切换机器人" title="切换机器人" @click="showBotSwitcher = true">
                  <AppIcon name="group" :size="14" />
                  <span>切换</span>
                </button>
                <button v-if="cur.appid && cur.uin" class="connect-profile-qr-trigger bot-manage-qr-trigger" type="button" aria-label="查看添加机器人二维码">
                  <AppIcon name="qr" :size="15" />
                  <span class="connect-profile-qr-pop">
                    <span class="create-friend-qr-img"><QrCodeImage :target="botShareTarget" /></span>
                    <span class="connect-profile-qr-tip">使用 QQ 扫一扫添加机器人</span>
                  </span>
                </button>
                <div class="manage-status-pill" :data-state="cur.revoking ? 'revoking' : cur.status === 4 ? 'banned' : cur.online ? 'online' : 'offline'">
                  <span class="manage-status-dot"></span>
                  <span>{{ botStatusText(cur) }}</span>
                </div>
              </div>
            </div>
            <div class="page-actions platform-notify-anchor">
              <button type="button" class="notify-entry" aria-label="通知中心" :aria-expanded="showNotifications" @click="showNotifications = !showNotifications"><AppIcon name="bell" :size="16" />通知中心</button>
              <template v-if="showNotifications">
                <button type="button" class="platform-notify-dismiss" aria-label="关闭通知中心" @click="showNotifications = false"></button>
                <div class="platform-notify-popover" role="dialog" aria-label="通知中心" @keydown.esc="showNotifications = false">
                  <NotifyCenter :developer-id="status.developerId" :request="v2" @toast="pushToast" />
                </div>
              </template>
            </div>
          </div>

          <div class="manage-layout" :aria-busy="detailLoading">
            <nav class="manage-nav" aria-label="机器人管理栏目">
              <template v-if="detailLoading">
                <span v-for="s in SECS" :key="s.key" class="mn-skeleton" aria-hidden="true"></span>
              </template>
              <template v-else>
                <button v-for="s in SECS" :key="s.key" type="button" class="mn-item"
                  :aria-current="activeSec === s.key || (s.key === 'data' && activeSec === 'data-details') ? 'page' : undefined"
                  :class="{ active: activeSec === s.key || (s.key === 'data' && activeSec === 'data-details') }"
                  @click="selectSec(s.key)">{{ s.label }}</button>
              </template>
            </nav>
            <div class="manage-body">
              <div v-if="detailLoading" class="mn-body-skeleton" role="status" aria-label="加载机器人详情"></div>

              <!-- 账号信息 -->
              <template v-else-if="activeSec === 'account-info'">
                <div class="sec-group">
                  <div class="sec-group-title">基本信息</div>
                  <div class="row" @click="openAvatarEditor"><div class="row-main"><div class="row-label">头像</div></div><div class="row-avatar" :style="{ background: baseInfo.avatar ? 'transparent' : cur.color }"><img v-if="baseInfo.avatar" :src="baseInfo.avatar" alt="" /><span v-else>{{ firstCharacter(baseInfo.name) }}</span></div><AppIcon name="chevron" :size="16" class="row-chev" /></div>
                  <div class="row" @click="openBotEditor('name')"><div class="row-main"><div class="row-label">昵称</div></div><div class="row-value">{{ baseInfo.name }}</div><AppIcon name="chevron" :size="16" class="row-chev" /></div>
                  <div class="row" @click="openBotEditor('desc')"><div class="row-main"><div class="row-label">简介</div></div><div class="row-value" :class="{ 'is-placeholder': !baseInfo.desc }">{{ baseInfo.desc || '点击设置机器人简介' }}</div><AppIcon name="chevron" :size="16" class="row-chev" /></div>
                </div>
                <div class="sec-group">
                  <div class="sec-group-title">管理员信息</div>
                  <div class="row static"><div class="row-main"><div class="row-label">管理员</div><div class="row-sub">该机器人的管理员 QQ 账号，拥有该机器人全部管理权限</div></div><div class="admin-value"><div class="admin-value-name">{{ developerInfo.adminNickName || 'QQ 管理员' }}</div><div class="admin-value-qq">QQ号：{{ developerInfo.adminUin || '—' }}</div></div></div>
                  <div class="row" @click="showAssistantManager = true"><div class="row-main"><div class="row-label">协助者成员</div><div class="row-sub">添加或移除可协助管理该机器人的成员</div></div><div class="avatar-stack"><span v-for="member in assistantMembers.slice(0, 5)" :key="member.uin" class="avatar-stack-item"><img v-if="member.avatar" :src="member.avatar" alt="" /><span v-else>{{ firstCharacter(member.name) }}</span></span></div><AppIcon name="chevron" :size="16" class="row-chev" /></div>
                </div>
              </template>

              <!-- 服务范围 -->
              <template v-else-if="activeSec === 'usage-scope'">
                <div class="sec-group">
                  <div class="row static">
                    <div class="row-main">
                      <div class="row-label">主体认证状态</div>
                      <div v-if="certificationDescription" class="row-sub">{{ certificationDescription }}</div>
                    </div>
                    <div class="row-value ok">{{ developerInfo.subjectType === 1 ? '已认证（个人）' : developerInfo.subjectType === 2 ? '已认证（企业）' : '未认证' }}</div>
                  </div>
                  <div class="row static">
                    <div class="row-main"><div class="row-label">认证主体</div></div>
                    <div class="row-value">{{ displaySubjectName(developerInfo.subjectName) || '未认证' }}{{ developerInfo.subjectType === 1 ? '（个人开发者）' : '' }}</div>
                  </div>
                  <div class="sec-group-title auth-title">
                    <span></span>
                    <span class="auth-intro-trigger" @click="authExpanded = !authExpanded">
                      <span class="auth-intro-trigger-text">查看不同认证状态下的使用说明</span>
                      <AppIcon name="chevron" :size="14" :style="{ transform: authExpanded ? 'rotate(90deg)' : '' }" />
                    </span>
                  </div>
                  <div v-if="authExpanded" class="auth-intro-body">
                    <p>对于不同账号认证状态下，服务范围的限制如下，详细可查看 <a href="https://bot.q.qq.com/wiki/develop/api-v2/" target="_blank" rel="noopener noreferrer">开发者文档</a> 说明。</p>
                    <ul>
                      <li><b>未认证</b>：机器人仅管理员使用，可添加到管理员作为群主的群内。</li>
                      <li><b>个人认证</b>：可设置为公开使用，机器人进群数量添加上限为 500 个。</li>
                      <li><b>企业认证</b>：所有场景均可设置为公开被使用的服务范围。</li>
                    </ul>
                    <p>备注：历史已通过平台审核上架的机器人不受群上限数量影响。</p>
                  </div>
                </div>
                <div class="sec-group scope-card">
                  <div class="sec-group-title friend-title">
                    <span>好友（私聊）</span>
                    <button v-if="isCertified" class="connect-profile-qr-trigger friend-qr-trigger" type="button" aria-label="查看添加好友二维码">
                      <AppIcon name="qr" :size="14" />
                      <span class="connect-profile-qr-pop">
                        <span class="create-friend-qr-img"><QrCodeImage :target="friendShareTarget" /></span>
                        <span class="connect-profile-qr-tip">使用 QQ 扫一扫添加好友</span>
                      </span>
                    </button>
                  </div>
                  <div :class="['row', 'static', { 'scope-row-disabled': !isCertified }]">
                    <div class="row-main">
                      <div class="row-label">允许被其他 QQ 用户添加使用</div>
                      <div class="row-sub">关闭后，仅机器人账号管理员或开发体验成员可添加机器人为好友</div>
                    </div>
                    <div class="switch" :class="{ on: scope.allowFriend, 'is-disabled': !isCertified }" @click="toggleScope('friend')"></div>
                  </div>
                  <div v-if="scope.allowFriend" class="row" @click="openFriendRange"><div class="row-main"><div class="row-label">可添加范围</div></div><div class="row-value">{{ scope.friendRange === 'all' ? '所有人可添加' : '仅 QQ 好友可添加' }}</div><AppIcon name="chevron" :size="16" class="row-chev" /></div>
                </div>
                <div class="sec-group scope-card">
                  <div class="sec-group-title group-title">
                    <span>群聊</span>
                    <button v-if="isCertified" class="connect-profile-qr-trigger group-qr-trigger" type="button" aria-label="查看添加到群聊二维码">
                      <AppIcon name="qr" :size="14" />
                      <span class="connect-profile-qr-pop">
                        <span class="create-friend-qr-img"><QrCodeImage :target="groupShareTarget" /></span>
                        <span class="connect-profile-qr-tip">使用 QQ 扫一扫添加到群聊</span>
                      </span>
                    </button>
                  </div>
                  <div :class="['row', 'static', { 'scope-row-disabled': !isCertified }]">
                    <div class="row-main">
                      <div class="row-label">允许机器人被添加到任意群聊使用（提供公开服务）</div>
                      <div class="row-sub">开启后，所有 QQ 用户可把机器人邀请添加到自己的群内</div>
                    </div>
                    <div class="switch" :class="{ on: scope.allowGroup, 'is-disabled': !isCertified }" @click="toggleScope('group')"></div>
                  </div>
                  <GroupAdminSettings :appid="cur.appid" :all="scope.allGroupSwitches" :whitelist="scope.whitelistGroupSwitches" :groups="scope.adminGroups" :request="v2" @saved="loadScope" @toast="pushToast" @manage-groups="showAdminGroups = true" />
                </div>
                <div class="sec-group scope-card">
                  <div class="sec-group-title channel-title">
                    <span>频道</span>
                    <button v-if="isCertified" class="connect-profile-qr-trigger channel-qr-trigger" type="button" aria-label="查看添加到频道二维码">
                      <AppIcon name="qr" :size="14" />
                      <span class="connect-profile-qr-pop">
                        <span class="create-friend-qr-img"><QrCodeImage :target="channelShareTarget" /></span>
                        <span class="connect-profile-qr-tip">使用 QQ 扫一扫添加到频道</span>
                      </span>
                    </button>
                  </div>
                  <div :class="['row', 'static', { 'scope-row-disabled': !isCertified }]">
                    <div class="row-main">
                      <div class="row-label">允许机器人被添加到任意频道使用（提供公开服务）</div>
                      <div class="row-sub">开启后，任意频道主/管理员可直接把机器人添加到自己的频道内</div>
                    </div>
                    <div class="switch" :class="{ on: scope.allowChannel, 'is-disabled': !isCertified }" @click="toggleScope('channel')"></div>
                  </div>
                </div>
                <div class="sec-group">
                  <div class="sec-group-title">隐私与安全</div>
                  <div class="row static">
                    <div class="row-main"><div class="row-label">允许被搜索到</div><div class="row-sub">打开后，QQ会根据机器人服务范围、服务状态等条件判断是否进入 QQ 网络搜索（综搜）的被搜范围</div></div>
                    <button type="button" :class="['switch', { on: scope.searchable }]" role="switch" :aria-checked="scope.searchable" aria-label="允许被搜索到" @click="toggleSearchable"></button>
                  </div>
                  <div class="row editable-row" @click="openPrivateProtocol">
                    <div class="row-main">
                      <div class="row-label">隐私协议</div>
                      <div class="row-sub">{{ privateProto.effectiveDate ? `已生效，最近一次更新于 ${privateProto.effectiveDate}` : '填写表单信息，平台将自动生成完整协议' }}</div>
                    </div>
                    <div>
                      <span :class="['agreement-tag', privateProto.status ? 'approved' : 'none']">{{ ({ 1: '审核中', 2: '已通过', 3: '审核未通过' })[privateProto.status] || '未提交' }}</span>
                      <AppIcon name="chevron" :size="15" class="row-chev" />
                    </div>
                  </div>
                </div>
              </template>

              <!-- 运营数据 -->
              <OperationsDashboard v-else-if="activeSec === 'data'" :appid="cur.appid" :request="v2" :download="downloadPlatformReport"
                @details="selectSec('data-details')" @error="pushToast" />

              <template v-else-if="activeSec === 'data-details'">
                <div class="data-details-toolbar">
                  <button class="btn small" type="button" @click="selectSec('data')"><AppIcon name="back" :size="14" />返回运营数据</button>
                  <h2>数据明细</h2>
                </div>
                <div class="sec-group report-view-setting">
                  <div class="row static">
                    <div class="row-main">
                      <div class="row-label">是否以折线图显示</div>
                      <div class="row-sub">开启后，运营数据将切换为平滑趋势图</div>
                    </div>
                    <button
                      type="button"
                      :class="['switch', { on: reportChartMode }]"
                      role="switch"
                      :aria-checked="reportChartMode"
                      aria-label="是否以折线图显示"
                      @click="reportChartMode = !reportChartMode"
                    ></button>
                  </div>
                </div>
                <div class="report-stat-cards">
                  <div class="report-stat-card accent">
                    <b>{{ msgSummary.avgDau }}</b>
                    <span>日均 DAU（近 {{ msgSummary.days || 0 }} 天上行消息人数均值）</span>
                  </div>
                  <div class="report-stat-card">
                    <b>{{ msgSummary.totalUp }}</b>
                    <span>总上行消息（近 {{ msgSummary.days || 0 }} 天）</span>
                  </div>
                </div>
                <div v-for="d in DATA_TYPES" :key="d.key" class="sec-group data-report">
                  <div class="report-header">
                    <div class="report-title-wrap">
                      <span class="report-title">{{ d.title }}</span>
                      <span class="report-tip"><AppIcon name="info" :size="12" /></span>
                    </div>
                    <div class="report-actions">
                      <select v-if="d.key === 'msg'" class="report-range-select" :value="reports[d.key].sceneId" @change="changeScene(d, Number($event.target.value))">
                        <option value="1">全部</option>
                        <option value="2">单聊</option>
                        <option value="3">群</option>
                        <option value="4">频道</option>
                      </select>
                      <select class="report-range-select" :value="reports[d.key].range" @change="changeRange(d, Number($event.target.value))">
                        <option v-for="r in RANGES" :key="r.v" :value="r.v">{{ r.label }}</option>
                      </select>
                      <button class="report-download-btn" @click="downloadReport(d)">下载表格</button>
                    </div>
                  </div>
                  <Transition name="report-view" mode="out-in">
                    <div :key="reportChartMode ? `chart-${reports[d.key].version}` : 'table'">
                      <div v-if="reports[d.key].loading" class="report-empty report-visual-state">加载中...</div>
                      <div v-else-if="!reports[d.key].rows.length" class="report-empty report-visual-state">暂无数据</div>
                      <ReportLineChart v-else-if="reportChartMode" :rows="reports[d.key].rows" :columns="REPORT_COLS[d.key]" />
                      <template v-else>
                        <div class="report-table-wrap">
                          <table class="report-table">
                            <thead><tr><th v-for="c in REPORT_COLS[d.key]" :key="c.key">{{ c.label }}</th></tr></thead>
                            <tbody>
                              <tr v-for="(row, i) in reportPageRows(d.key)" :key="i">
                                <td v-for="c in REPORT_COLS[d.key]" :key="c.key">{{ row[c.key] }}</td>
                              </tr>
                            </tbody>
                          </table>
                        </div>
                        <div class="report-footer">
                          <span class="report-count">共 {{ reports[d.key].rows.length }} 项数据</span>
                          <div class="report-pager">
                            <span class="pager-size">10 条/页</span>
                            <button class="pager-btn" type="button" :disabled="reports[d.key].page <= 1" @click="reports[d.key].page--">&lt;</button>
                            <button v-for="page in reportPageCount(d.key)" :key="page" :class="['pager-btn', { active: reports[d.key].page === page }]" type="button" @click="reports[d.key].page = page">{{ page }}</button>
                            <button class="pager-btn" type="button" :disabled="reports[d.key].page >= reportPageCount(d.key)" @click="reports[d.key].page++">&gt;</button>
                          </div>
                        </div>
                      </template>
                    </div>
                  </Transition>
                </div>
              </template>

              <FeedbackPanel v-else-if="activeSec === 'feedback'" :appid="cur.appid" :request="v2" @toast="pushToast" />

              <!-- 高级设置 -->
              <template v-else-if="activeSec === 'advanced'">
                <MenuCommandSettings v-if="activeSub === 'menu-command'" :appid="cur.appid" :request="v2" @back="activeSub = ''" @toast="pushToast" />
                <ServiceGuide v-else-if="activeSub === 'switch-service'" :appid="cur.appid" :secret="dev.secretShown ? dev.secret : ''" @back="activeSub = ''" @reset-secret="secretResetDialog = true" @toast="pushToast" />
                <template v-else>
                  <div class="sec-group"><div class="sec-group-title">连接 AI 服务</div><div class="row" @click="openAIServiceSettings"><div class="row-main"><div class="row-label">设置机器人服务</div><div class="row-sub">可连接到你已有的 AI 服务，通过 QQ 机器人控制 AI “干活”</div></div><div :class="['conn-status', { 'is-online': cur.online }]"><span class="conn-status-dot"></span><span class="conn-status-text">{{ cur.online ? '已连接' : '未连接' }}</span></div><span class="row-value ua-list">{{ cur.online ? `${dev.mode}连接` : '' }}</span><AppIcon name="chevron" :size="16" class="row-chev" /></div></div>
                  <div class="sec-group"><div class="sec-group-title">菜单与指令</div><div class="row" @click="openFunctionConfig"><div class="row-main"><div class="row-label">配置机器人菜单与指令服务</div><div class="row-sub">为好友和群聊设置可视化菜单、快捷指令与跳转服务</div></div><span class="row-value">去配置</span><AppIcon name="chevron" :size="16" class="row-chev" /></div></div>
                  <div class="sec-group"><div class="sec-group-title">功能介绍图</div><div class="row" @click="showFeatureImages = true"><div class="row-main"><div class="row-label">功能介绍图</div><div v-if="!featurePreviewItems.length" class="row-sub">最多支持上传 5 张，展示在机器人的资料页上</div></div><span :class="['fi-status-tag', featurePreviewClass]">{{ featurePreviewText }}</span><AppIcon name="chevron" :size="16" class="row-chev" /></div><div v-if="featurePreviewItems.length" class="feature-images-inline"><div class="fi-inline-list"><div v-for="(item, index) in featurePreviewItems" :key="item.url" class="fi-inline-item"><div class="fi-inline-thumb"><img :src="item.url" :alt="`功能介绍图 ${index + 1}`" @click="featurePreviewUrl = item.url" /><span class="fi-inline-index">{{ index + 1 }}</span></div></div></div></div></div>
                </template>
              </template>

              <!-- 开发设置 -->
              <template v-else-if="activeSec === 'dev-settings'">
                <template v-if="activeSub !== 'event-callback'">
                <div class="sec-group">
                  <div class="sec-group-title">AppID 接入凭证</div>
                  <div class="row static">
                    <div class="row-main">
                      <div class="row-label">AppID</div>
                    </div>
                    <div class="row-value mono">{{ dev.appid }}<button class="guide-copy-btn" type="button" aria-label="复制 AppID" @click="copyText(dev.appid)"><AppIcon name="copy" :size="15" class="guide-copy-icon" /></button></div>
                  </div>
                  <div class="row static">
                    <div class="row-main">
                      <div class="row-label">AppSecret</div>
                      <div class="row-sub">用于服务接入鉴权，请妥善保管。</div>
                    </div>
                    <div class="row-value mono">
                      <span>{{ dev.secretShown ? dev.secret : '****************' }}</span>
                      <button v-if="!isMember" class="guide-copy-btn" type="button" aria-label="重置并查看 AppSecret" @click="secretResetDialog = true"><AppIcon name="eye" :size="15" class="guide-copy-icon" /></button>
                    </div>
                  </div>
                </div>
                <div class="sec-group">
                  <div class="sec-group-title">事件与回调配置</div>
                  <div class="row editable-row" @click="openConnectionSettings">
                    <div class="row-main">
                      <div class="row-label">事件订阅与回调地址</div>
                      <div class="row-sub">配置机器人事件推送与服务端回调地址</div>
                    </div>
                    <div class="row-value link">{{ dev.mode }} <AppIcon name="chevron" :size="15" /></div>
                  </div>
                </div>
                <div class="sec-group">
                  <div class="sec-group-head">
                    <div>
                      <div class="sec-group-title">服务器 IP 白名单</div>
                    </div>
                    <button class="sec-group-action" @click="showAddIp = true"><AppIcon name="plus" :size="14" /> 添加 IP</button>
                  </div>
                  <div class="sec-group-desc">设置白名单后，仅白名单地址允许调通平台 API，支持最多 50 个 IP 地址</div>
                  <div v-if="!dev.ips.length" class="ip-whitelist-empty">
                    <AppIcon name="info" :size="22" />
                    <div class="ip-whitelist-empty-title">尚未添加 IP 白名单</div>
                    <div class="ip-whitelist-empty-sub">未设置时所有请求来源 IP 都会被允许调用 API 接口，建议提供公开服务的机器人使用</div>
                  </div>
                  <ul v-else class="ip-whitelist-list">
                    <li v-for="ip in dev.ips" :key="ip" class="ip-whitelist-item">
                      <span class="mono">{{ ip }}</span>
                      <button class="btn ghost sm" @click="removeIp(ip)">删除</button>
                    </li>
                  </ul>
                </div>
                <div class="sec-group">
                  <div class="sec-group-head">
                    <div>
                      <div class="dev-tester-block-title">开发体验号码设置</div>
                    </div>
                    <div></div>
                  </div>
                  <div class="sec-group-desc">用于内部开发与内邀体验的场景。配置后，不受服务范围的设置影响，测试体验机器人在不同场景的对话能力。</div>
                  <div class="dev-tester-block">
                    <div class="dev-tester-block-head">
                      <div class="dev-tester-label">可添加机器人为好友的开发体验用户 <span class="dev-tester-count">{{ scope.friendInfos.length }} / 20</span></div>
                      <button class="sec-group-action" @click="showAddTester = true"><AppIcon name="plus" :size="14" /> 添加用户</button>
                    </div>
                  <div v-if="!scope.friendInfos.length" class="dev-tester-empty">尚未添加用户</div>
                  <ul v-else class="ip-whitelist-list">
                    <li v-for="tester in scope.friendInfos" :key="tester.uin" class="ip-whitelist-item">
                      <span>{{ tester.name || 'QQ 用户' }} <span class="mono">{{ tester.uin }}</span></span>
                      <button class="btn ghost sm" @click="removeDeveloperTester(tester.uin)">删除</button>
                    </li>
                  </ul>
                  </div>
                </div>
                <div class="sec-group sensitive-alert-config">
                  <div class="sec-group-head">
                    <div class="sec-group-title">敏感权限告警设置</div>
                  </div>
                  <p class="sec-group-desc">该告警用于提醒敏感接口的异常调用。请先 <button class="panel-sub-link" :disabled="dev.helperFriend" @click="addBotHelper">{{ dev.helperFriend ? '已添加QQ机器人开放平台小助手' : '添加QQ机器人开放平台小助手' }}</button><span :class="['assistant-qr-trigger', { 'is-open': showHelperQR }]"><button class="assistant-qr-button" aria-label="查看小助手二维码" :aria-expanded="showHelperQR" @click="showHelperQR = !showHelperQR"><AppIcon name="qr" :size="14" /></button><span class="connect-profile-qr-pop"><span class="create-friend-qr-img"><img :src="HELPER_QR_URL" alt="QQ机器人开放平台小助手二维码" /></span></span></span> 以接收通知消息。如已添加，不用重复操作。</p>
                  <div class="sensitive-alert-grid">
                    <div class="sensitive-alert-column">
                      <div class="dev-tester-block-head"><div class="dev-tester-label">允许接收告警的用户 <span class="dev-tester-count">{{ dev.sensitiveUsers.length }} / 20</span></div><button class="sec-group-action" type="button" @click="openSensitiveDialog('user')"><AppIcon name="plus" :size="14" /> 添加用户</button></div>
                      <div v-if="!dev.sensitiveUsers.length" class="dev-tester-empty">尚未设置</div>
                      <ul v-else class="ip-whitelist-list"><li v-for="user in dev.sensitiveUsers" :key="user.uin" class="ip-whitelist-item"><span>{{ user.name }} <span class="mono">{{ user.uin }}</span></span><button class="btn ghost sm" type="button" @click="removeSensitiveUser(user)">删除</button></li></ul>
                    </div>
                    <div class="sensitive-alert-column">
                      <div class="dev-tester-block-head"><div class="dev-tester-label">允许接收告警的群聊 <span class="dev-tester-count">{{ dev.sensitiveGroups.length }} / 20</span></div><button class="sec-group-action" type="button" @click="openGroupPicker('alert')"><AppIcon name="plus" :size="14" /> 添加群聊</button></div>
                      <div v-if="!dev.sensitiveGroups.length" class="dev-tester-empty">尚未设置</div>
                      <ul v-else class="ip-whitelist-list"><li v-for="group in dev.sensitiveGroups" :key="group.groupCode" class="ip-whitelist-item"><span>{{ group.groupName }} <span class="mono">{{ group.groupCode }}</span></span><button class="btn ghost sm" type="button" @click="removeSensitiveGroup(group)">删除</button></li></ul>
                    </div>
                  </div>
                </div>
                </template>
                <template v-else>
                  <nav class="breadcrumb-bar" aria-label="面包屑导航"><button class="breadcrumb-parent" aria-label="返回开发设置" @click="activeSub = ''"><AppIcon name="back" :size="14" />开发设置</button><span class="breadcrumb-sep">/</span><span class="breadcrumb-current">事件订阅与回调</span></nav>
                  <div class="sec-group">
                    <div class="sec-group-head"><div class="sec-group-title">接入方式 <span class="ec-channel-current">(当前为：{{ dev.mode }})</span></div></div>
                    <p class="sec-group-desc">提示：切换后立刻生效，请谨慎操作，避免影响在线服务。原创机器人开发者请阅读开发者文档：<a class="panel-sub-link" href="https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html" target="_blank" rel="noopener noreferrer">事件订阅与通知</a></p>
                    <div class="ec-channel-cards">
                      <button :class="['ec-channel-card', { active: connectionDraft.mode === 'WebSocket' }]" type="button" :disabled="connectionBusy" @click="connectionDraft.mode = 'WebSocket'"><span class="ec-channel-icon"><AppIcon name="computer" :size="22" /></span><span class="ec-channel-text"><b class="ec-channel-title">WebSocket</b><small class="ec-channel-desc">不依赖公网服务器部署，适合个人单机使用，需维护长连接状态</small></span><span class="ec-channel-check">✓</span></button>
                      <button :class="['ec-channel-card', { active: connectionDraft.mode === 'Webhook' }]" type="button" :disabled="connectionBusy" @click="connectionDraft.mode = 'Webhook'"><span class="ec-channel-icon"><AppIcon name="cloud" :size="22" /></span><span class="ec-channel-text"><b class="ec-channel-title">Webhook</b><small class="ec-channel-desc">事件推送至你配置的 HTTP/HTTPS 服务上，适合提供公开服务的机器人使用，运维更可靠</small></span><span class="ec-channel-check">✓</span></button>
                    </div>
                    <div v-if="connectionDraft.mode === 'WebSocket'" class="ec-tip-block"><AppIcon name="info" :size="14" /><span>如果你是想用于连接 OpenClaw 等类似的 AI Agent 服务，在 AI 服务提供方没有明确说明和指引的前提下，选用 WebSocket 即可。</span></div>
                    <div v-else class="ec-channel-extra">
                      <div class="ec-field-card">
                        <label class="ec-field-label" for="webhook-url">回调地址</label>
                        <div class="ec-field-value-row">
                          <input id="webhook-url" v-model="connectionDraft.webhook" class="ec-field-mono ec-editable" type="url"
                            placeholder="https://your-domain.com/qq/callback" :disabled="connectionDraft.saving || eventsSaving"
                            @input="webhookCheck.msg = ''" />
                          <button class="ec-icon-btn" type="button" aria-label="复制回调地址" :disabled="!connectionDraft.webhook" @click="copyText(connectionDraft.webhook)">
                            <AppIcon name="copy" :size="14" />
                          </button>
                        </div>
                        <div class="ec-field-tip">回调地址需能从公网访问，支持 HTTP、HTTPS 和 IP 地址。QQ 平台会校验返回签名。</div>
                        <div class="webhook-editor-tools">
                          <button v-if="webhookSuggest.available" class="btn ghost sm" type="button" :disabled="connectionBusy" @click="fillSuggestedWebhook">
                            自动填写本机地址
                          </button>
                          <button class="btn ghost sm" type="button" :disabled="connectionBusy" @click="checkWebhookUrl">
                            {{ webhookCheck.checking ? '校验中…' : '校验地址' }}
                          </button>
                        </div>
                        <p v-if="webhookCheck.msg" role="status" :class="['webhook-check-msg', webhookCheck.ok ? 'ok' : 'fail']">{{ webhookCheck.msg }}</p>
                      </div>
                    </div>
                    <div v-if="connectionChanged" class="ec-channel-foot"><button class="btn" type="button" :disabled="connectionBusy" @click="resetConnection">撤销修改</button><button class="btn primary" type="button" :disabled="connectionBusy" @click="saveConnection">{{ connectionDraft.saving ? '保存中…' : '保存' }}</button></div>
                  </div>
                  <div v-if="connectionDraft.mode === 'Webhook'" class="sec-group">
                    <div class="sec-group-head"><div class="sec-group-title">接收事件配置</div></div>
                    <p class="sec-group-desc">选择 Webhook 事件的接收模式：全部接收无需维护事件列表；部分接收可按需勾选，减少不必要的网络开销。</p>
                    <div class="ec-channel-cards"><button v-for="mode in [{ all: true, title: '全部接收', desc: '接收所有事件类型，无需逐个勾选' }, { all: false, title: '部分接收', desc: '仅接收下方勾选的事件类型' }]" :key="String(mode.all)" :class="['ec-channel-card', { active: dev.allSubscriptions === mode.all }]" :disabled="eventsSaving" @click="dev.allSubscriptions = mode.all"><span class="ec-channel-text"><b class="ec-channel-title">{{ mode.title }}</b><small class="ec-channel-desc">{{ mode.desc }}</small></span><span class="ec-channel-check">✓</span></button></div>
                    <template v-if="!dev.allSubscriptions">
                      <div class="sec-group-head ec-events-subhead"><div class="sec-group-title">选择要接收的事件 <span class="ec-events-count">{{ selectedEventCount }} / {{ dev.events.length }}</span></div><div class="ec-events-quick"><button class="ec-quick-btn" type="button" :disabled="eventsSaving" @click="selectAllEvents(true)">全选</button><button class="ec-quick-btn" type="button" :disabled="eventsSaving" @click="selectAllEvents(false)">全部取消</button></div></div>
                      <div v-if="eventGroups.length" class="ec-events-tabs"><section v-for="group in eventGroups" :key="group.name" class="ec-event-section"><div class="ec-event-section-title">{{ group.name }}</div><div class="ec-event-list"><label v-for="event in group.items" :key="event.id" class="ec-event-item"><input v-model="event.selected" type="checkbox" :disabled="eventsSaving" /><span class="ec-event-text"><b class="ec-event-name">{{ event.name }}</b><small class="ec-event-desc">{{ event.description }}</small></span><code class="ec-event-key">{{ event.id }}</code></label></div></section></div>
                      <div v-else class="ec-events-empty">暂无可订阅事件</div>
                    </template>
                    <div v-if="eventsChanged" class="ec-events-foot"><button class="btn" type="button" :disabled="eventsSaving || connectionDraft.saving" @click="resetEvents">撤销修改</button><button class="btn primary" type="button" :disabled="connectionBusy || dev.mode !== 'Webhook'" @click="saveEvents">{{ eventsSaving ? '保存中…' : '保存' }}</button></div>
                  </div>
                </template>
              </template>
            </div>
          </div>
        </div>
      </main>
    </div>

    <FeatureImages v-if="showFeatureImages" :appid="cur.appid" :info="featurePreview" :request="v2" @close="showFeatureImages = false" @saved="loadFeaturePreview" @toast="pushToast" />
    <div v-if="featurePreviewUrl" class="fi-lightbox" @click="featurePreviewUrl = ''"><img :src="featurePreviewUrl" alt="功能介绍图预览" /></div>
    <div v-if="friendRangeDialog.visible" class="modal-root"><div class="modal-mask" @click="!friendRangeDialog.saving && (friendRangeDialog.visible = false)"></div><section class="modal friend-range-modal" role="dialog" aria-modal="true" aria-label="可添加范围"><button class="support-close" aria-label="关闭" @click="friendRangeDialog.visible = false"><AppIcon name="close" :size="16" /></button><div class="am-head"><h3>可添加范围</h3><p>选择允许添加机器人为好友的用户范围</p></div><div class="ai-body"><label v-for="item in [{ value: 'all', title: '所有人可添加' }, { value: 'friend', title: '仅 QQ 好友可添加' }]" :key="item.value" :class="['friend-range-option', { active: friendRangeDialog.value === item.value }]"><input v-model="friendRangeDialog.value" type="radio" :value="item.value" />{{ item.title }}</label></div><div class="am-foot"><button class="btn" :disabled="friendRangeDialog.saving" @click="friendRangeDialog.visible = false">取消</button><button class="btn primary" :disabled="friendRangeDialog.saving" @click="applyFriendRange">保存</button></div></section></div>
    <div v-if="showAdminGroups" class="modal-root"><div class="modal-mask" @click="showAdminGroups = false"></div><section class="modal group-admin-card__modal" role="dialog" aria-modal="true" aria-label="管理白名单群"><div class="group-admin-card__modal-head"><div><h3 class="group-admin-card__modal-title">管理白名单群</h3><p class="group-admin-card__modal-sub">为白名单群聊单独设置机器人管理能力</p></div><button class="group-admin-card__close" aria-label="关闭" @click="showAdminGroups = false"><AppIcon name="close" :size="16" /></button></div><div class="group-admin-card__modal-body"><div class="group-admin-card__whitelist-head"><span>已添加 {{ scope.adminGroups.length }} / 20 个群</span><button class="group-admin-card__add-button" :disabled="scope.adminGroups.length >= 20" @click="openGroupPicker('admin')"><AppIcon name="plus" :size="14" />添加群聊</button></div><div v-if="!scope.adminGroups.length" class="group-admin-card__whitelist-empty">暂无白名单群聊</div><div v-else class="group-admin-card__whitelist-list"><div v-for="group in scope.adminGroups" :key="group.id" class="group-admin-card__whitelist-item"><div class="group-admin-card__whitelist-info"><strong>{{ group.name }}</strong><small>{{ group.id }}</small></div><button class="group-admin-card__remove-button" @click="removeAdminGroup(group)">移除</button></div></div></div><div class="group-admin-card__modal-foot"><button class="btn" @click="showAdminGroups = false">关闭</button></div></section></div>
    <div v-if="showAssistantManager" class="modal-root"><div class="modal-mask" @click="showAssistantManager = false"></div><section class="modal assistant-manager-modal" role="dialog" aria-modal="true" aria-label="协作者成员"><button class="support-close" aria-label="关闭" @click="showAssistantManager = false"><AppIcon name="close" :size="16" /></button><div class="am-head"><h3>协作者成员</h3><p>添加协作者协助管理该机器人；对方确认接受后才正式加入。可分配「开发」「运营」权限。</p></div><div class="am-body"><div v-if="assistantState.loading" class="am-empty">加载中…</div><div v-else-if="assistantState.error" class="am-empty">{{ assistantState.error }}<button class="btn ghost" @click="loadAssistantMembers">重试</button></div><div v-else-if="!assistantMembers.length" class="am-empty">暂无协作者</div><div v-for="member in assistantMembers" :key="member.uin" class="am-card"><img v-if="member.avatar" class="am-avatar" :src="member.avatar" alt="" /><div v-else class="am-avatar-letter">{{ firstCharacter(member.name) }}</div><div class="am-main"><div class="am-name-row"><span class="am-name">{{ member.name }}</span></div><div class="am-qq">QQ号：{{ member.uin }}</div><div class="am-role-row"><span v-for="role in member.roles" :key="role" class="am-role-tag">{{ role === 'dev' ? '开发' : '运营' }}</span></div></div><div v-if="!isMember" class="am-actions"><button class="btn small" @click="openAssistantRoleDialog(member)">修改权限</button><button class="btn small" @click="removeAssistant(member)">移除</button></div></div></div><div class="am-foot"><button class="btn" @click="showAssistantManager = false">关闭</button><button v-if="!isMember" class="btn primary" @click="openAssistantDialog">添加协作者</button></div></section></div>

    <!-- 机器人切换 -->
    <div v-if="showBotSwitcher" class="v2-qr-overlay" @click.self="showBotSwitcher = false">
      <div class="form-modal developer-picker-modal">
        <div class="v2-qr-title">切换机器人</div>
        <div class="v2-qr-desc">选择要管理的机器人，无需返回列表</div>
        <div class="developer-picker-list">
          <button
            v-for="b in bots"
            :key="b.appid"
            type="button"
            :class="['developer-picker-item', { selected: b.appid === cur.appid }]"
            @click="switchBot(b)"
          >
            <span class="developer-picker-mark bot-switch-avatar" :style="{ background: b.avatar ? 'transparent' : b.color }">
              <img v-if="b.avatar" :src="b.avatar" alt="" /><template v-else>{{ (b.name || 'B').charAt(0) }}</template>
            </span>
            <span class="developer-picker-info">
              <strong>{{ b.name }}</strong>
              <small>AppID：{{ b.appid }}</small>
            </span>
            <span class="developer-picker-type">{{ b.appid === cur.appid ? '当前' : botStatusText(b) }}</span>
          </button>
          <div v-if="!bots.length" class="report-empty">暂无机器人</div>
        </div>
        <div class="create-actions">
          <button class="btn ghost" type="button" @click="showBotSwitcher = false">取消</button>
        </div>
      </div>
    </div>

    <!-- 主体切换 -->
    <div v-if="showDeveloperPicker" class="v2-qr-overlay" @click.self="showDeveloperPicker = false">
      <div class="form-modal developer-picker-modal">
        <div class="v2-qr-title">选择主体账号</div>
        <div class="v2-qr-desc">请选择你要登录的开发者主体账号</div>
        <div v-if="developersLoading" class="report-empty">正在加载主体...</div>
        <div v-else class="developer-picker-list">
          <button
            v-for="developer in developers"
            :key="developer.id"
            type="button"
            :class="['developer-picker-item', { selected: selectedSwitchDeveloperId === developer.id }]"
            @click="selectedSwitchDeveloperId = developer.id"
          >
            <span class="developer-picker-mark">{{ developerMark(developer) }}</span>
            <span class="developer-picker-info">
              <strong>{{ displaySubjectName(developer.name) || '未认证主体' }}</strong>
              <small>{{ developer.email || '暂无主体邮箱' }}</small>
            </span>
            <span class="developer-picker-type">{{ subjectTypeText(developer.subjectType) }}</span>
          </button>
          <div v-if="!developers.length" class="report-empty">暂无可切换主体</div>
        </div>
        <div class="create-actions">
          <button class="btn ghost" type="button" @click="showDeveloperPicker = false">取消</button>
          <button
            class="btn primary"
            type="button"
            :disabled="developerSwitching || !selectedSwitchDeveloperId || selectedSwitchDeveloperId === status.developerId"
            @click="switchDeveloper"
          >
            {{ developerSwitching ? '切换中...' : '切换主体' }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="showDiscardCreate" class="modal-root">
      <div class="modal-mask" @click="showDiscardCreate = false"></div>
      <section class="modal form-modal" role="dialog" aria-modal="true" aria-labelledby="discard-create-title">
        <div class="modal-header"><h3 id="discard-create-title">退出创建？</h3><p>已填写的信息尚未保存。</p></div>
        <div class="modal-foot">
          <button type="button" class="btn" @click="showDiscardCreate = false">继续填写</button>
          <button type="button" class="btn primary" @click="closeCreate">退出创建</button>
        </div>
      </section>
    </div>

    <!-- 编辑基础信息 -->
    <div v-if="editor.visible" class="modal-root">
      <div class="modal-mask" @click="!editor.loading && (editor.visible = false)"></div>
      <form class="modal editor-modal" role="dialog" aria-modal="true" :aria-label="`设置机器人${editor.field === 'name' ? '昵称' : '简介'}`" @submit.prevent="submitBotEditor">
        <div class="editor-head"><h3>设置机器人{{ editor.field === 'name' ? '昵称' : '简介' }}</h3><p>{{ editor.field === 'name' ? '昵称将公开展示于 QQ 内，请合理规范使用' : '简介将公开展示于 QQ 内，请简要介绍机器人的功能' }}</p></div>
        <div class="editor-body"><div class="editor-field-wrap"><input v-if="editor.field === 'name'" ref="editorInput" v-model="editor.value" class="editor-input" maxlength="20" placeholder="请输入机器人昵称" /><textarea v-else ref="editorInput" v-model="editor.value" class="editor-input editor-textarea" maxlength="100" rows="4" placeholder="请输入机器人简介"></textarea><div :class="['editor-count', { 'is-limit': editor.value.length >= (editor.field === 'name' ? 20 : 100) }]">{{ editor.value.length }} / {{ editor.field === 'name' ? 20 : 100 }}</div></div></div>
        <div class="modal-foot"><button class="btn large" type="button" :disabled="editor.loading" @click="editor.visible = false">取消</button><button class="btn primary large" type="submit" :disabled="editor.loading || (editor.field === 'name' && !editor.value.trim())">{{ editor.loading ? '保存中…' : '确认' }}</button></div>
      </form>
    </div>

    <!-- 头像选择 -->
    <div v-if="showAvatarPicker" class="modal-root">
      <div class="modal-mask" @click="!avatarSaving && closeAvatarEditor()"></div>
      <section class="modal avatar-picker-modal" role="dialog" aria-modal="true" aria-label="设置机器人头像">
        <button class="apm-close" aria-label="关闭" :disabled="avatarSaving" @click="closeAvatarEditor"><AppIcon name="close" :size="16" /></button>
        <div class="modal-header"><h3>设置机器人头像</h3><p>使用官方推荐或者自定义上传</p></div>
        <div class="avatar-picker-body"><div v-if="officialAvatars.length" class="avatar-picker-grid"><button v-for="avatar in officialAvatars" :key="avatar.id" type="button" :class="['avatar-option', { active: selectedAvatarId === avatar.id }]" @click="selectOfficialAvatar(avatar)"><img :src="avatar.url" alt="" /></button></div><div v-else class="report-empty">暂无可用头像</div><div v-if="customAvatarUrl" class="custom-avatar-preview"><img :src="customAvatarUrl" alt="自定义头像预览" /><span>自定义头像</span></div></div>
        <input ref="avatarFileInput" class="avatar-file-input" type="file" accept="image/*" @change="selectCustomAvatar" />
        <div class="modal-foot"><button class="btn large" type="button" :disabled="avatarSaving" @click="avatarFileInput?.click()">上传自定义头像</button><button class="btn primary large" type="button" :disabled="avatarSaving || (!selectedAvatarId && !customAvatarFile)" @click="submitAvatar">{{ avatarSaving ? '保存中…' : '确定' }}</button></div>
      </section>
    </div>

    <!-- 自定义头像裁剪 -->
    <div v-if="cropper.visible" class="v2-qr-overlay cropper-overlay">
      <div class="form-modal avatar-cropper-modal" role="dialog" aria-modal="true" aria-label="编辑头像">
        <div class="v2-qr-title">编辑头像</div>
        <div class="v2-qr-desc">拖动图片调整位置，使用滑杆缩放头像</div>
        <div class="avatar-cropper-body">
          <div class="avatar-cropper-stage" @pointerdown="startCropDrag">
            <img :src="cropper.source" :style="cropImageStyle" alt="头像裁剪预览" draggable="false" />
            <span class="avatar-cropper-grid horizontal one"></span>
            <span class="avatar-cropper-grid horizontal two"></span>
            <span class="avatar-cropper-grid vertical one"></span>
            <span class="avatar-cropper-grid vertical two"></span>
          </div>
          <div class="avatar-cropper-preview">
            <span>预览</span>
            <div>
              <img :src="cropper.source" :style="cropPreviewStyle" alt="" draggable="false" />
            </div>
          </div>
        </div>
        <label class="avatar-cropper-zoom">
          <span>缩放</span>
          <input v-model.number="cropper.zoom" type="range" min="1" max="3" step="0.01" @input="clampCropOffset" />
          <button class="btn ghost sm" type="button" @click="resetCropper">重置</button>
        </label>
        <div class="create-actions">
          <button class="btn ghost" type="button" @click="closeCropper">取消</button>
          <button class="btn primary" type="button" @click="confirmCropper">确定</button>
        </div>
      </div>
    </div>

    <!-- 隐私协议 -->
    <div v-if="privacyDialog.visible" class="v2-qr-overlay" @click.self="privacyDialog.visible = false">
      <div class="form-modal privacy-modal">
        <div class="v2-qr-title">机器人隐私保护协议</div>
        <div v-if="!privacyDialog.editing" class="privacy-empty">
          <div class="privacy-empty-icon"><AppIcon name="safe" :size="28" /></div>
          <b>暂未生成隐私保护协议</b>
          <p>填写联系信息后，平台将根据机器人和开发者主体信息自动生成完整协议。</p>
          <button class="btn primary" type="button" @click="privacyDialog.editing = true">填写协议信息</button>
        </div>
        <template v-else>
          <label class="create-field">
            <span>联系邮箱</span>
            <input v-model="privacyDialog.email" type="email" placeholder="请输入有效联系邮箱" />
          </label>
          <div class="privacy-preview">
            <b>协议预览</b>
            <p>本机器人由 {{ displaySubjectName(developerInfo.subjectName) || '当前开发者主体' }} 提供服务。用户可通过上述邮箱联系开发者，申请查询、更正或删除与机器人服务相关的信息。</p>
          </div>
          <div class="create-actions">
            <button class="btn ghost" type="button" @click="privacyDialog.visible = false">取消</button>
            <button class="btn primary" type="button" :disabled="privacyDialog.loading || !privacyDialog.email.trim()" @click="updatePrivateProtocol">{{ privacyDialog.loading ? '提交中...' : '提交协议' }}</button>
          </div>
        </template>
      </div>
    </div>

    <!-- 添加 IP -->
    <div v-if="showAddIp" class="v2-qr-overlay" @click.self="showAddIp = false">
      <div class="form-modal compact-modal">
        <div class="v2-qr-title">添加 IP 白名单</div>
        <div class="v2-qr-desc">仅支持单个 IPv4；不支持 CIDR 网段。</div>
        <label class="create-field"><span>IPv4</span><input v-model="ipInput" placeholder="请输入 IPv4" @keyup.enter="addIp" /></label>
        <div class="create-actions"><button class="btn ghost" type="button" @click="showAddIp = false">取消</button><button class="btn primary" type="button" :disabled="!ipInput.trim()" @click="addIp">添加</button></div>
      </div>
    </div>

    <!-- 添加体验用户 -->
    <div v-if="showAddTester" class="v2-qr-overlay" @click.self="showAddTester = false">
      <div class="form-modal compact-modal">
        <div class="v2-qr-title">添加用户</div>
        <div class="v2-qr-desc">请输入用户的 QQ 号</div>
        <label class="create-field"><span>QQ 号</span><input v-model="testerInput" inputmode="numeric" maxlength="11" placeholder="请输入 QQ 号" @keyup.enter="addDeveloperTester" /></label>
        <div class="create-actions"><button class="btn ghost" type="button" @click="showAddTester = false">取消</button><button class="btn primary" type="button" :disabled="!/^\d{5,11}$/.test(testerInput.trim())" @click="addDeveloperTester">保存</button></div>
      </div>
    </div>

    <!-- 协助者成员 -->
    <div v-if="assistantDialog.visible" class="modal-root"><div class="modal-mask" @click="!assistantDialog.saving && (assistantDialog.visible = false)"></div><section class="modal assistant-invite-modal" role="dialog" aria-modal="true" aria-label="添加协作者"><button class="support-close" aria-label="关闭" :disabled="assistantDialog.saving" @click="assistantDialog.visible = false"><AppIcon name="close" :size="16" /></button><div class="ai-head"><h3>添加协作者</h3><p>添加协作者协助管理该机器人；可分配「开发」「运营」权限。</p></div><div class="ai-body"><label class="ai-field"><span class="ai-label">协作者 QQ 号</span><input v-model="assistantDialog.uin" class="ai-input" inputmode="numeric" maxlength="12" placeholder="请输入对方 QQ 号" /></label><label class="ai-field"><span class="ai-label">备注名称（选填）</span><input v-model="assistantDialog.note" class="ai-input" maxlength="20" placeholder="便于识别的备注名" /></label><div class="ai-field"><div class="ai-label">权限</div><div class="ai-role-grid"><label v-for="role in [{ key: 'dev', title: '开发', sub: '接入、调试与开发配置' }, { key: 'ops', title: '运营', sub: '数据管理与运营配置' }]" :key="role.key" :class="['ai-role-card', { 'is-checked': assistantDialog.roles.includes(role.key) }]"><input v-model="assistantDialog.roles" class="ai-role-checkbox" type="checkbox" :value="role.key" /><span class="ai-role-main"><span class="ai-role-title">{{ role.title }}</span><span class="ai-role-desc">{{ role.sub }}</span></span></label></div><p class="ai-hint">至少选择一项权限。</p></div></div><div class="ai-foot"><button class="btn" :disabled="assistantDialog.saving" @click="assistantDialog.visible = false">取消</button><button class="btn primary" :disabled="assistantDialog.saving || !/^\d{5,11}$/.test(assistantDialog.uin.trim()) || !assistantDialog.roles.length" @click="addAssistant">{{ assistantDialog.saving ? '添加中…' : '确认添加' }}</button></div></section></div>

    <div v-if="assistantRoleDialog.visible" class="v2-qr-overlay" @click.self="assistantRoleDialog.visible = false">
      <div class="form-modal compact-modal">
        <div class="v2-qr-title">编辑协助权限</div>
        <div class="v2-qr-desc">{{ assistantRoleDialog.member?.name }}（{{ assistantRoleDialog.member?.uin }}）</div>
        <div class="assistant-role-options"><label v-for="role in ASSISTANT_ROLE_OPTIONS" :key="role.key"><input v-model="assistantRoleDialog.roles" type="checkbox" :value="role.key" />{{ role.label }}</label></div>
        <div class="create-actions"><button class="btn ghost" type="button" @click="assistantRoleDialog.visible = false">取消</button><button class="btn primary" type="button" :disabled="assistantRoleDialog.saving || !assistantRoleDialog.roles.length" @click="saveAssistantRoles">{{ assistantRoleDialog.saving ? '保存中...' : '保存权限' }}</button></div>
      </div>
    </div>

    <!-- 群选择器 -->
    <div v-if="groupPicker.visible" class="v2-qr-overlay" @click.self="groupPicker.visible = false">
      <div class="form-modal developer-picker-modal">
        <div class="v2-qr-title">{{ groupPicker.purpose === 'admin' ? '选择白名单群' : '选择提醒群' }}</div>
        <div class="v2-qr-desc">{{ groupPicker.purpose === 'admin' ? '只显示机器人已加入的群聊' : '选择接收敏感权限提醒的群聊' }}</div>
        <div v-if="groupPicker.loading" class="report-empty">正在加载群聊...</div>
        <div v-else class="developer-picker-list">
          <button v-for="group in groupPicker.groups" :key="group.id" type="button" class="developer-picker-item" :disabled="groupPicker.saving || groupAlreadySelected(group)" @click="selectBotGroup(group)">
            <span class="developer-picker-mark">{{ firstCharacter(group.name) || '群' }}</span>
            <span class="developer-picker-info"><strong>{{ group.name }}</strong><small>群号：{{ group.id }}</small></span>
            <span class="developer-picker-type">{{ groupAlreadySelected(group) ? '已添加' : '选择' }}</span>
          </button>
          <div v-if="!groupPicker.groups.length" class="report-empty">暂无可选群聊</div>
        </div>
        <div class="create-actions"><button class="btn ghost" type="button" @click="groupPicker.visible = false">关闭</button></div>
      </div>
    </div>

    <!-- 敏感提醒用户 -->
    <div v-if="sensitiveDialog.visible" class="v2-qr-overlay" @click.self="sensitiveDialog.visible = false">
      <div class="form-modal compact-modal">
        <div class="v2-qr-title">添加提醒用户</div>
        <div class="v2-qr-desc">请输入接收敏感权限提醒的 QQ 号</div>
        <label class="create-field"><span>QQ 号</span><input v-model="sensitiveDialog.value" inputmode="numeric" maxlength="11" placeholder="请输入 QQ 号" /></label>
        <div class="create-actions"><button class="btn ghost" type="button" @click="sensitiveDialog.visible = false">取消</button><button class="btn primary" type="button" :disabled="sensitiveDialog.saving || !/^\d{5,11}$/.test(sensitiveDialog.value.trim())" @click="saveSensitiveDialog">{{ sensitiveDialog.saving ? '保存中...' : '保存' }}</button></div>
      </div>
    </div>

    <!-- 重置 AppSecret -->
    <div v-if="secretResetDialog && !isMember" class="v2-qr-overlay" @click.self="secretResetDialog = false">
      <div class="form-modal compact-modal">
        <div class="v2-qr-title">重置 AppSecret</div>
        <div class="delete-warning">重置后旧的 AppSecret 立即失效。请及时更新已部署服务中的接入凭证。</div>
        <div class="create-actions"><button class="btn ghost" type="button" @click="secretResetDialog = false">取消</button><button class="btn danger" type="button" @click="resetSecret">重置并查看</button></div>
      </div>
    </div>

    <!-- 开放平台登录弹窗 -->
    <OpenAPILoginDialog
      v-if="showLogin"
      :title="mobileLogin ? '在手机 QQ 中授权' : scanStatus === 'selecting' ? '选择登录主体' : '扫码登录 QQ 开放平台'"
      :description="mobileLogin ? '在手机 QQ 中打开链接并确认授权。若当前使用电脑，可复制链接到手机打开。' : scanStatus === 'selecting' ? '请选择你要登录的开发者主体帐号' : '登录会话仅用于连接 QQ 机器人开放平台管理面板'"
      :steps="scanSteps"
      :show-qr="!mobileLogin && scanStatus !== 'selecting'"
      :qr-image="qrImg"
      :preparing="scanStatus === 'preparing'"
      :status-text="scanText"
      :status-tone="scanStatus === 'logged_in' ? 'success' : scanEnded ? 'error' : 'waiting'"
      :open-url="activeMobileUrl"
      open-label="打开手机 QQ"
      :actions="scanActions"
      @action="handleScanAction"
      @close="closeScan"
    >
      <template #body>
        <div v-if="scanStatus === 'selecting'" class="v2-entity-list">
          <label v-for="developer in scanDevelopers" :key="developer.id" :class="['v2-entity-item', { selected: selectedDeveloperId === developer.id }]">
            <input v-model="selectedDeveloperId" type="radio" :value="developer.id" />
            <span class="v2-entity-info">
              <strong>{{ displaySubjectName(developer.name) || '未认证主体' }}</strong>
              <small>{{ subjectTypeText(developer.subject_type) }}{{ developer.email ? ` · ${developer.email}` : '' }}</small>
            </span>
          </label>
        </div>
      </template>
    </OpenAPILoginDialog>

    <transition name="toast-fade"><div v-if="toast" class="qq-toast">{{ toast }}</div></transition>
  </div>
</template>

<style scoped>
.data-details-toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.data-details-toolbar h2 { margin: 0; font-size: 17px; }
.v2-gate { max-width: 820px; margin: 36px auto 0; padding: 38px; border: 1px solid var(--line); border-radius: 26px; background: var(--bg-elev); box-shadow: var(--shadow); }
.v2-gate-hero { display: flex; align-items: center; gap: 18px; }
.v2-gate-icon { width: 62px; height: 62px; flex: none; display: grid; place-items: center; border-radius: 18px; background: linear-gradient(145deg, var(--accent), var(--accent-light)); color: #fff; box-shadow: 0 10px 24px color-mix(in srgb, var(--accent) 24%, transparent); }
.v2-gate .page-title { margin: 0; font-size: 27px; }
.v2-gate .page-sub { margin: 7px 0 0; color: var(--ink-3); font-size: 13.5px; line-height: 1.65; }
.v2-login-overview { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 22px; }
.v2-login-step { display: flex; gap: 10px; padding: 15px; border: 1px solid var(--line); border-radius: 14px; background: var(--bg-sunken); }
.v2-login-step-num { width: 24px; height: 24px; flex: none; display: grid; place-items: center; border-radius: 50%; background: var(--accent-soft); color: var(--accent); font-size: 12px; font-weight: 800; }
.v2-login-step div { display: grid; gap: 4px; }
.v2-login-step b { color: var(--ink-2); font-size: 12.5px; }
.v2-login-step div span { color: var(--ink-4); font-size: 11.5px; line-height: 1.45; }
.v2-gate-actions { display: flex; align-items: center; gap: 15px; margin-top: 24px; }
.v2-gate-security { color: var(--ink-4); font-size: 12px; }
.v2-qr-overlay { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.45); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.v2-qr-title { font-size: 17px; font-weight: 700; color: var(--ink); }
.v2-qr-desc { font-size: 13px; color: var(--ink-3); margin: 6px 0 16px; }
.v2-entity-list { max-height: 280px; overflow-y: auto; display: grid; gap: 8px; text-align: left; }
.v2-entity-item { display: flex; align-items: center; gap: 10px; padding: 12px; border: 1px solid var(--line-strong); border-radius: 10px; cursor: pointer; }
.v2-entity-item.selected { border-color: var(--accent); background: var(--accent-soft); }
.v2-entity-info { min-width: 0; display: grid; gap: 3px; }
.v2-entity-info strong { color: var(--ink); font-size: 14px; }
.v2-entity-info small { color: var(--ink-3); font-size: 12px; overflow-wrap: anywhere; }
.qqdash .empty-hint { color: var(--ink-3); font-size: 14px; padding: 40px 0; text-align: center; }
.qqdash .icon-btn { border: none; background: none; cursor: pointer; color: var(--ink-4); padding: 0 4px; display: inline-flex; }
.qqdash .icon-btn:hover { color: var(--accent); }
.qqdash .btn.sm { padding: 4px 10px; font-size: 12px; }
.qqdash .row-value.mono, .qqdash .mono { font-family: var(--font-mono); display: inline-flex; align-items: center; gap: 8px; }
.qqdash .row-value.is-placeholder { color: var(--ink-4); }
.qqdash .range-select { display: inline-flex; background: var(--bg-sunken); border-radius: 10px; padding: 3px; gap: 2px; }
.qqdash .range-opt { border: none; background: none; padding: 5px 12px; border-radius: 8px; font-size: 12px; cursor: pointer; color: var(--ink-3); }
.qqdash .range-opt.active { background: var(--bg-elev); color: var(--ink); box-shadow: var(--shadow-sm); }
.qqdash .report-actions { display: flex; align-items: center; gap: 10px; }
.qqdash .report-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.qqdash .report-table-wrap { overflow-x: auto; }
.qqdash .report-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.qqdash .report-table th { text-align: left; color: var(--ink-3); font-weight: 600; padding: 10px 8px; border-bottom: 1px solid var(--line); white-space: nowrap; }
.qqdash .report-table td { padding: 12px 8px; border-bottom: 1px solid var(--line); color: var(--ink-2); white-space: nowrap; }
.qqdash .report-empty { text-align: center; color: var(--ink-4); padding: 24px 0 !important; }
.qqdash .report-view-setting { overflow: hidden; }
.qqdash .report-view-setting .row { border-bottom: 0; }
.qqdash .report-view-setting .switch { border: 0; }
.qqdash .report-view-setting .switch:focus-visible { outline: 3px solid var(--accent-soft); outline-offset: 3px; }
.qqdash .report-visual-state { min-height: 276px; display: grid; place-items: center; }
.report-view-enter-active, .report-view-leave-active { transition: opacity .2s ease, transform .28s cubic-bezier(.22, .8, .32, 1); }
.report-view-enter-from { opacity: 0; transform: translateY(8px); }
.report-view-leave-to { opacity: 0; transform: translateY(-4px); }
.qqdash .editable-row { cursor: pointer; }
.qqdash .editable-row:hover { background: var(--bg-sunken); }
.qqdash .dangerText { color: var(--danger); font-weight: 600; }
.qqdash .revoke-card { cursor: default; }
.qqdash .bot-card-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; border-top: 1px solid var(--line); color: var(--ink-3); font-size: 12px; }
.qqdash .empty-state { grid-column: 1 / -1; min-height: 320px; display: grid; justify-items: center; align-content: center; text-align: center; overflow: hidden; }
.qqdash .empty-title { margin: 0; color: var(--ink); font-size: 22px; font-weight: 720; }
.qqdash .empty-desc { margin: 7px 0 0; color: var(--ink-4); font-size: 13px; }
.qqdash .connection-actions { display: flex; align-items: center; gap: 8px; }
.qqdash .event-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; padding: 12px 16px; }
.qqdash .event-item { display: flex; align-items: flex-start; gap: 9px; padding: 10px; border: 1px solid var(--line); border-radius: 8px; cursor: pointer; }
.qqdash .event-item span { display: grid; gap: 3px; }
.qqdash .event-item strong { color: var(--ink-2); font-size: 13px; }
.qqdash .event-item small { color: var(--ink-4); font-size: 11px; line-height: 1.45; }
.qqdash .event-actions { display: flex; justify-content: flex-end; padding: 0 16px 16px; }
.create-modal { width: min(520px, calc(100vw - 32px)); padding: 26px; border-radius: 14px; background: var(--bg-elev); box-shadow: var(--shadow-lg); }
.create-field { display: grid; gap: 8px; margin-top: 18px; color: var(--ink-2); font-size: 13px; font-weight: 600; }
.create-field input, .create-field textarea { width: 100%; padding: 10px 12px; border: 1px solid var(--line-strong); border-radius: 8px; background: var(--bg-elev); color: var(--ink); font: inherit; font-weight: 400; resize: vertical; outline: none; }
.create-field input:focus, .create-field textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.avatar-options { display: flex; gap: 10px; overflow-x: auto; padding: 3px; }
.avatar-option { width: 54px; height: 54px; flex: none; padding: 2px; border: 2px solid transparent; border-radius: 50%; background: transparent; cursor: pointer; }
.avatar-option.active { border-color: var(--accent); }
.avatar-option img { width: 100%; height: 100%; object-fit: cover; border-radius: 50%; }
.create-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 22px; }
.form-modal { width: min(520px, calc(100vw - 32px)); padding: 28px; border-radius: 18px; background: var(--bg-elev); box-shadow: var(--shadow-lg); }
.compact-modal { width: min(420px, calc(100vw - 32px)); }
.form-modal .create-field small { color: var(--ink-4); font-size: 11px; font-weight: 400; text-align: right; }
.avatar-picker-grid { display: grid; grid-template-columns: repeat(auto-fill, 62px); gap: 12px; justify-content: center; margin: 22px 0 8px; max-height: 310px; overflow-y: auto; }
.avatar-file-input { display: none; }
.avatar-upload-row { display: flex; align-items: center; gap: 12px; margin-top: 18px; }
.avatar-upload-row small { color: var(--ink-4); font-size: 11.5px; }
.custom-avatar-preview { display: flex; align-items: center; gap: 10px; margin-top: 18px; color: var(--ink-3); font-size: 12px; }
.custom-avatar-preview img { width: 54px; height: 54px; border: 2px solid var(--accent); border-radius: 50%; object-fit: cover; }
.privacy-empty { display: grid; justify-items: center; gap: 10px; padding: 34px 20px 16px; text-align: center; }
.privacy-empty-icon { width: 60px; height: 60px; display: grid; place-items: center; border-radius: 18px; background: var(--accent-soft); color: var(--accent); }
.privacy-empty b { color: var(--ink); font-size: 15px; }
.privacy-empty p { max-width: 360px; margin: 0 0 8px; color: var(--ink-4); font-size: 12.5px; line-height: 1.65; }
.privacy-preview { margin-top: 18px; padding: 16px; border: 1px solid var(--line); border-radius: 12px; background: var(--bg-sunken); }
.privacy-preview b { color: var(--ink-2); font-size: 12.5px; }
.privacy-preview p { margin: 7px 0 0; color: var(--ink-4); font-size: 12px; line-height: 1.7; }
.delete-warning { margin-top: 18px; padding: 13px 14px; border: 1px solid rgba(255, 59, 48, .18); border-radius: 10px; background: rgba(255, 59, 48, .06); color: var(--danger); font-size: 12.5px; line-height: 1.6; }
.delete-bot-card { display: flex; align-items: center; gap: 12px; margin-top: 18px; padding: 14px; border: 1px solid var(--line); border-radius: 12px; }
.delete-bot-card .bot-avatar { width: 46px; height: 46px; }
.delete-bot-card>div:last-child { display: grid; gap: 4px; }
.delete-bot-card b { color: var(--ink); font-size: 14px; }
.delete-bot-card span { color: var(--ink-4); font-family: var(--font-mono); font-size: 11.5px; }
.panel-breadcrumb { display: inline-flex; align-items: center; gap: 7px; width: max-content; padding: 4px 2px; border: 0; background: transparent; color: var(--ink-3); font: inherit; font-size: 13px; cursor: pointer; }
.panel-breadcrumb span { color: var(--ink-4); }
.event-config { padding: 20px !important; }
.event-config-title { color: var(--ink); font-size: 15px; font-weight: 700; }
.event-config-title span { color: var(--ink-4); font-size: 12px; font-weight: 500; }
.event-config .sec-group-desc { padding: 0; margin: 8px 0 16px; }
.event-config .sec-group-desc a { color: var(--accent); text-decoration: none; }
.event-channel-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.event-channel-card { position: relative; display: flex; align-items: flex-start; gap: 12px; padding: 17px; border: 1px solid var(--line-strong); border-radius: 14px; background: var(--bg-elev); color: var(--ink-3); text-align: left; cursor: pointer; }
.event-channel-card>span { display: grid; gap: 5px; }
.event-channel-card b { color: var(--ink); font-size: 13.5px; }
.event-channel-card small { color: var(--ink-4); font-size: 11.5px; line-height: 1.55; }
.event-channel-card i { position: absolute; top: 10px; right: 11px; display: none; color: var(--accent); font-style: normal; }
.event-channel-card.active { border-color: var(--accent); background: var(--accent-soft); box-shadow: 0 4px 16px color-mix(in srgb, var(--accent) 10%, transparent); }
.event-channel-card.active i { display: block; }
.event-note { display: flex; align-items: flex-start; gap: 7px; margin-top: 14px; padding: 12px; border-radius: 10px; background: var(--accent-soft); color: var(--ink-2); font-size: 11.5px; line-height: 1.55; }
.event-webhook-field { display: grid; gap: 8px; margin-top: 16px; color: var(--ink-2); font-size: 12.5px; font-weight: 650; }
.event-webhook-field input { padding: 11px 12px; border: 1px solid var(--line-strong); border-radius: 9px; background: var(--bg-elev); color: var(--ink); font: inherit; font-weight: 400; outline: none; }
.event-webhook-field input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.event-webhook-field small { color: var(--ink-4); font-size: 11px; font-weight: 400; line-height: 1.5; }
.event-save-mode { display: flex; justify-content: flex-end; margin-top: 18px; }
.event-config-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.event-quick-btn { margin-left: 7px; padding: 3px 5px; border: 0; background: transparent; color: var(--accent); font: inherit; font-size: 12px; cursor: pointer; }
.event-groups { display: grid; gap: 18px; }
.event-groups section { display: grid; gap: 7px; }
.event-groups h4 { margin: 0; color: var(--ink-4); font-size: 11px; letter-spacing: .04em; }
.event-groups .event-item { display: grid; grid-template-columns: 18px minmax(0, 1fr) minmax(120px, auto); align-items: center; gap: 9px; padding: 11px 12px; }
.event-groups .event-item span { display: grid; gap: 3px; }
.event-groups .event-item b { color: var(--ink-2); font-size: 12.5px; }
.event-groups .event-item small { color: var(--ink-4); font-size: 11px; line-height: 1.45; }
.event-groups .event-item code { color: var(--ink-4); font-size: 10.5px; text-align: right; overflow-wrap: anywhere; }
.connect-profile-qr-pop .create-friend-qr-img img { width: 100%; height: 100%; object-fit: contain; }
.qqdash .bot-switch-trigger { display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border: 1px solid var(--line-strong); border-radius: 999px; background: var(--bg-elev); color: var(--ink-2); font-size: 12px; font-weight: 600; cursor: pointer; transition: all .15s; flex: none; }
.qqdash .bot-switch-trigger:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }
.qqdash .bot-switch-avatar { overflow: hidden; color: #fff; }
.qqdash .bot-switch-avatar img { width: 100%; height: 100%; object-fit: cover; border-radius: inherit; }


.qqdash .page-actions { display: flex; align-items: center; gap: 10px; }
.qqdash .current-subject-card { display: inline-flex; align-items: center; gap: 9px; min-width: 230px; max-width: 360px; margin-top: 13px; padding: 8px 10px 8px 8px; border: 1px solid var(--accent-border); border-radius: 12px; background: var(--accent-soft); }
.qqdash .current-subject-mark, .developer-picker-mark { width: 30px; height: 30px; display: grid; flex: none; place-items: center; border-radius: 50%; background: var(--bg-elev); color: var(--accent); font-size: 13px; font-weight: 750; box-shadow: 0 2px 8px color-mix(in srgb, var(--accent) 12%, transparent); }
.qqdash .current-subject-text { min-width: 0; display: grid; flex: 1; gap: 2px; text-align: left; }
.qqdash .current-subject-text strong, .developer-picker-info strong { overflow: hidden; color: var(--ink); font-size: 12px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.qqdash .current-subject-text small, .developer-picker-info small { overflow: hidden; color: var(--ink-4); font-size: 10.5px; text-overflow: ellipsis; white-space: nowrap; }
.qqdash .current-subject-tag, .developer-picker-type { flex: none; padding: 3px 7px; border-radius: 999px; background: color-mix(in srgb, var(--accent) 14%, transparent); color: var(--accent); font-size: 10px; font-weight: 700; }
.qqdash .current-subject-card[data-auth-type="enterprise"] .current-subject-tag { background: rgba(41, 183, 128, .12); color: #149665; }
.developer-picker-modal { width: min(460px, calc(100vw - 32px)); }
.developer-picker-list { display: grid; gap: 8px; max-height: min(430px, 55vh); margin-top: 20px; overflow-y: auto; }
.developer-picker-item { width: 100%; display: flex; align-items: center; gap: 11px; padding: 11px; border: 1px solid var(--line); border-radius: 12px; background: var(--bg-elev); font: inherit; text-align: left; cursor: pointer; transition: border-color .18s, background .18s, box-shadow .18s; }
.developer-picker-item:hover { border-color: var(--accent-border); background: var(--accent-soft); }
.developer-picker-item.selected { border-color: var(--accent); background: var(--accent-soft); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 8%, transparent); }
.developer-picker-info { min-width: 0; display: grid; flex: 1; gap: 3px; }
.developer-picker-mark { border: 1px solid var(--line); background: var(--bg-sunken); box-shadow: none; }
.cropper-overlay { z-index: 1001; }
.avatar-cropper-modal { width: min(560px, calc(100vw - 32px)); }
.avatar-cropper-body { display: flex; align-items: center; justify-content: center; gap: 34px; margin-top: 22px; }
.avatar-cropper-stage { position: relative; width: 320px; height: 320px; flex: none; overflow: hidden; border-radius: 16px; background: #20242b; cursor: grab; touch-action: none; user-select: none; }
.avatar-cropper-stage:active { cursor: grabbing; }
.avatar-cropper-stage img, .avatar-cropper-preview img { position: absolute; max-width: none; pointer-events: none; user-select: none; }
.avatar-cropper-stage::after { position: absolute; inset: 0; border: 2px solid rgba(255, 255, 255, .9); border-radius: inherit; box-shadow: inset 0 0 0 999px rgba(0, 0, 0, .08); content: ""; pointer-events: none; }
.avatar-cropper-grid { position: absolute; z-index: 1; background: rgba(255, 255, 255, .35); pointer-events: none; }
.avatar-cropper-grid.horizontal { right: 0; left: 0; height: 1px; }
.avatar-cropper-grid.vertical { top: 0; bottom: 0; width: 1px; }
.avatar-cropper-grid.horizontal.one { top: 33.333%; }
.avatar-cropper-grid.horizontal.two { top: 66.666%; }
.avatar-cropper-grid.vertical.one { left: 33.333%; }
.avatar-cropper-grid.vertical.two { left: 66.666%; }
.avatar-cropper-preview { display: grid; justify-items: center; gap: 9px; color: var(--ink-4); font-size: 11px; }
.avatar-cropper-preview>div { position: relative; width: 96px; height: 96px; overflow: hidden; border: 1px solid var(--line-strong); border-radius: 50%; background: #20242b; }
.avatar-cropper-zoom { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 12px; margin-top: 20px; color: var(--ink-3); font-size: 12px; }
.avatar-cropper-zoom input { width: 100%; accent-color: var(--accent); }
@media (max-width: 720px) {
  .qqdash .page { padding: 24px 16px 40px; }
  .qqdash .page-head { align-items: flex-start; flex-direction: column; }
  .qqdash .page-actions { width: 100%; }
  .qqdash .page-actions .btn { flex: 1; justify-content: center; }
  .qqdash .current-subject-card { width: 100%; max-width: none; box-sizing: border-box; }
  .qqdash .manage-layout { grid-template-columns: 1fr; }
  .qqdash .manage-nav { position: static; display: flex; overflow-x: auto; }
  .qqdash .mn-item { white-space: nowrap; }
  .qqdash .event-list { grid-template-columns: 1fr; }
  .v2-gate { padding: 24px; }
  .v2-gate-hero { align-items: flex-start; }
  .v2-login-overview, .event-channel-grid { grid-template-columns: 1fr; }
  .v2-gate-actions { align-items: flex-start; flex-direction: column; }
  .event-groups .event-item { grid-template-columns: 18px minmax(0, 1fr); }
  .event-groups .event-item code { grid-column: 2; text-align: left; }
  .avatar-cropper-modal { width: min(560px, calc(100vw - 16px)); padding: 12px; }
  .avatar-cropper-body { justify-content: flex-start; gap: 14px; overflow-x: auto; }
  .avatar-cropper-preview { display: none; }
}
.qqdash .assistant-member-item { gap: 12px; }
.qqdash .assistant-member-info { min-width: 0; display: flex; align-items: center; gap: 10px; }
.qqdash .assistant-member-info>img, .qqdash .assistant-avatar { width: 36px; height: 36px; flex: none; border-radius: 50%; object-fit: cover; }
.qqdash .assistant-avatar { display: grid; place-items: center; background: var(--accent-soft); color: var(--accent); font-weight: 700; }
.qqdash .assistant-member-info>span:last-child { display: grid; gap: 3px; color: var(--ink-2); font-size: 12px; }
.qqdash .assistant-member-info small { color: var(--ink-4); font-size: 11px; }
.qqdash .assistant-actions { display: flex; align-items: center; gap: 7px; }
.qqdash .assistant-role-options { display: grid; gap: 12px; margin-top: 18px; color: var(--ink-2); font-size: 13px; }
.qqdash .assistant-role-options label { display: flex; align-items: center; gap: 9px; }
.qqdash .assistant-role-options input, .qqdash .event-all-toggle input { accent-color: var(--accent); }
.qqdash .scope-range-row select { padding: 7px 28px 7px 10px; border: 1px solid var(--line-strong); border-radius: 8px; background: var(--bg-elev); color: var(--ink-2); font: inherit; }
.qqdash .group-admin-targets { display: inline-flex; gap: 4px; margin: 16px 0 12px; padding: 3px; border-radius: 9px; background: var(--bg-sunken); }
.qqdash .group-admin-targets button { padding: 7px 13px; border: 0; border-radius: 7px; background: transparent; color: var(--ink-3); font: inherit; font-size: 12px; cursor: pointer; }
.qqdash .group-admin-targets button.active { background: var(--bg-elev); color: var(--accent); box-shadow: 0 1px 4px rgba(0,0,0,.1); }
.qqdash .group-admin-whitelist { margin-bottom: 8px; }
.qqdash .group-permission-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.qqdash .group-permission-item { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 60px; padding: 12px; border: 1px solid var(--line); border-radius: 11px; background: var(--bg-elev); color: var(--ink-2); text-align: left; cursor: pointer; }
.qqdash .group-permission-item>span:first-child { display: grid; gap: 4px; }
.qqdash .group-permission-item b { font-size: 12px; }
.qqdash .group-permission-item small { color: var(--ink-4); font-size: 10.5px; line-height: 1.4; }
.qqdash .group-permission-item .switch { position: relative; flex: none; pointer-events: none; }
.qqdash .group-permission-save { display: flex; justify-content: flex-end; margin-top: 14px; }
.qqdash .sensitive-alert-config .sec-group-head { align-items: flex-start; }
.qqdash .sensitive-alert-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; margin-top: 16px; }
.qqdash .sensitive-alert-column { min-width: 0; }
.qqdash .sensitive-alert-column .dev-tester-block-head { margin-bottom: 7px; }
.qqdash .event-all-toggle { display: inline-flex; align-items: center; gap: 8px; margin: 0 0 14px; color: var(--ink-2); font-size: 12px; }
.qqdash .advanced-entry-card { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 20px; }
.qqdash .advanced-entry-copy { min-width: 0; }
.qqdash .advanced-entry-copy p { margin: 7px 0 0; color: var(--ink-4); font-size: 12px; line-height: 1.6; }
.qqdash .advanced-entry-card>.btn { flex: none; }
.qqdash .switch-row { display: flex; align-items: center; justify-content: space-between; }
.qqdash .row-desc { color: var(--ink-3); font-size: 12px; margin-top: 4px; }
.qqdash .ip-whitelist-head, .qqdash .dev-tester-block-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.qqdash .ip-add-row { display: flex; gap: 10px; margin-bottom: 14px; }
.qqdash .ip-input { flex: 1; padding: 8px 12px; border: 1px solid var(--line-strong); border-radius: 10px; font-family: var(--font-mono); font-size: 13px; }
.qqdash .ip-whitelist-list { list-style: none; padding: 0; margin: 0; }
.qqdash .ip-whitelist-item { display: flex; align-items: center; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid var(--line); }
.qqdash .auth-intro-body { color: var(--ink-3); font-size: 13px; margin-top: 10px; line-height: 1.6; }
.qqdash .bot-avatar.sm { width: 44px; height: 44px; font-size: 18px; }
.qqdash .head-right { margin-left: auto; }
.qqdash .danger-action-title { font-weight: 600; color: var(--danger); }
.qqdash .danger-action-desc { color: var(--ink-3); font-size: 12px; margin-top: 4px; }
.qq-toast { position: fixed; left: 50%; bottom: 48px; transform: translateX(-50%); background: rgba(0,0,0,.82); color: #fff; padding: 10px 20px; border-radius: 10px; font-size: 13px; z-index: 9999; }
.toast-fade-enter-active, .toast-fade-leave-active { transition: opacity .25s; }
.toast-fade-enter-from, .toast-fade-leave-to { opacity: 0; }
@media (max-width: 720px) {
  .qqdash .group-permission-grid, .qqdash .sensitive-alert-grid { grid-template-columns: 1fr; }
  .qqdash .advanced-entry-card { align-items: flex-start; flex-direction: column; }
  .qqdash .advanced-entry-card>.btn { width: 100%; justify-content: center; }
}
</style>
