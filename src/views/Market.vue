<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { NModal, NPopover, useDialog, useMessage } from 'naive-ui'
import axios from '../utils/axios'
import { formatFileSize, urlHost } from '../utils/format'
import SvgIcon from '../components/SvgIcon.vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const msg = useMessage()
const dialog = useDialog()
const items = ref([])
const search = ref('')
const category = ref('')
const type = ref('all')
const status = ref('all')
const sort = ref('recommended')
const page = ref(1)
const pageSize = ref(48)
const scrollArea = ref(null)
const view = ref('grid')
try {
  if (localStorage.getItem('elaina_market_view') === 'list') view.value = 'list'
} catch {}
const typeOptions = [
  { value: 'all', label: '全部', icon: 'grid' },
  { value: 'complete', label: '完整', icon: 'extension-puzzle' },
  { value: 'single', label: '独立', icon: 'file' },
  { value: 'module', label: '模块', icon: 'cube' },
]
const loading = ref(false)
const error = ref('')
const preview = reactive({
  show: false,
  name: '',
  github: '',
  branch: 'main',
  files: [],
  selected: '',
  loading: false,
  error: '',
})
const selectedPreviewFile = computed(
  () => preview.files.find((file) => file.path === preview.selected) || preview.files[0] || null
)
function resolveMarkdownImageUrl(src) {
  const value = String(src || '').trim()
  if (!value || value.startsWith('data:') || value.startsWith('blob:')) return value
  if (/^[a-z][a-zd+.-]*:/i.test(value) && !/^https?:/i.test(value)) return ''

  const blobMatch = value.match(/^https?:\/\/github\.com\/([^/]+)\/([^/]+?)\/blob\/([^/]+)\/(.+)$/i)
  if (blobMatch) {
    const [, owner, repo, branch, path] = blobMatch
    return `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${path}`
  }
  if (/^(?:https?:)?\/\//i.test(value)) return value

  const repoMatch = preview.github.match(/^https?:\/\/github\.com\/([^/]+)\/([^/]+?)(?:\.git)?(?:\/|$)/i)
  if (!repoMatch) return value
  const [, owner, repo] = repoMatch
  const filePath = selectedPreviewFile.value?.path || ''
  const directory = filePath.includes('/') ? filePath.slice(0, filePath.lastIndexOf('/') + 1) : ''
  const base = `https://raw.githubusercontent.com/${owner}/${repo}/${preview.branch}/${directory}`
  try {
    return new URL(value, base).href
  } catch {
    return value
  }
}
const previewHtml = computed(() => {
  const content = selectedPreviewFile.value?.content || ''
  if (!content) return ''
  const doc = new DOMParser().parseFromString(DOMPurify.sanitize(marked.parse(content)), 'text/html')
  doc.querySelectorAll('img[src]').forEach((img) => {
    img.src = resolveMarkdownImageUrl(img.getAttribute('src'))
    img.loading = 'lazy'
    img.referrerPolicy = 'no-referrer'
  })
  return DOMPurify.sanitize(doc.body.innerHTML)
})
function normType(i) {
  const t = (i.type || '').toLowerCase()
  if (t === 'module') return 'module'
  if (t === 'single' || t === 'standalone' || t === 'alone') return 'single'
  return 'complete'
}

const mirrorShow = ref(false)
const mirrorList = ref([])
const fastMirrors = ref([])
const selectedMirror = ref('')
const mirrorTesting = ref(false)
let mirrorTestSource = null

function mirrorLabel(m) {
  return m ? urlHost(m, 'GitHub').slice(0, 30) : '自动选择'
}
function mirrorLatency(m) {
  const r = fastMirrors.value.find((x) => (typeof x === 'string' ? x : x.mirror) === m)
  if (!r) return ''
  const ms = typeof r === 'object' ? r.latency : null
  return ms != null ? `${(ms * 1000).toFixed(0)}ms` : ''
}
async function fetchMirror() {
  try {
    const res = await axios.get('/api/update/mirrors')
    if (res.data.success) {
      const data = res.data.data || {}
      mirrorList.value = data.mirrors || []
      fastMirrors.value = data.fast_mirrors || []
      selectedMirror.value = data.custom_mirror || ''
    }
  } catch {}
}
async function setMirror(m) {
  selectedMirror.value = m
  mirrorShow.value = false
  try {
    await axios.post('/api/update/mirror', { mirror: m })
    msg.success(`镜像已设为: ${mirrorLabel(m)}`)
  } catch {
    msg.error('设置镜像失败')
    await fetchMirror()
  }
}
function testMirrors() {
  if (mirrorTesting.value) return
  mirrorTesting.value = true
  fastMirrors.value = []
  mirrorTestSource = new EventSource('/api/update/test-mirrors')
  mirrorTestSource.onmessage = (event) => {
    try {
      const result = JSON.parse(event.data)
      if (result.done) {
        mirrorTestSource?.close()
        mirrorTestSource = null
        mirrorTesting.value = false
        msg.success('镜像测速完成')
        return
      }
      fastMirrors.value.push(result)
    } catch {}
  }
  mirrorTestSource.onerror = () => {
    mirrorTestSource?.close()
    mirrorTestSource = null
    mirrorTesting.value = false
    msg.error('镜像测速中断')
  }
}

const typeItems = computed(() =>
  type.value === 'all' ? items.value : items.value.filter((i) => i._type === type.value)
)
const typeCounts = computed(() =>
  Object.fromEntries(
    typeOptions.map((option) => [
      option.value,
      option.value === 'all'
        ? items.value.length
        : items.value.filter((item) => item._type === option.value).length,
    ])
  )
)
const matched = computed(() => {
  let list = typeItems.value
  if (category.value) list = list.filter((i) => i.category === category.value)
  const q = search.value.trim().toLowerCase()
  if (q)
    list = list.filter(
      (i) =>
        (i.name || '').toLowerCase().includes(q) ||
        (i.description || '').toLowerCase().includes(q) ||
        (i.author || '').toLowerCase().includes(q) ||
        (i.tags || []).some((t) => t.toLowerCase().includes(q))
    )
  return list
})
const statusOptions = computed(() => [
  { value: 'all', label: '全部', count: matched.value.length },
  { value: 'installed', label: '已安装', count: matched.value.filter((item) => item.installed).length },
  { value: 'updates', label: '可更新', count: matched.value.filter((item) => item.has_update).length },
])
const filtered = computed(() => {
  let list = matched.value
  if (status.value === 'installed') list = list.filter((item) => item.installed)
  if (status.value === 'updates') list = list.filter((item) => item.has_update)
  if (sort.value === 'name')
    return [...list].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN', { numeric: true }))
  if (sort.value === 'official')
    return [...list].sort(
      (a, b) =>
        Number(isOfficial(b)) - Number(isOfficial(a)) || Number(!!b.has_update) - Number(!!a.has_update)
    )
  return [...list].sort((a, b) => Number(!!b.has_update) - Number(!!a.has_update))
})
const categories = computed(() =>
  [...new Set(typeItems.value.map((item) => item.category).filter(Boolean))].map((name) => ({
    name,
    count: typeItems.value.filter((item) => item.category === name).length,
  }))
)
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize.value)))
const visibleItems = computed(() =>
  filtered.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value)
)
const rangeStart = computed(() => (filtered.value.length ? (page.value - 1) * pageSize.value + 1 : 0))
const rangeEnd = computed(() => Math.min(page.value * pageSize.value, filtered.value.length))
const pageButtons = computed(() => {
  const numbers =
    pageCount.value <= 7
      ? Array.from({ length: pageCount.value }, (_, index) => index + 1)
      : [...new Set([1, pageCount.value, page.value - 1, page.value, page.value + 1])]
          .filter((n) => n > 0 && n <= pageCount.value)
          .sort((a, b) => a - b)
  return numbers.flatMap((number, index) =>
    index && number - numbers[index - 1] > 1 ? [`gap-${number}`, number] : [number]
  )
})
const hasFilters = computed(
  () => !!search.value.trim() || !!category.value || type.value !== 'all' || status.value !== 'all'
)

function resetFilters() {
  search.value = ''
  category.value = ''
  type.value = 'all'
  status.value = 'all'
}
function categoryIcon(name) {
  if (/系统|管理/.test(name)) return 'settings'
  if (/游戏|娱乐/.test(name)) return 'rocket'
  if (/工具|实用/.test(name)) return 'code'
  if (/图片|图像|美化/.test(name)) return 'image'
  if (/AI|智能/i.test(name)) return 'sparkles'
  if (/社交|群/.test(name)) return 'chatbubbles'
  return 'cube'
}
function itemTypeLabel(item) {
  return typeOptions.find((option) => option.value === item._type)?.label || '完整'
}
function itemBusy(item) {
  return item._installing || item._uninstalling
}
function scrollToTop() {
  nextTick(() => {
    if (scrollArea.value) scrollArea.value.scrollTop = 0
  })
}
watch(type, () => {
  category.value = ''
})
watch([search, category, type, status, sort, pageSize], () => {
  page.value = 1
  scrollToTop()
})
watch(pageCount, (count) => {
  page.value = Math.min(page.value, count)
})
watch(page, scrollToTop)
watch(view, (value) => {
  try {
    localStorage.setItem('elaina_market_view', value)
  } catch {}
})

function avatarUrl(item) {
  const m = (item.github || '').match(/github\.com\/([^/]+)/)
  return m ? `https://github.com/${m[1]}.png?size=80` : ''
}
function isOfficial(item) {
  return (item.github || '').includes('ElainaCore/') || (item.author || '').toLowerCase() === 'elainabot'
}
function filteredTags(item) {
  const cat = (item.category || '').toLowerCase()
  return (item.tags || []).filter((t) => t.toLowerCase() !== cat)
}
async function fetchList() {
  loading.value = true
  error.value = ''
  try {
    const res = await axios.get('/api/market/list')
    if (res.data.success)
      items.value = (res.data.data || []).map((i) => ({
        ...i,
        _type: normType(i),
        _installing: false,
        _uninstalling: false,
        _avatarLoaded: false,
        _avatarError: false,
      }))
    else error.value = res.data.message || '获取插件列表失败'
  } catch {
    error.value = '无法连接插件库, 请检查网络'
  } finally {
    loading.value = false
  }
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const res = await axios.post('/api/market/refresh')
    if (res.data.success) {
      msg.success(res.data.message || '已刷新')
      await fetchList()
    } else {
      error.value = res.data.message || '刷新失败'
      loading.value = false
    }
  } catch {
    error.value = '刷新失败'
    loading.value = false
  }
}

async function previewItem(item) {
  if (!item.github) {
    msg.warning('无可预览仓库')
    return
  }
  Object.assign(preview, {
    show: true,
    name: item.name,
    github: item.github,
    branch: item.branch || 'main',
    files: [],
    selected: '',
    loading: true,
    error: '',
  })
  try {
    const res = await axios.post('/api/market/preview', {
      github: item.github,
      branch: item.branch || 'main',
      path: item.path || '',
    })
    if (res.data.success) {
      preview.files = res.data.files || []
      preview.selected = preview.files[0]?.path || ''
    } else preview.error = res.data.message || '预览失败'
  } catch {
    preview.error = '预览请求失败'
  } finally {
    preview.loading = false
  }
}

async function install(item) {
  if (itemBusy(item)) return
  item._installing = true
  try {
    const res = await axios.post('/api/market/install', {
      name: item.name,
      type: item._type,
      github: item.github || '',
      url: item.download_url || '',
      path: item.path || '',
      alone: item.alone !== false,
      branch: item.branch || 'main',
    })
    if (res.data.success) {
      msg.success(res.data.message || `${item.name} 安装成功`)
      await fetchList()
    } else msg.error(res.data.message || '安装失败')
  } catch {
    msg.error('安装请求失败')
  } finally {
    item._installing = false
  }
}

function uninstall(item) {
  if (itemBusy(item)) return
  dialog.warning({
    title: '卸载插件',
    content: `确定要卸载插件 “${item.name}” 吗？卸载后插件目录会被删除，此操作不可恢复。`,
    positiveText: '确定卸载',
    negativeText: '取消',
    onPositiveClick: async () => {
      item._uninstalling = true
      try {
        const res = await axios.post('/api/market/uninstall', { name: item.name, type: item._type })
        if (res.data.success) {
          msg.success(res.data.message || `${item.name} 已卸载`)
          await fetchList()
        } else msg.error(res.data.message || '卸载失败')
      } catch {
        msg.error('卸载请求失败')
      } finally {
        item._uninstalling = false
      }
    },
  })
}

onMounted(() => {
  fetchList()
  fetchMirror()
})
onUnmounted(() => {
  mirrorTestSource?.close()
})
</script>

<template>
  <section class="market-page" aria-label="插件市场">
    <div class="market-layout">
      <aside class="market-sidebar" aria-label="市场分类">
        <div class="market-sidebar-heading">
          <SvgIcon name="storefront" :size="18" />
          <h2>探索市场</h2>
        </div>
        <nav class="market-type-tabs" aria-label="插件类型">
          <button
            v-for="option in typeOptions"
            :key="option.value"
            :class="{ active: type === option.value }"
            :aria-pressed="type === option.value"
            @click="type = option.value"
          >
            {{ option.label }}
          </button>
        </nav>
        <div class="market-section-label">
          <span>分类浏览</span><span>{{ categories.length }} 个分类</span>
        </div>
        <nav class="market-categories" aria-label="插件分类">
          <button :class="{ active: !category }" :aria-pressed="!category" @click="category = ''">
            <SvgIcon name="grid" :size="16" /><span>全部插件</span><small>{{ typeItems.length }}</small>
          </button>
          <button
            v-for="entry in categories"
            :key="entry.name"
            :class="{ active: category === entry.name }"
            :aria-pressed="category === entry.name"
            :title="entry.name"
            @click="category = entry.name"
          >
            <SvgIcon :name="categoryIcon(entry.name)" :size="16" /><span>{{ entry.name }}</span
            ><small>{{ entry.count }}</small>
          </button>
        </nav>
        <div class="market-sidebar-bottom">
          <div class="market-community">
            <span class="market-community-icon"><SvgIcon name="code" :size="19" /></span>
            <div><strong>灵感，来自社区</strong><span>分享你的创意与实用工具</span></div>
          </div>
          <a
            class="market-contribute"
            href="https://github.com/ElainaCore/Elaina-plugins"
            target="_blank"
            rel="noopener noreferrer"
            >发布你的插件<SvgIcon name="arrow-forward" :size="14"
          /></a>
          <p class="market-notice">
            <SvgIcon name="information-circle" :size="13" /><span
              >第三方插件由社区提供，请自行辨别可用性与安全风险。</span
            >
          </p>
        </div>
      </aside>

      <div class="market-main">
        <header class="market-header">
          <div class="market-heading">
            <div class="market-heading-title">
              <h1>插件市场</h1>
              <span class="market-total">{{ items.length }}</span>
            </div>
            <p>发现实用扩展，让机器人拥有更多可能。</p>
          </div>
          <div class="market-header-actions">
            <NPopover
              v-model:show="mirrorShow"
              trigger="click"
              placement="bottom-end"
              :show-arrow="false"
              raw
            >
              <template #trigger
                ><button
                  class="market-button market-mirror-trigger"
                  :title="'下载镜像：' + mirrorLabel(selectedMirror)"
                  aria-label="选择下载镜像"
                  :aria-expanded="mirrorShow"
                >
                  <SvgIcon name="globe" :size="15" /><span>下载镜像</span>
                </button></template
              >
              <div class="market-mirror">
                <div class="market-mirror-heading">
                  <div>
                    <strong>GitHub 下载镜像</strong>
                    <p>插件安装与框架更新共用</p>
                  </div>
                  <button class="market-button" :disabled="mirrorTesting" @click="testMirrors">
                    <SvgIcon name="refresh" :size="13" :class="{ spinning: mirrorTesting }" />{{
                      mirrorTesting ? '测速中' : '一键测速'
                    }}
                  </button>
                </div>
                <div class="market-mirror-options" role="group" aria-label="下载镜像">
                  <button
                    :class="{ active: !selectedMirror }"
                    :aria-pressed="!selectedMirror"
                    @click="setMirror('')"
                  >
                    <span>自动选择<small>按可用性选择下载线路</small></span
                    ><SvgIcon v-if="!selectedMirror" name="check" :size="16" />
                  </button>
                  <button
                    v-for="mirror in mirrorList"
                    :key="mirror"
                    :class="{ active: selectedMirror === mirror }"
                    :aria-pressed="selectedMirror === mirror"
                    @click="setMirror(mirror)"
                  >
                    <span>{{ mirrorLabel(mirror) }}</span
                    ><small v-if="mirrorLatency(mirror)">{{ mirrorLatency(mirror) }}</small
                    ><SvgIcon v-if="selectedMirror === mirror" name="check" :size="16" />
                  </button>
                </div>
              </div>
            </NPopover>
            <button
              class="market-button market-refresh"
              :disabled="loading"
              aria-label="刷新插件市场"
              title="刷新插件市场"
              @click="refresh"
            >
              <SvgIcon name="refresh" :size="15" :class="{ spinning: loading }" /><span>刷新</span>
            </button>
            <a
              class="market-button market-submit"
              href="https://github.com/ElainaCore/Elaina-plugins"
              target="_blank"
              rel="noopener noreferrer"
              ><SvgIcon name="plus" :size="15" /><span>提交插件</span></a
            >
          </div>
        </header>

        <div class="market-toolbar">
          <label class="market-search"
            ><SvgIcon name="search" :size="16" /><input
              v-model="search"
              type="search"
              aria-label="搜索插件"
              placeholder="搜索插件、作者或标签…" /><button
              v-if="search"
              type="button"
              class="market-icon-button"
              aria-label="清除搜索"
              @click.prevent="search = ''"
            >
              <SvgIcon name="x" :size="13" /></button
          ></label>
          <nav class="market-status-tabs" aria-label="安装状态">
            <button
              v-for="option in statusOptions"
              :key="option.value"
              :class="{ active: status === option.value }"
              :aria-pressed="status === option.value"
              @click="status = option.value"
            >
              {{ option.label
              }}<span :class="{ 'has-updates': option.value === 'updates' && option.count }">{{
                option.count
              }}</span>
            </button>
          </nav>
          <div class="market-filter-selects">
            <select v-model="type" class="market-select market-mobile-select" aria-label="插件类型">
              <option v-for="option in typeOptions" :key="option.value" :value="option.value">
                {{
                  option.value === 'all'
                    ? '全部类型'
                    : option.label + (option.value === 'module' ? '' : '插件')
                }}
                · {{ typeCounts[option.value] }}
              </option>
            </select>
            <select v-model="category" class="market-select market-mobile-select" aria-label="插件分类">
              <option value="">全部分类</option>
              <option v-for="entry in categories" :key="entry.name" :value="entry.name">
                {{ entry.name }} · {{ entry.count }}
              </option>
            </select>
            <select v-model="sort" class="market-select market-sort" aria-label="插件排序">
              <option value="recommended">更新优先</option>
              <option value="name">名称排序</option>
              <option value="official">官方优先</option>
            </select>
          </div>
          <div class="market-view-switch" role="group" aria-label="显示方式">
            <button
              :class="{ active: view === 'grid' }"
              :aria-pressed="view === 'grid'"
              title="卡片视图"
              aria-label="卡片视图"
              @click="view = 'grid'"
            >
              <SvgIcon name="grid" :size="15" /></button
            ><button
              :class="{ active: view === 'list' }"
              :aria-pressed="view === 'list'"
              title="列表视图"
              aria-label="列表视图"
              @click="view = 'list'"
            >
              <SvgIcon name="list" :size="16" />
            </button>
          </div>
        </div>

        <div class="market-results-heading">
          <div>
            <h2>{{ search.trim() ? '搜索结果' : category || '全部插件' }}</h2>
            <span>{{ loading ? '加载中' : filtered.length + ' 个结果' }}</span>
          </div>
          <button v-if="hasFilters" class="market-reset" @click="resetFilters">
            <SvgIcon name="x" :size="12" />重置筛选</button
          ><span v-else class="market-results-note">{{
            type === 'all'
              ? '完整插件、独立插件与模块'
              : typeOptions.find((option) => option.value === type)?.label + (type === 'module' ? '' : '插件')
          }}</span>
        </div>

        <div ref="scrollArea" class="market-scroll" :aria-busy="loading">
          <div v-if="loading" class="market-grid market-skeletons" aria-label="正在加载插件库" role="status">
            <div v-for="index in 18" :key="index" class="market-skeleton">
              <div><i /><span /></div>
              <p />
              <p />
              <footer />
            </div>
          </div>
          <div v-else-if="error" class="market-state" role="alert">
            <span class="market-state-icon error"><SvgIcon name="alert-circle" :size="27" /></span>
            <h3>插件库暂时无法加载</h3>
            <p>{{ error }}</p>
            <button class="market-button" @click="fetchList">
              <SvgIcon name="refresh" :size="14" />重新加载
            </button>
          </div>
          <template v-else-if="filtered.length">
            <div class="market-grid" :class="{ 'market-list': view === 'list' }">
              <article
                v-for="item in visibleItems"
                :key="item._type + ':' + item.name + ':' + (item.github || '') + ':' + (item.path || '')"
                class="m-card"
                :class="{ 'm-card-update': item.has_update }"
                :aria-label="item.name"
              >
                <div class="m-card-head">
                  <div class="m-card-icon" :class="'type-' + item._type">
                    <img
                      v-if="avatarUrl(item) && !item._avatarError"
                      :src="avatarUrl(item)"
                      alt=""
                      class="m-avatar"
                      :class="{ loaded: item._avatarLoaded }"
                      loading="lazy"
                      decoding="async"
                      referrerpolicy="no-referrer"
                      @load="item._avatarLoaded = true"
                      @error="item._avatarError = true"
                    /><SvgIcon
                      v-if="!avatarUrl(item) || !item._avatarLoaded || item._avatarError"
                      :name="
                        typeOptions.find((option) => option.value === item._type)?.icon || 'extension-puzzle'
                      "
                      :size="18"
                    />
                  </div>
                  <div class="m-card-info">
                    <div class="m-card-name-row">
                      <h3 class="m-card-name" :title="item.name">{{ item.name }}</h3>
                      <span v-if="isOfficial(item)" class="m-official" title="官方插件" aria-label="官方插件"
                        ><SvgIcon name="shield" :size="12"
                      /></span>
                    </div>
                    <div
                      class="m-card-author"
                      :title="
                        (item.author || '未知作者') +
                        ' · ' +
                        itemTypeLabel(item) +
                        (item.version ? ' · v' + item.version : '')
                      "
                    >
                      <span>{{ item.author || '未知作者' }}</span
                      ><i>·</i><span class="m-card-kind">{{ itemTypeLabel(item) }}</span
                      ><template v-if="item.version"
                        ><i>·</i
                        ><span
                          class="m-card-version"
                          :title="
                            item.local_version && item.local_version !== item.version
                              ? '本地 v' + item.local_version + ' → v' + item.version
                              : 'v' + item.version
                          "
                          >v{{ item.version }}</span
                        ></template
                      >
                    </div>
                  </div>
                  <span v-if="item.has_update" class="m-card-status update"><i />可更新</span
                  ><span v-else-if="item.installed" class="m-card-status installed"
                    ><SvgIcon name="check" :size="11" />已安装</span
                  >
                </div>
                <p class="m-card-desc" :title="item.description || '暂无描述'">
                  {{ item.description || '这个插件还没有填写简介。' }}
                </p>
                <div
                  class="m-card-tags"
                  :title="[item.category, ...(item.tags || [])].filter(Boolean).join(' · ')"
                >
                  <span class="m-tag cat">{{ item.category || '未分类' }}</span
                  ><span v-for="tag in filteredTags(item).slice(0, 2)" :key="tag" class="m-tag">{{
                    tag
                  }}</span>
                </div>
                <div class="m-card-actions">
                  <a
                    v-if="item.github"
                    :href="item.github"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="market-icon-button m-repository"
                    :aria-label="'打开 ' + item.name + ' 的仓库'"
                    title="GitHub 仓库"
                    ><SvgIcon name="github" :size="14"
                  /></a>
                  <button
                    v-if="item.github"
                    class="market-icon-button"
                    :aria-label="'查看 ' + item.name + ' 的文档'"
                    title="查看文档"
                    @click="previewItem(item)"
                  >
                    <SvgIcon name="document-text" :size="15" />
                  </button>
                  <button
                    v-if="item.installed"
                    class="market-icon-button m-uninstall"
                    :aria-label="'卸载 ' + item.name"
                    :title="
                      item._type !== 'module' && item.name === 'system' ? '系统插件不可卸载' : '卸载插件'
                    "
                    :disabled="itemBusy(item) || (item._type !== 'module' && item.name === 'system')"
                    @click="uninstall(item)"
                  >
                    <SvgIcon
                      :name="item._uninstalling ? 'refresh' : 'trash'"
                      :size="14"
                      :class="{ spinning: item._uninstalling }"
                    />
                  </button>
                  <button
                    class="m-install"
                    :class="{ update: item.has_update, reinstall: item.installed && !item.has_update }"
                    :disabled="itemBusy(item)"
                    :aria-label="
                      (item.has_update ? '更新 ' : item.installed ? '重新安装 ' : '安装 ') + item.name
                    "
                    @click="install(item)"
                  >
                    <SvgIcon
                      :name="
                        item._installing
                          ? 'refresh'
                          : item.has_update
                            ? 'refresh'
                            : item.installed
                              ? 'check'
                              : 'plus'
                      "
                      :size="13"
                      :class="{ spinning: item._installing }"
                    /><span>{{
                      item._installing
                        ? '安装中'
                        : item.has_update
                          ? '更新'
                          : item.installed
                            ? '重装'
                            : '安装'
                    }}</span>
                  </button>
                </div>
              </article>
            </div>
            <p class="market-notice market-mobile-notice">
              <SvgIcon name="information-circle" :size="13" /><span
                >第三方插件由社区提供，请自行辨别可用性与安全风险。</span
              >
            </p>
          </template>
          <div v-else class="market-state" role="status">
            <span class="market-state-icon"
              ><SvgIcon :name="hasFilters ? 'search' : 'extension-puzzle'" :size="27"
            /></span>
            <h3>{{ hasFilters ? '没有找到匹配的插件' : '市场里还没有插件' }}</h3>
            <p>
              {{ hasFilters ? '试试其他关键词，或调整分类和安装状态。' : '稍后刷新看看，新的创意正在路上。' }}
            </p>
            <button v-if="hasFilters" class="market-button" @click="resetFilters">清除筛选</button
            ><button v-else class="market-button" @click="refresh">刷新插件市场</button>
          </div>
        </div>

        <footer class="market-footer">
          <div class="market-result-range" aria-live="polite">
            <span>{{ rangeStart }}–{{ rangeEnd }}<i>/</i>{{ filtered.length }} 个</span
            ><label
              ><select v-model.number="pageSize" class="market-select" aria-label="每页插件数量">
                <option :value="24">24 个 / 页</option>
                <option :value="48">48 个 / 页</option>
                <option :value="96">96 个 / 页</option>
              </select></label
            >
          </div>
          <nav class="market-pagination" aria-label="插件分页">
            <button :disabled="page <= 1 || loading" aria-label="上一页" @click="page--">
              <SvgIcon name="chevron-back" :size="15" /></button
            ><template v-for="number in pageButtons" :key="number"
              ><span v-if="typeof number === 'string'" class="market-page-gap">…</span
              ><button
                v-else
                class="market-page-number"
                :class="{ active: page === number }"
                :aria-current="page === number ? 'page' : undefined"
                :aria-label="'第 ' + number + ' 页'"
                :disabled="loading"
                @click="page = number"
              >
                {{ number }}
              </button></template
            ><span class="market-page-mobile">{{ page }} / {{ pageCount }}</span
            ><button :disabled="page >= pageCount || loading" aria-label="下一页" @click="page++">
              <SvgIcon name="chevron-forward" :size="15" />
            </button>
          </nav>
        </footer>
      </div>
    </div>

    <NModal v-model:show="preview.show" :close-on-esc="true">
      <section class="m-modal" role="dialog" aria-modal="true" aria-labelledby="market-document-title">
        <header class="m-modal-head">
          <div class="m-modal-title">
            <span class="m-document-icon"><SvgIcon name="document-text" :size="19" /></span>
            <div>
              <h2 id="market-document-title">{{ preview.name }}</h2>
              <p>插件文档</p>
            </div>
          </div>
          <button class="market-icon-button m-close" aria-label="关闭文档" @click="preview.show = false">
            <SvgIcon name="x" :size="19" />
          </button>
        </header>
        <div v-if="preview.files.length" class="m-document-toolbar">
          <select
            v-model="preview.selected"
            class="market-select m-md-select"
            aria-label="选择 Markdown 文档"
          >
            <option v-for="file in preview.files" :key="file.path" :value="file.path">
              {{ file.name }}
            </option></select
          ><span v-if="selectedPreviewFile?.size" class="m-preview-size">{{
            formatFileSize(selectedPreviewFile.size)
          }}</span
          ><a :href="preview.github" target="_blank" rel="noopener noreferrer" class="market-button"
            ><SvgIcon name="github" :size="14" />查看仓库</a
          >
        </div>
        <div class="m-modal-body">
          <div v-if="preview.loading" class="market-state" role="status">
            <SvgIcon name="refresh" :size="24" class="spinning" />
            <p>正在读取插件文档…</p>
          </div>
          <article v-else-if="previewHtml" class="m-preview-markdown" v-html="previewHtml" />
          <div v-else class="market-state" role="status">
            <span class="market-state-icon"><SvgIcon name="document-text" :size="25" /></span>
            <h3>{{ preview.error ? '文档加载失败' : '暂无插件文档' }}</h3>
            <p>{{ preview.error || '当前目录没有 Markdown 文档，可前往仓库了解更多。' }}</p>
            <a :href="preview.github" target="_blank" rel="noopener noreferrer" class="market-button"
              >前往仓库<SvgIcon name="open-outline" :size="13"
            /></a>
          </div>
        </div>
      </section>
    </NModal>
  </section>
</template>

<style scoped src="./market.css"></style>
