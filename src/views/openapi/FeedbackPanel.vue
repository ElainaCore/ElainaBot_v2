<script>
import { Transition, computed, h, onMounted, onUnmounted, ref, watch } from 'vue'

const PAGE_SIZE = 20;
const RANGE_OPTIONS = [
  { value: 7, label: '近 7 天' },
  { value: 14, label: '近 14 天' },
  { value: 30, label: '近 30 天' },
];
const TYPE_OPTIONS = [
  { value: 0, label: '全部' },
  { value: 1, label: '问题反馈' },
  { value: 2, label: '功能建议' },
];
const STATUS_OPTIONS = [
  { value: -1, label: '全部' },
  { value: 0, label: '未处理' },
  { value: 1, label: '已处理' },
  { value: 2, label: '暂不处理' },
];
const TYPE_LABELS = { 1: '问题反馈', 2: '功能建议' };
const STATUS_LABELS = { 0: '未处理', 1: '已处理', 2: '暂不处理' };
const STATUS_CLASS = { 0: 'is-pending', 1: 'is-done', 2: 'is-deferred' };
const STATUS_ORDER = [0, 1, 2];
// 平台 modify 接口不接受 target_status=0（实测返回 retcode=-999 系统异常），
// 「已处理 / 暂不处理」无法退回「未处理」，对应入口置灰并给出说明。
const LOCKED_STATUSES = new Set([0]);
const LOCKED_STATUS_HINT = '平台不支持将处理状态改回「未处理」';
const LOCKED_ICON = ['M12 8v5', 'M12 16h.01', 'M21 12a9 9 0 1 1-9-9 9 9 0 0 1 9 9z'];
const CHECK_ICON = ['M20 6 9 17l-5-5'];
const CARET_ICON = ['M6 9l6 6 6-6'];
const STATUS_ICONS = {
  0: ['M12 7v5l3 2', 'M21 12a9 9 0 1 1-9-9 9 9 0 0 1 9 9z'],
  1: ['M22 11.1V12a10 10 0 1 1-5.93-9.14', 'M22 4 12 14l-3-3'],
  2: ['M8 12h8', 'M21 12a9 9 0 1 1-9-9 9 9 0 0 1 9 9z'],
};

/* ==================== 工具函数 ==================== */

const pad2 = (value) => (value < 10 ? `0${value}` : String(value));

function formatTime(seconds, withSeconds) {
  const value = Number(seconds) || 0;
  if (!value) return '时间未知';
  const date = new Date(value * 1000);
  const base = `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
  if (!withSeconds) return base;
  return `${base} ${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

function clockLabel() {
  const date = new Date();
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

/** 近 N 天（含今天）的 0 点时间戳（秒） */
function rangeStart(days) {
  const date = new Date();
  date.setHours(0, 0, 0, 0);
  date.setDate(date.getDate() - (Math.max(1, Number(days) || 1) - 1));
  return Math.floor(date.getTime() / 1000);
}

/** 今天 23:59:59 的时间戳（秒） */
function rangeEnd() {
  const date = new Date();
  date.setHours(23, 59, 59, 0);
  return Math.floor(date.getTime() / 1000);
}

function monthDay(seconds) {
  const date = new Date(seconds * 1000);
  return `${pad2(date.getMonth() + 1)}/${pad2(date.getDate())}`;
}

/** 收集反馈截图：images[] 与 chat_record_image 都可能是图片来源 */
function collectImages(raw) {
  const urls = [];
  const push = (candidate) => {
    if (typeof candidate === 'string') {
      if (candidate.trim()) urls.push(candidate.trim());
      return;
    }
    if (candidate && typeof candidate === 'object') {
      const url = candidate.url || candidate.image || candidate.src || candidate.uri;
      if (typeof url === 'string' && url.trim()) urls.push(url.trim());
    }
  };
  if (Array.isArray(raw && raw.images)) raw.images.forEach(push);
  push(raw && raw.chat_record_image);
  return [...new Set(urls)];
}

function normalizeItem(raw) {
  const item = raw && typeof raw === 'object' ? raw : {};
  const status = Number(item.status);
  return {
    id: String(item.id || ''),
    type: Number(item.feedback_type) || 1,
    issueTypes: Array.isArray(item.issue_types) ? item.issue_types.filter(Boolean).map(String) : [],
    description: String(item.description || '').replaceAll('\\n', '\n').trim(),
    images: collectImages(item),
    status: Number.isFinite(status) ? status : 0,
    createTime: Number(item.create_time) || 0,
    openid: String(item.user_openid || ''),
  };
}

/** 反馈类型列：优先展示问题分类，无分类时回退到反馈类型 */
function typeText(item) {
  if (item.issueTypes.length) return item.issueTypes.join('、');
  return TYPE_LABELS[item.type] || '用户反馈';
}

function icon(paths, size, extra) {
  return h(
    'svg',
    Object.assign(
      {
        width: size || 14,
        height: size || 14,
        viewBox: '0 0 24 24',
        fill: 'none',
        stroke: 'currentColor',
        'stroke-width': 1.8,
        'stroke-linecap': 'round',
        'stroke-linejoin': 'round',
        'aria-hidden': 'true',
      },
      extra || {},
    ),
    paths.map((d) => h('path', { d })),
  );
}

/* ==================== 组件 ==================== */

export default {
  name: 'FeedbackPanel',
  props: {
    appid: { type: [String, Number], default: '' },
    request: { type: Function, required: true },
  },
  emits: ['toast'],
  setup(props, { emit }) {
    const loading = ref(false);
    const error = ref('');
    const items = ref([]);
    const total = ref(0);
    const page = ref(1);
    const range = ref(30);
    const typeFilter = ref(0);
    const statusFilter = ref(-1);
    const selected = ref([]);
    const pending = ref({});
    const busy = ref(false);
    const refreshedAt = ref('');
    const modalId = ref('');
    const statusMenuId = ref('');
    const previewUrl = ref('');
    const previewZoom = ref(1);
    const previewOffset = ref({ x: 0, y: 0 });
    const previewDragging = ref(false);
    let previewDragStart = null;

    let seq = 0;

    const pages = computed(() => Math.max(1, Math.ceil((Number(total.value) || 0) / PAGE_SIZE)));
    const selectedSet = computed(() => new Set(selected.value));
    const allSelected = computed(
      () => items.value.length > 0 && items.value.every((item) => selectedSet.value.has(item.id)),
    );
    const modalItem = computed(() => items.value.find((item) => item.id === modalId.value) || null);
    const rangeLabel = computed(() => `${monthDay(rangeStart(range.value))}-${monthDay(rangeEnd())}`);

    const toast = (message) => emit('toast', message);

    /* ---------- 图片预览 ---------- */

    const PREVIEW_MIN_ZOOM = 0.5;
    const PREVIEW_MAX_ZOOM = 4;
    const ZOOM_ICON_PLUS = ['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z', 'M21 21l-4.3-4.3', 'M11 8v6', 'M8 11h6'];
    const ZOOM_ICON_MINUS = ['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z', 'M21 21l-4.3-4.3', 'M8 11h6'];

    const clampZoom = (value) => Math.min(PREVIEW_MAX_ZOOM, Math.max(PREVIEW_MIN_ZOOM, value));

    function openPreview(url) {
      previewUrl.value = url;
      previewZoom.value = 1;
      previewOffset.value = { x: 0, y: 0 };
      previewDragging.value = false;
      previewDragStart = null;
    }

    function closePreview() {
      previewUrl.value = '';
      previewZoom.value = 1;
      previewOffset.value = { x: 0, y: 0 };
      previewDragging.value = false;
      previewDragStart = null;
    }

    function applyZoom(next) {
      previewZoom.value = clampZoom(next);
      if (previewZoom.value <= 1) previewOffset.value = { x: 0, y: 0 };
    }

    const zoomIn = () => applyZoom(previewZoom.value * 1.25);
    const zoomOut = () => applyZoom(previewZoom.value / 1.25);
    const resetZoom = () => {
      previewZoom.value = 1;
      previewOffset.value = { x: 0, y: 0 };
    };
    const toggleZoom = () => (previewZoom.value > 1 ? resetZoom() : applyZoom(2));

    function onPreviewWheel(event) {
      if (!previewUrl.value) return;
      event.preventDefault();
      applyZoom(previewZoom.value * (event.deltaY < 0 ? 1.1 : 1 / 1.1));
    }

    function onPreviewPointerDown(event) {
      if (previewZoom.value <= 1) return;
      event.currentTarget.setPointerCapture?.(event.pointerId);
      previewDragging.value = true;
      previewDragStart = {
        x: event.clientX,
        y: event.clientY,
        ox: previewOffset.value.x,
        oy: previewOffset.value.y,
      };
    }

    function onPreviewPointerMove(event) {
      if (!previewDragging.value || !previewDragStart) return;
      previewOffset.value = {
        x: previewDragStart.ox + (event.clientX - previewDragStart.x),
        y: previewDragStart.oy + (event.clientY - previewDragStart.y),
      };
    }

    function onPreviewPointerUp(event) {
      if (!previewDragging.value) return;
      previewDragging.value = false;
      previewDragStart = null;
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    }

    async function load(resetPage) {
      const mine = ++seq;
      if (resetPage) {
        page.value = 1;
      }
      selected.value = [];
      statusMenuId.value = '';
      const appid = Number(props.appid);
      if (!appid) {
        items.value = [];
        total.value = 0;
        loading.value = false;
        error.value = '缺少机器人 AppID';
        return;
      }
      loading.value = true;
      error.value = '';
      try {
        const payload = {
          bot_appid: appid,
          start_time: rangeStart(range.value),
          end_time: rangeEnd(),
          page: page.value,
          page_size: PAGE_SIZE,
        };
        if (typeFilter.value) payload.feedback_types = [typeFilter.value];
        if (statusFilter.value >= 0) payload.statuses = [statusFilter.value];
        const res = await props.request('/cgi-bin/v2/feedback/list', payload);
        if (mine !== seq) return;
        items.value = (Array.isArray(res && res.list) ? res.list : []).map(normalizeItem);
        total.value = Number((res && res.total) || 0);
        if (page.value > pages.value) {
          page.value = pages.value;
          await load(false);
          return;
        }
        refreshedAt.value = clockLabel();
      } catch (e) {
        if (mine !== seq) return;
        items.value = [];
        total.value = 0;
        error.value = (e && e.message) || '加载用户反馈失败';
      } finally {
        if (mine === seq) loading.value = false;
      }
    }

    async function applyStatus(ids, status) {
      const targets = (Array.isArray(ids) ? ids : []).filter(Boolean);
      if (!targets.length || busy.value || loading.value) return;
      if (LOCKED_STATUSES.has(status)) {
        toast(LOCKED_STATUS_HINT);
        return;
      }
      if (targets.length > 1 && !window.confirm(`确认将选中的 ${targets.length} 条反馈标记为「${STATUS_LABELS[status]}」？`)) {
        return;
      }
      const appid = Number(props.appid);
      if (!appid) {
        toast('缺少机器人 AppID');
        return;
      }
      busy.value = true;
      pending.value = Object.fromEntries(targets.map((id) => [id, true]));
      try {
        const res = await props.request('/cgi-bin/v2/feedback/modify', {
          bot_appid: appid,
          ids: targets,
          target_status: status,
        });
        if (Number(props.appid) !== appid) return;
        const raw = res ? Number(res.success_count) : NaN;
        const count = Number.isFinite(raw) ? Math.max(0, Math.min(targets.length, raw)) : null;
        // The count cannot identify which feedback changed. Reload the actual statuses.
        await load(false);
        if (!modalItem.value) modalId.value = '';
        toast(
          count === null
            ? '处理状态请求已提交，请以刷新后的列表为准'
            : count === 0
              ? '处理状态未发生变化，请重试'
              : count < targets.length
                ? `已将 ${count} 条反馈标记为「${STATUS_LABELS[status]}」，其余反馈未更新`
                : `已将 ${count} 条反馈标记为「${STATUS_LABELS[status]}」`,
        );
      } catch (e) {
        toast((e && e.message) || '修改处理状态失败，请重试');
      } finally {
        busy.value = false;
        pending.value = {};
      }
    }

    function go(target) {
      const next = Math.min(Math.max(1, Number(target) || 1), pages.value);
      if (next === page.value) return;
      page.value = next;
      selected.value = [];
      load(false);
    }

    function toggleSelect(id, checked) {
      const set = new Set(selected.value);
      if (checked) set.add(id);
      else set.delete(id);
      selected.value = [...set];
    }

    const selectPage = () => {
      selected.value = items.value.map((item) => item.id);
    };
    const clearSelected = () => {
      selected.value = [];
    };
    const openDetail = (item) => {
      modalId.value = item.id;
    };
    const closeDetail = () => {
      modalId.value = '';
      closePreview();
    };

    /* ---------- 通用渲染片段 ---------- */

    function renderSelect(options, current, onChange, label) {
      return h('div', { class: 'fbp-select-wrap' }, [
        h(
          'select',
          { class: 'fbp-select', 'aria-label': label, onChange },
          options.map((option) =>
            h('option', { value: option.value, selected: option.value === current }, option.label),
          ),
        ),
        icon(['M6 9l6 6 6-6'], 14, { class: 'fbp-select-chevron' }),
      ]);
    }

    const toggleStatusMenu = (id) => {
      statusMenuId.value = statusMenuId.value === id ? '' : id;
    };
    const closeStatusMenu = () => {
      statusMenuId.value = '';
    };

    function renderStatusMenu(item, placement) {
      return h(
        'div',
        {
          class: ['fbp-menu', placement === 'up' ? 'is-up' : 'is-down'],
          role: 'menu',
          'aria-label': '处理状态',
        },
        STATUS_ORDER.map((status) => {
          const locked = LOCKED_STATUSES.has(status);
          const current = item.status === status;
          return h(
            'button',
            {
              type: 'button',
              role: 'menuitemradio',
              'aria-checked': current ? 'true' : 'false',
              class: ['fbp-menu-item', { 'is-current': current, 'is-locked': locked }],
              disabled: locked || busy.value || loading.value,
              title: locked ? LOCKED_STATUS_HINT : `将处理状态修改为「${STATUS_LABELS[status]}」`,
              onClick: (e) => {
                e.stopPropagation();
                closeStatusMenu();
                if (current || locked) return;
                applyStatus([item.id], status);
              },
            },
            [
              icon(locked ? LOCKED_ICON : STATUS_ICONS[status] || STATUS_ICONS[2], 14, { class: 'fbp-menu-icon' }),
              h('span', { class: 'fbp-menu-label' }, STATUS_LABELS[status]),
              current ? icon(CHECK_ICON, 14, { class: 'fbp-menu-check' }) : null,
            ],
          );
        }),
      );
    }

    function renderStatusCell(item, index) {
      const open = statusMenuId.value === item.id;
      // 靠底部的行向上展开，避免菜单被容器裁掉
      const placement = index >= items.value.length - 2 ? 'up' : 'down';
      return h(
        'div',
        { class: 'fbp-cell-status', role: 'cell', onClick: (e) => e.stopPropagation() },
        [
          h('div', { class: ['fbp-status-wrap', { 'is-open': open }] }, [
          h(
            'button',
            {
              type: 'button',
              class: ['fbp-pill', STATUS_CLASS[item.status] || 'is-deferred', { 'is-open': open }],
              'aria-haspopup': 'menu',
              'aria-expanded': open ? 'true' : 'false',
              title: '点击修改处理状态',
              onClick: (e) => {
                e.stopPropagation();
                toggleStatusMenu(item.id);
              },
              onKeydown: (e) => {
                if (e.key === 'Escape' && open) {
                  e.stopPropagation();
                  closeStatusMenu();
                }
              },
            },
            [
              icon(STATUS_ICONS[item.status] || STATUS_ICONS[2], 13),
              h('span', null, STATUS_LABELS[item.status] || `状态 ${item.status}`),
              icon(CARET_ICON, 11, { class: 'fbp-pill-caret' }),
            ],
          ),
          h(
            Transition,
            { name: 'fbp-pop' },
            { default: () => (open ? renderStatusMenu(item, placement) : null) },
          ),
          ]),
        ],
      );
    }

    /* ---------- 头部 / 筛选 ---------- */

    function renderHead() {
      return h('header', { class: 'fbp-head' }, [
        h('div', { class: 'fbp-head-text' }, [
          h('h2', { class: 'fbp-title' }, '用户反馈'),
          h('p', { class: 'fbp-sub' }, '用户通过机器人提交的问题与建议，点击单条反馈可查看详情并更新处理状态。'),
        ]),
        h('div', { class: 'fbp-head-actions' }, [
          refreshedAt.value ? h('span', { class: 'fbp-updated' }, `更新于 ${refreshedAt.value}`) : null,
          h(
            'button',
            { type: 'button', class: 'btn ghost small', disabled: loading.value || busy.value, onClick: () => load(false) },
            loading.value ? '刷新中…' : '刷新',
          ),
        ]),
      ]);
    }

    function renderFilters() {
      const ranges = h(
        'div',
        { class: 'fbp-range', tabindex: '0' },
        [
          h('span', { class: 'fbp-range-text' }, rangeLabel.value),
          icon(['M8 2v4', 'M16 2v4', 'M3 9h18', 'M5 4h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z'], 15, { class: 'fbp-range-icon' }),
          h(
            'div',
            { class: 'fbp-range-menu', role: 'menu' },
            RANGE_OPTIONS.map((option) =>
              h(
                'button',
                {
                  type: 'button',
                  role: 'menuitem',
                  class: ['fbp-range-item', { 'is-active': option.value === range.value }],
                  onClick: (e) => {
                    e.currentTarget.blur();
                    if (option.value !== range.value) {
                      range.value = option.value;
                      load(true);
                    }
                  },
                },
                option.label,
              ),
            ),
          ),
        ],
      );

      return h('div', { class: 'fbp-filters' }, [
        h('div', { class: 'fbp-filter' }, [h('span', { class: 'fbp-filter-label' }, '时间'), ranges]),
        h('div', { class: 'fbp-filter' }, [
          h('span', { class: 'fbp-filter-label' }, '类型'),
          renderSelect(TYPE_OPTIONS, typeFilter.value, (e) => {
            typeFilter.value = Number(e.target.value);
            load(true);
          }, '反馈类型'),
        ]),
        h('div', { class: 'fbp-filter' }, [
          h('span', { class: 'fbp-filter-label' }, '状态'),
          renderSelect(STATUS_OPTIONS, statusFilter.value, (e) => {
            statusFilter.value = Number(e.target.value);
            load(true);
          }, '处理状态'),
        ]),
        loading.value ? h('span', { class: 'fbp-loading-hint' }, '加载中…') : null,
      ]);
    }

    /* ---------- 批量操作 ---------- */

    function renderBatchBar() {
      const count = selected.value.length;
      if (!count) return null;
      return h('div', { class: 'fbp-batch' }, [
        h('span', { class: 'fbp-batch-count' }, `已选择 ${count} 条`),
        h('div', { class: 'fbp-batch-actions', role: 'group', 'aria-label': '批量修改处理状态' }, [
          h('span', { class: 'fbp-batch-label' }, '标记为'),
          ...STATUS_ORDER.map((status) => {
            const locked = LOCKED_STATUSES.has(status);
            return h(
              'button',
              {
                type: 'button',
                class: ['fbp-act', { 'is-locked': locked }],
                disabled: locked || loading.value || busy.value,
                title: locked ? LOCKED_STATUS_HINT : `将选中的反馈标记为「${STATUS_LABELS[status]}」`,
                onClick: () => applyStatus(selected.value.slice(), status),
              },
              STATUS_LABELS[status],
            );
          }),
          h('button', { type: 'button', class: 'btn ghost small', disabled: busy.value, onClick: clearSelected }, '取消选择'),
        ]),
      ]);
    }

    /* ---------- 列表 ---------- */

    function renderThead() {
      return h('div', { class: 'fbp-thead', role: 'row' }, [
        h(
          'label',
          { class: 'fbp-cell-check' },
          h('input', {
            type: 'checkbox',
            class: 'fbp-check',
            'aria-label': '全选本页',
            checked: allSelected.value,
            disabled: !items.value.length || loading.value || busy.value,
            onChange: (e) => (e.target.checked ? selectPage() : clearSelected()),
          }),
        ),
        h('span', { class: 'fbp-cell-content', role: 'columnheader' }, '反馈内容'),
        h('span', { class: 'fbp-cell-type', role: 'columnheader' }, '反馈类型'),
        h('span', { class: 'fbp-cell-status', role: 'columnheader' }, '处理状态'),
        h('span', { class: 'fbp-cell-time', role: 'columnheader' }, '反馈时间'),
      ]);
    }

    function renderRow(item, index) {
      const hasShots = item.images.length > 0;
      return h(
        'div',
        {
          key: item.id,
          class: ['fbp-row', { 'is-active': modalId.value === item.id }],
          role: 'row',
          tabindex: '0',
          'aria-label': `查看反馈详情：${item.description || typeText(item)}`,
          onClick: () => openDetail(item),
          onKeydown: (e) => {
            if (e.target !== e.currentTarget) return;
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              openDetail(item);
            }
          },
        },
        [
          h(
            'label',
            { class: 'fbp-cell-check', onClick: (e) => e.stopPropagation() },
            h('input', {
              type: 'checkbox',
              class: 'fbp-check',
              'aria-label': '选择该反馈',
              checked: selectedSet.value.has(item.id),
              disabled: busy.value,
              onChange: (e) => toggleSelect(item.id, e.target.checked),
            }),
          ),
          h('div', { class: 'fbp-cell-content', role: 'cell' }, [
            h('span', { class: 'fbp-row-text' }, item.description || typeText(item)),
            hasShots
              ? icon(['M21 11.5a8.4 8.4 0 0 1-9 8.4 8.9 8.9 0 0 1-3.8-.9L3 21l1.9-3.7A8.4 8.4 0 0 1 3 11.5a8.4 8.4 0 0 1 9-8.4 8.4 8.4 0 0 1 9 8.4z'], 13, { class: 'fbp-row-flag' })
              : null,
          ]),
          h('span', { class: 'fbp-cell-type', role: 'cell' }, typeText(item)),
          renderStatusCell(item, index),
          h('span', { class: 'fbp-cell-time', role: 'cell' }, formatTime(item.createTime)),
        ],
      );
    }

    function renderList() {
      if (error.value) {
        return h('div', { class: 'fbp-state is-error' }, [
          h('span', { class: 'fbp-state-title' }, error.value),
          h(
            'button',
            { type: 'button', class: 'btn ghost small', disabled: loading.value, onClick: () => load(false) },
            '重新加载',
          ),
        ]);
      }
      if (loading.value && !items.value.length) {
        return h('div', { class: 'fbp-state' }, [h('span', { class: 'fbp-state-title' }, '加载中…')]);
      }
      if (!items.value.length) {
        return h('div', { class: 'fbp-state is-empty' }, [
          h('span', { class: 'fbp-state-title' }, '暂无用户反馈'),
          h('span', { class: 'fbp-state-sub' }, '用户可在机器人资料页提交问题反馈与功能建议，收到后会展示在这里。'),
        ]);
      }
      return h('div', { class: ['fbp-table', { 'is-loading': loading.value }], role: 'table', 'aria-label': '用户反馈列表' }, [
        renderThead(),
        ...items.value.map((item, index) => renderRow(item, index)),
      ]);
    }

    /* ---------- 分页 ---------- */

    function renderPager() {
      const numbers = [];
      const last = pages.value;
      const start = Math.max(1, Math.min(page.value - 2, last - 4));
      for (let index = start; index <= Math.min(last, start + 4); index += 1) numbers.push(index);
      return h('div', { class: 'fbp-foot' }, [
        h('span', { class: 'fbp-foot-total' }, `共 ${total.value} 条反馈`),
        h('div', { class: 'fbp-pager', role: 'navigation', 'aria-label': '分页' }, [
          h(
            'button',
            {
              type: 'button',
              class: 'fbp-page-arrow',
              'aria-label': '上一页',
              disabled: page.value <= 1 || loading.value || busy.value,
              onClick: () => go(page.value - 1),
            },
            icon(['M15 18l-6-6 6-6'], 15),
          ),
          ...numbers.map((number) =>
            h(
              'button',
              {
                type: 'button',
                class: ['fbp-page-num', { 'is-active': number === page.value }],
                'aria-current': number === page.value ? 'page' : undefined,
                disabled: loading.value || busy.value,
                onClick: () => go(number),
              },
              String(number),
            ),
          ),
          h(
            'button',
            {
              type: 'button',
              class: 'fbp-page-arrow',
              'aria-label': '下一页',
              disabled: page.value >= pages.value || loading.value || busy.value,
              onClick: () => go(page.value + 1),
            },
            icon(['M9 18l6-6-6-6'], 15),
          ),
        ]),
      ]);
    }

    /* ---------- 详情弹窗 ---------- */

    function renderDetailField(label, value) {
      return h('div', { class: 'fbp-detail-field' }, [
        h('span', { class: 'fbp-detail-label' }, label),
        h('span', { class: 'fbp-detail-value' }, value || '—'),
      ]);
    }

    function renderDetail() {
      const item = modalItem.value;
      if (!item) return null;
      const shots = item.images.map((url, index) =>
        h(
          'button',
          {
            type: 'button',
            class: 'fbp-detail-shot',
            title: '查看大图',
            'aria-label': `查看第 ${index + 1} 张对话截图`,
            onClick: (e) => {
              e.stopPropagation();
              openPreview(url);
            },
          },
          h('img', { src: url, alt: `对话信息截图 ${index + 1}`, loading: 'lazy' }),
        ),
      );

      return h(
        'div',
        {
          class: 'fbp-modal-mask',
          onClick: (e) => {
            if (e.target === e.currentTarget) closeDetail();
          },
        },
        [
          h('section', { class: 'fbp-modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': '反馈详情' }, [
            h('header', { class: 'fbp-modal-head' }, [
              h('h3', { class: 'fbp-modal-title' }, '反馈详情'),
              h(
                'button',
                { type: 'button', class: 'fbp-modal-close', 'aria-label': '关闭', onClick: closeDetail },
                icon(['M18 6 6 18', 'M6 6l12 12'], 18),
              ),
            ]),
            h('div', { class: 'fbp-detail' }, [
              h('div', { class: 'fbp-detail-grid' }, [
                renderDetailField('用户 OpenID', item.openid),
                renderDetailField('反馈类型', typeText(item)),
                renderDetailField('反馈时间', formatTime(item.createTime, true)),
                renderDetailField('处理状态', STATUS_LABELS[item.status] || `状态 ${item.status}`),
              ]),
              h('div', { class: 'fbp-detail-block' }, [
                h('h4', { class: 'fbp-detail-title' }, '反馈内容'),
                h('p', { class: 'fbp-detail-text' }, item.description || '用户未填写文字描述'),
              ]),
              item.images.length
                ? h('div', { class: 'fbp-detail-block' }, [
                    h('h4', { class: 'fbp-detail-title' }, '对话信息'),
                    h('div', { class: 'fbp-detail-shots' }, shots),
                  ])
                : null,
            ]),
            h('footer', { class: 'fbp-modal-foot' }, [
              h('span', { class: 'fbp-modal-foot-label' }, '修改处理状态'),
              h(
                'div',
                { class: 'fbp-modal-actions', role: 'group', 'aria-label': '修改处理状态' },
                STATUS_ORDER.map((status) => {
                  const locked = LOCKED_STATUSES.has(status);
                  const current = item.status === status;
                  return h(
                    'button',
                    {
                      type: 'button',
                      class: ['fbp-act', { 'is-current': current, 'is-locked': locked }],
                      disabled: current || locked || loading.value || busy.value || Boolean(pending.value[item.id]),
                      title: locked
                        ? LOCKED_STATUS_HINT
                        : current
                          ? '当前处理状态'
                          : `将处理状态修改为「${STATUS_LABELS[status]}」`,
                      onClick: () => applyStatus([item.id], status),
                    },
                    STATUS_LABELS[status],
                  );
                }),
              ),
            ]),
          ]),
        ],
      );
    }

    function renderPreview() {
      if (!previewUrl.value) return null;
      const percent = `${Math.round(previewZoom.value * 100)}%`;
      return h(
        'div',
        {
          class: 'fbp-preview',
          role: 'dialog',
          'aria-modal': 'true',
          'aria-label': '图片预览',
          onClick: (e) => {
            if (e.target === e.currentTarget) closePreview();
          },
          onWheel: onPreviewWheel,
        },
        [
          h('section', { class: 'fbp-preview-panel', onClick: (e) => e.stopPropagation() }, [
            h('header', { class: 'fbp-preview-head' }, [
              h('h3', { class: 'fbp-preview-title' }, '图片预览'),
              h('div', { class: 'fbp-preview-zoom', role: 'group', 'aria-label': '缩放' }, [
                h(
                  'button',
                  {
                    type: 'button',
                    class: 'fbp-preview-zoom-btn',
                    'aria-label': '缩小',
                    disabled: previewZoom.value <= PREVIEW_MIN_ZOOM,
                    onClick: zoomOut,
                  },
                  icon(ZOOM_ICON_MINUS, 16),
                ),
                h(
                  'button',
                  { type: 'button', class: 'fbp-preview-percent', title: '恢复 100%', onClick: resetZoom },
                  percent,
                ),
                h(
                  'button',
                  {
                    type: 'button',
                    class: 'fbp-preview-zoom-btn',
                    'aria-label': '放大',
                    disabled: previewZoom.value >= PREVIEW_MAX_ZOOM,
                    onClick: zoomIn,
                  },
                  icon(ZOOM_ICON_PLUS, 16),
                ),
              ]),
              h(
                'button',
                { type: 'button', class: 'fbp-preview-close', 'aria-label': '关闭预览', onClick: closePreview },
                icon(['M18 6 6 18', 'M6 6l12 12'], 18),
              ),
            ]),
            h('div', { class: 'fbp-preview-body' }, [
              h('img', {
                class: [
                  'fbp-preview-img',
                  { 'is-zoomed': previewZoom.value > 1, 'is-dragging': previewDragging.value },
                ],
                src: previewUrl.value,
                alt: '反馈截图预览',
                draggable: 'false',
                style: {
                  transform: `translate(${previewOffset.value.x}px, ${previewOffset.value.y}px) scale(${previewZoom.value})`,
                },
                onDblclick: toggleZoom,
                onPointerdown: onPreviewPointerDown,
                onPointermove: onPreviewPointerMove,
                onPointerup: onPreviewPointerUp,
                onPointercancel: onPreviewPointerUp,
              }),
            ]),
          ]),
        ],
      );
    }

    const onDocumentPointerDown = (event) => {
      if (!statusMenuId.value) return;
      const target = event.target;
      if (target instanceof Element && target.closest('.fbp-status-wrap')) return;
      closeStatusMenu();
    };
    const onDocumentKeydown = (event) => {
      if (event.key !== 'Escape') return;
      if (previewUrl.value) {
        closePreview();
        return;
      }
      if (statusMenuId.value) closeStatusMenu();
    };

    onMounted(() => {
      document.addEventListener('pointerdown', onDocumentPointerDown, true);
      document.addEventListener('keydown', onDocumentKeydown);
    });

    onUnmounted(() => {
      seq += 1;
      document.removeEventListener('pointerdown', onDocumentPointerDown, true);
      document.removeEventListener('keydown', onDocumentKeydown);
    });

    watch(
      () => props.appid,
      () => {
        selected.value = [];
        modalId.value = '';
        load(true);
      },
      { immediate: true },
    );

    return () =>
      h('section', { class: 'fbp', 'aria-label': '用户反馈' }, [
        renderHead(),
        h('div', { class: 'fbp-card' }, [
          h('h3', { class: 'fbp-card-title' }, '用户反馈列表'),
          renderFilters(),
          renderBatchBar(),
          renderList(),
          error.value || (!items.value.length && !loading.value) ? null : renderPager(),
        ]),
        renderDetail(),
        renderPreview(),
      ]);
  },
};
</script>

<style src="./feedback-panel.css"></style>
