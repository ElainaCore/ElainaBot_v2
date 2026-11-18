<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { useMessage } from 'naive-ui'
import yaml from 'js-yaml'
import axios from '../utils/axios'
import { responseMessage, responsePayload, responseOk } from '../utils/api'
import SvgIcon from '../components/SvgIcon.vue'
import ConfigField from '../components/config/ConfigField.vue'
import TemplateEditor from './config/TemplateEditor.vue'
import { BOT_SECTIONS, SETTINGS_SECTIONS, getFieldValue, setFieldValue } from './config/fields'

const message = useMessage()
const loading = ref(true)
const loaded = ref(false)
const savingFile = ref('')
const loadError = ref('')
const activeFile = ref('bot')
const viewMode = ref('visual')
const raw = reactive({ bot: '', settings: '', templates: '' })
const saved = reactive({ bot: '', settings: '', templates: '' })
const botIndex = ref(0)
const templateInvalid = ref(false)
const revision = ref(0)
const contentRef = ref(null)
const sectionRefs = new Map()
const focusedSection = ref('')
const TABS = [
  { key: 'bot', label: '机器人配置', icon: 'link' },
  { key: 'settings', label: '全局设置', icon: 'settings' },
  { key: 'templates', label: '消息模板', icon: 'chatbubbles' },
]
const activeTab = computed(() => TABS.find(tab => tab.key === activeFile.value))
const changedFiles = computed(() => TABS.filter(tab => raw[tab.key] !== saved[tab.key] || (tab.key === 'templates' && templateInvalid.value)).map(tab => tab.key))
const dirty = computed(() => changedFiles.value.includes(activeFile.value))
const sections = computed(() => activeFile.value === 'bot' ? BOT_SECTIONS : SETTINGS_SECTIONS)
function parse(source) { try { const value = yaml.load(source); return value && typeof value === 'object' && !Array.isArray(value) ? value : {} } catch { return {} } }
function dump(value) { return yaml.dump(value, { lineWidth: -1, noRefs: true, sortKeys: false, quotingType: '"' }) }
function validate(source) {
  try {
    const value = yaml.load(source)
    if (!value || typeof value !== 'object' || Array.isArray(value)) return '配置必须是 YAML 键值对象'
    return ''
  } catch (error) { return error.message }
}
const yamlError = computed(() => validate(raw[activeFile.value]))
const bots = computed(() => { const value = parse(raw.bot).bots; return Array.isArray(value) ? value : [] })
const currentBot = computed(() => bots.value[botIndex.value] || null)
const settings = computed(() => parse(raw.settings))
const currentData = computed(() => activeFile.value === 'bot' ? currentBot.value : settings.value)
watch(bots, value => { if (botIndex.value >= value.length) botIndex.value = Math.max(0, value.length - 1) })
watch(activeFile, () => { focusedSection.value = ''; contentRef.value?.scrollTo(0, 0) })

function updateField(field, value) {
  const data = parse(raw[activeFile.value])
  const target = activeFile.value === 'bot' ? data.bots?.[botIndex.value] : data
  if (!target) return
  setFieldValue(target, field.path, value)
  raw[activeFile.value] = dump(data)
}
function visibleFields(section) { return section.fields.filter(field => !field.when || field.when(currentData.value || {})) }
function scrollToSection(id) { focusedSection.value = id; sectionRefs.get(id)?.scrollIntoView({ block: 'start', behavior: 'smooth' }) }
function setMode(mode) {
  if (mode === 'visual' && yamlError.value) { message.error('请先修正 YAML 格式：' + yamlError.value); return }
  if (mode === 'yaml' && activeFile.value === 'templates' && templateInvalid.value) { message.error('请先修正模板按钮的 YAML 格式'); return }
  viewMode.value = mode
}
function addBot() {
  const data = parse(raw.bot)
  if (!Array.isArray(data.bots)) data.bots = []
  data.bots.push({
    appid: '', secret: '', api_version: 'new', robot_qq: '', owner_ids: [],
    websocket: { enabled: true, subscribe_channel_events: false, custom_url: '', reconnect_interval: 5, max_reconnects: -1, identify: { name: 'ElainaBot' } },
    message: { use_markdown: true, markdown_suffix: '', button_enter_to_send: false, interaction_explicit_only: false, send_default_response: false, send_error_response: true, default_response_excluded_regex: [] },
    identity: { use_union_id_for_group: false, use_union_id_for_channel: false },
    welcome: { group_welcome: false, new_user_welcome: false, friend_add_message: false },
    maintenance: { enabled: false, reply: true }, dedup: { enabled: false }, blacklist: { reply: true, user_list: {}, group_list: {} },
    non_at_message: { enabled: true, group_whitelist: [], ignore_at_other_bot: true, ignore_at_other_user: true, ignore_bot_sender: true, quiet_at_self: false, strip_bot_name_at: false },
  })
  raw.bot = dump(data)
  botIndex.value = data.bots.length - 1
}
function removeBot() {
  if (!confirm(`确定删除机器人 #${botIndex.value + 1}？保存后生效。`)) return
  const data = parse(raw.bot)
  if (!data.bots?.[botIndex.value]) return
  data.bots.splice(botIndex.value, 1)
  raw.bot = dump(data)
}

// 扫码快捷绑定机器人
const qrBind = reactive({ show: false, status: 'loading', taskId: '', url: '', img: '', message: '', appid: '' })
let qrBindTimer = null

function stopQrBindPoll() { if (qrBindTimer) { clearInterval(qrBindTimer); qrBindTimer = null } }

function closeQrBind() { stopQrBindPoll(); qrBind.show = false }

async function startQrBind() {
  stopQrBindPoll()
  qrBind.show = true; qrBind.status = 'loading'; qrBind.url = ''; qrBind.img = ''; qrBind.message = ''; qrBind.appid = ''
  try {
    const res = await axios.post('/api/config/qr-bind/start')
    const data = responsePayload(res)
    if (!responseOk(res) || !data.task_id || !data.url) {
      qrBind.status = 'error'; qrBind.message = responseMessage(res, '创建绑定任务失败')
      return
    }
    qrBind.taskId = data.task_id
    qrBind.url = data.url
    qrBind.img = `https://api.2dcode.biz/v1/create-qr-code?data=${encodeURIComponent(data.url)}`
    qrBind.status = 'waiting'
    qrBindTimer = setInterval(pollQrBind, 2000)
  } catch (e) { qrBind.status = 'error'; qrBind.message = e.normalizedMessage || e.message || '创建绑定任务失败' }
}

async function pollQrBind() {
  try {
    const res = await axios.post('/api/config/qr-bind/poll', { task_id: qrBind.taskId })
    const data = responsePayload(res)
    if (!responseOk(res)) {
      stopQrBindPoll()
      qrBind.status = data.status === 'not_found' ? 'expired' : 'error'
      qrBind.message = responseMessage(res, '绑定失败')
      return
    }
    if (data.status === 'completed') {
      stopQrBindPoll()
      qrBind.status = 'completed'; qrBind.appid = data.appid || ''
      message.success(`机器人 ${data.appid} ${data.created ? '接入成功' : '凭据已更新'}，配置已自动保存并热重载`)
      await fetchConfig(true, true)
      const idx = bots.value.findIndex(b => String(b.appid || '') === String(data.appid || ''))
      if (idx >= 0) botIndex.value = idx
      setTimeout(() => { qrBind.show = false }, 1200)
    } else if (data.status === 'expired' || data.status === 'not_found') {
      stopQrBindPoll()
      qrBind.status = 'expired'
    }
  } catch {}
}

async function fetchConfig(force = false, onlyBot = false) {
  if (!force && changedFiles.value.length && !confirm('重新加载会丢弃所有未保存的修改，是否继续？')) return
  loading.value = true
  loadError.value = ''
  try {
    const response = await axios.get('/api/config')
    if (!responseOk(response)) throw new Error(responseMessage(response, '获取配置失败'))
    const data = responsePayload(response)
    for (const file of onlyBot ? ['bot'] : ['bot', 'settings', 'templates']) raw[file] = saved[file] = data[file] || ''
    if (!onlyBot) { templateInvalid.value = false; revision.value++ }
    loaded.value = true
  } catch (error) { loadError.value = error.normalizedMessage || error.message || '获取配置失败' }
  finally { loading.value = false }
}
async function saveConfig() {
  if (!dirty.value || loading.value || savingFile.value) return
  if (activeFile.value === 'templates' && templateInvalid.value) { message.error('请先修正模板按钮的 YAML 格式'); return }
  if (yamlError.value) { message.error('YAML 格式有误：' + yamlError.value); return }
  if (viewMode.value === 'visual') {
    const invalid = contentRef.value?.querySelector('input:invalid')
    if (invalid) { invalid.reportValidity(); return }
  }
  const file = activeFile.value
  const content = raw[file]
  savingFile.value = file
  try {
    const response = await axios.post('/api/config/save', { file, content })
    if (responseOk(response)) { saved[file] = content; message.success(`${file}.yaml 已保存，部分更改需要重启生效`) }
    else message.error(responseMessage(response, '保存失败'))
  } catch (error) { message.error('保存失败：' + (error.normalizedMessage || error.message || '请重试')) }
  finally { savingFile.value = '' }
}
function onKeydown(event) { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); saveConfig() } }
function onBeforeUnload(event) { if (changedFiles.value.length) { event.preventDefault(); event.returnValue = '' } }
onMounted(() => { fetchConfig(true); window.addEventListener('keydown', onKeydown); window.addEventListener('beforeunload', onBeforeUnload) })
onUnmounted(() => { stopQrBindPoll(); window.removeEventListener('keydown', onKeydown); window.removeEventListener('beforeunload', onBeforeUnload) })
</script>

<template>
  <div class="config-page">
    <div class="cfg-shell">
      <header class="cfg-toolbar">
        <nav class="cfg-files" aria-label="配置文件">
          <button v-for="tab in TABS" :key="tab.key" class="cfg-file" :class="{ active: activeFile === tab.key }" :aria-pressed="activeFile === tab.key" @click="activeFile = tab.key">
            <SvgIcon :name="tab.icon" :size="19" />
            <span><strong>{{ tab.label }}</strong><small>{{ tab.key }}.yaml</small></span>
            <i v-if="changedFiles.includes(tab.key)" class="cfg-dirty-dot" aria-label="有未保存的修改" />
          </button>
        </nav>
        <div class="cfg-toolbar-actions">
          <div class="cfg-mode" aria-label="编辑方式">
            <button :class="{ active: viewMode === 'visual' }" :aria-pressed="viewMode === 'visual'" @click="setMode('visual')"><SvgIcon name="grid" :size="13" />可视化</button>
            <button :class="{ active: viewMode === 'yaml' }" :aria-pressed="viewMode === 'yaml'" @click="setMode('yaml')"><SvgIcon name="code" :size="14" />YAML</button>
          </div>
          <button class="cfg-btn cfg-icon-btn" title="重新加载配置" aria-label="重新加载配置" :disabled="loading || !!savingFile" @click="fetchConfig()"><SvgIcon name="refresh" :size="16" :class="{ 'cfg-spinning': loading }" /></button>
          <button class="cfg-btn cfg-save" :disabled="!dirty || !!savingFile || loading || !loaded" @click="saveConfig"><SvgIcon name="save" :size="15" />{{ savingFile === activeFile ? '保存中…' : '保存配置' }}</button>
        </div>
      </header>

      <div v-if="loadError" class="cfg-notice cfg-notice--error" role="alert"><SvgIcon name="alert-circle" :size="16" /><span>{{ loadError }}</span><button @click="fetchConfig()">重新加载</button></div>
      <div v-if="loading && !loaded" class="cfg-state" role="status"><SvgIcon name="refresh" :size="25" class="cfg-spinning" /><strong>正在加载配置</strong><span>读取机器人、全局设置与消息模板</span></div>
      <div v-else-if="!loaded" class="cfg-state"><SvgIcon name="alert-circle" :size="28" /><strong>配置暂时无法加载</strong><button class="cfg-btn" @click="fetchConfig()">重试</button></div>
      <div v-else class="cfg-workspace" :class="{ 'cfg-workspace--templates': activeFile === 'templates' && viewMode === 'visual' }">
        <aside v-if="activeFile !== 'templates' && viewMode === 'visual'" class="cfg-nav" aria-label="配置分区">
          <div class="cfg-nav-title">本页设置 <span>{{ sections.length }}</span></div>
          <button v-for="section in sections" :key="section.id" :class="{ active: focusedSection === section.id }" @click="scrollToSection(section.id)"><SvgIcon :name="section.icon" :size="15" /><span>{{ section.title }}</span></button>
          <div class="cfg-nav-note"><SvgIcon name="information-circle" :size="15" /><span>所有分区展开<br />可在本页集中调整</span></div>
        </aside>
        <section v-show="activeFile !== 'templates' || viewMode === 'yaml'" class="cfg-main" :aria-label="activeTab.label">
          <div v-if="viewMode === 'visual' && activeFile === 'bot'" class="cfg-bot-picker">
            <span class="cfg-bot-symbol"><SvgIcon name="link" :size="18" /></span>
            <select v-if="bots.length" v-model="botIndex" aria-label="选择机器人"><option v-for="(bot, index) in bots" :key="index" :value="index">{{ bot.name || `机器人 #${index + 1}` }} · {{ bot.appid || '待填写 AppID' }}</option></select>
            <span v-else class="cfg-no-bots">暂未添加机器人</span>
            <span class="cfg-bot-count">{{ bots.length }} 个机器人</span>
            <div class="cfg-bot-actions">
              <button class="cfg-btn" @click="addBot"><SvgIcon name="plus" :size="14" />添加</button>
              <button class="cfg-btn cfg-btn-accent" @click="startQrBind"><SvgIcon name="link" :size="14" />扫码接入</button>
              <button v-if="bots.length" class="cfg-btn cfg-icon-btn cfg-btn-danger" title="删除当前机器人" aria-label="删除当前机器人" @click="removeBot"><SvgIcon name="trash" :size="15" /></button>
            </div>
          </div>
          <div ref="contentRef" class="cfg-scroll" :class="{ 'cfg-scroll--yaml': viewMode === 'yaml' }">
            <template v-if="viewMode === 'yaml'">
              <div class="cfg-yaml-heading"><span><SvgIcon name="code" :size="16" />源文件编辑</span><small>Ctrl / ⌘ + S 保存</small></div>
              <textarea v-model="raw[activeFile]" class="cfg-yaml-editor" :aria-label="`${activeFile}.yaml 源文件`" spellcheck="false" />
              <pre v-if="yamlError" class="cfg-yaml-error" role="alert">{{ yamlError }}</pre>
            </template>
            <div v-else-if="activeFile === 'bot' && !currentBot" class="cfg-state"><SvgIcon name="link" :size="32" /><strong>接入你的第一个机器人</strong><span>添加配置，或使用手机 QQ 扫码接入</span><button class="cfg-btn cfg-save" @click="addBot"><SvgIcon name="plus" :size="15" />添加机器人</button></div>
            <div v-else class="cfg-columns">
              <div v-for="column in [0, 1]" :key="`${activeFile}-${column}-${botIndex}`" class="cfg-column">
                <section v-for="section in sections.filter(item => item.column === column)" :id="`cfg-${section.id}`" :key="section.id" :ref="el => el ? sectionRefs.set(section.id, el) : sectionRefs.delete(section.id)" class="cfg-card" :class="{ 'cfg-card--caution': section.caution, 'cfg-card--focused': focusedSection === section.id }" :aria-label="section.title">
                  <header class="cfg-card-heading"><span class="cfg-card-icon"><SvgIcon :name="section.icon" :size="17" /></span><div><h3>{{ section.title }}</h3><p>{{ section.description }}</p></div><span class="cfg-card-count">{{ visibleFields(section).length + (section.switches?.length || 0) }} 项</span></header>
                  <div class="cfg-fields"><ConfigField v-for="field in visibleFields(section)" :key="field.path" :field="field" :prefix="`${activeFile}-${botIndex}`" :model-value="getFieldValue(currentData, field)" @update:model-value="updateField(field, $event)" /></div>
                  <div v-if="section.switches" class="cfg-switch-pair"><ConfigField v-for="field in section.switches" :key="field.path" :field="field" :prefix="activeFile" :model-value="getFieldValue(currentData, field)" @update:model-value="updateField(field, $event)" /></div>
                  <p v-if="section.note" class="cfg-card-note"><SvgIcon name="information-circle" :size="13" />{{ section.note }}</p>
                </section>
              </div>
            </div>
          </div>
        </section>
        <TemplateEditor :key="revision" v-show="activeFile === 'templates' && viewMode === 'visual'" :active="activeFile === 'templates' && viewMode === 'visual'" :source="raw.templates" :bots="bots" @update:source="raw.templates = $event" @invalid="templateInvalid = $event" />
      </div>
      <footer class="cfg-footer">
        <span class="cfg-save-status" :class="{ pending: dirty }"><i /><span>{{ dirty ? '当前文件有未保存的修改' : '当前文件已保存' }}</span></span>
        <span v-if="changedFiles.length" class="cfg-other-pending">{{ changedFiles.length }} 个文件待保存</span>
        <span class="cfg-footer-tip">保存当前文件 <kbd>Ctrl S</kbd></span>
      </footer>
    </div>

    <div v-if="qrBind.show" class="cfg-modal-overlay" @click.self="closeQrBind" @keydown.esc="closeQrBind">
      <div class="cfg-qr-modal" role="dialog" aria-modal="true" aria-labelledby="cfg-qr-title">
        <h3 id="cfg-qr-title">扫码接入机器人</h3>
        <p>使用机器人管理员手机 QQ 扫描二维码完成绑定</p>
        <p class="cfg-qr-warning">扫码接入会重置机器人密钥（Secret），请留意其他框架的接入状态。</p>
        <div class="cfg-qr-frame"><img v-if="qrBind.img" :src="qrBind.img" alt="绑定二维码" /><span v-else>{{ qrBind.status === 'error' ? '获取失败' : '正在生成二维码…' }}</span></div>
        <p class="cfg-qr-status" role="status">{{ qrBind.status === 'completed' ? `绑定成功 · ${qrBind.appid}` : qrBind.status === 'expired' ? '二维码已过期，请重新获取' : qrBind.status === 'error' ? (qrBind.message || '绑定失败') : qrBind.status === 'loading' ? '正在创建绑定任务…' : '等待扫码绑定…' }}</p>
        <button v-if="qrBind.status === 'expired' || qrBind.status === 'error'" class="cfg-btn cfg-save" @click="startQrBind">重新获取二维码</button>
        <button class="cfg-btn" @click="closeQrBind">关闭</button>
      </div>
    </div>
  </div>
</template>

<style src="./config/config.css"></style>
