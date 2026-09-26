<template>
  <section class="page" data-module="consume">
    <header class="page-head">
      <div>
        <h2>耗材领用管理</h2>
        <p class="page-desc">领用单按「待审批 → 已批准 → 已领用」流转，可退回并留痕；审批环节校验同科室同时段库存上限，发放环节联动试剂物料结存。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记领用单</button>
        <button class="btn" type="button" @click="exportRows">导出耗材领用清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>领用单号</span>
        <input v-model="filters.keyword" placeholder="按领用单号检索" />
      </label>
      <label class="filter-item">
        <span>所属科室</span>
        <input v-model="filters.dept" placeholder="按所属科室检索" />
      </label>
      <label class="filter-item">
        <span>领用状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th>领用单号</th>
          <th>物料名称</th>
          <th>领用数量</th>
          <th>领用人员</th>
          <th>所属科室</th>
          <th>领用日期</th>
          <th>领用状态</th>
          <th>流转记录</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td>{{ row['领用单号'] ?? '—' }}</td>
          <td>{{ row['物料名称'] ?? '—' }}</td>
          <td>{{ row['领用数量'] ?? '—' }}</td>
          <td>{{ row['领用人员'] ?? '—' }}</td>
          <td>{{ row['所属科室'] ?? '—' }}</td>
          <td>{{ row['领用日期'] ?? '—' }}</td>
          <td>
            <span class="status-tag" :class="tagClass(String(row.status))">{{ row.status }}</span>
            <span v-if="row.status === '已退回' && row['退回原因']" class="cell-sub">原因：{{ row['退回原因'] }}</span>
          </td>
          <td>
            <button class="link" type="button" @click="openHistory(row)">查看（{{ historyCount(row) }}）</button>
          </td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(String(row.status))"
              :key="action"
              class="link"
              :class="{ 'return-link': action === '退回物料' }"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <span v-if="!availableActions(String(row.status)).length" class="cell-sub">无</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="9" class="empty-state">暂无耗材领用数据，可先登记领用单</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条耗材领用记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <h3 class="section-title">科室领用统计</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>所属科室</th>
          <th>申领总单数</th>
          <th>待审批</th>
          <th>已批准待发放</th>
          <th>已领用</th>
          <th>已退回</th>
          <th>累计已领用量</th>
          <th>累计退回数量</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="dept in departments" :key="dept['所属科室']">
          <td>{{ dept['所属科室'] }}</td>
          <td>{{ dept['申领总数'] }}</td>
          <td>{{ dept['待审批'] }}</td>
          <td>{{ dept['已批准'] }}</td>
          <td>{{ dept['已领用'] }}</td>
          <td>{{ dept['已退回'] }}</td>
          <td>{{ dept['已领用量'] }}</td>
          <td>{{ dept['退回数量'] }}</td>
        </tr>
        <tr v-if="!departments.length">
          <td colspan="8" class="empty-state">暂无科室统计数据</td>
        </tr>
      </tbody>
    </table>

    <!-- 登记领用单 -->
    <div v-if="createOpen" class="modal-mask" @click.self="closeCreate">
      <div class="modal-card">
        <div class="modal-head">
          <h3>登记领用单</h3>
          <button class="modal-close" type="button" @click="closeCreate">×</button>
        </div>
        <div class="modal-body">
          <div class="form-grid">
            <div class="form-field full">
              <label>物料名称 *</label>
              <input v-model="form.物料名称" list="reagent-options" placeholder="从试剂台账选择或直接输入" />
              <datalist id="reagent-options">
                <option v-for="item in reagentOptions" :key="String(item.id)" :value="item['物料名称']">
                  结存 {{ item['结存数量'] }}
                </option>
              </datalist>
              <p class="form-tip">审批时以试剂台账结存为库存上限；台账中不存在的物料无法通过审批。</p>
            </div>
            <div class="form-field">
              <label>领用数量 *</label>
              <input v-model.number="form.领用数量" type="number" min="1" step="1" placeholder="大于 0 的整数" />
            </div>
            <div class="form-field">
              <label>所属科室 *</label>
              <input v-model="form.所属科室" placeholder="如：检验科" />
            </div>
            <div class="form-field">
              <label>领用人员 *</label>
              <input v-model="form.领用人员" :placeholder="session.operator" />
            </div>
            <div class="form-field">
              <label>领用日期</label>
              <input v-model="form.领用日期" type="date" />
            </div>
            <div class="form-field full">
              <label>用途说明</label>
              <textarea v-model="form.用途说明" placeholder="简要说明申领用途"></textarea>
            </div>
          </div>
          <p v-if="formError" class="form-tip error-text">{{ formError }}</p>
        </div>
        <div class="modal-foot">
          <button class="btn" type="button" @click="closeCreate">取消</button>
          <button class="btn primary" type="button" :disabled="submitting" @click="submitCreate">提交登记</button>
        </div>
      </div>
    </div>

    <!-- 退回原因 -->
    <div v-if="returnOpen" class="modal-mask" @click.self="closeReturn">
      <div class="modal-card">
        <div class="modal-head">
          <h3>退回物料 · {{ returnTarget?.['领用单号'] }}</h3>
          <button class="modal-close" type="button" @click="closeReturn">×</button>
        </div>
        <div class="modal-body">
          <p class="form-tip">
            当前状态：{{ returnTarget?.status }}。退回后单据进入「已退回」终态，
            <template v-if="returnTarget?.status === '已领用'">已扣减的结存将按数量回补，</template>
            退回原因会写入流转记录长期保留。
          </p>
          <div class="form-field">
            <label>退回原因 *</label>
            <textarea v-model="returnReason" placeholder="请填写退回原因，如：物料规格不符、未拆封整批退回等"></textarea>
          </div>
          <p v-if="returnError" class="form-tip error-text">{{ returnError }}</p>
        </div>
        <div class="modal-foot">
          <button class="btn" type="button" @click="closeReturn">取消</button>
          <button class="btn primary" type="button" :disabled="submitting" @click="confirmReturn">确认退回</button>
        </div>
      </div>
    </div>

    <!-- 流转记录 -->
    <div v-if="historyOpen" class="modal-mask" @click.self="closeHistory">
      <div class="modal-card wide">
        <div class="modal-head">
          <h3>流转记录 · {{ historyTarget?.['领用单号'] }}</h3>
          <button class="modal-close" type="button" @click="closeHistory">×</button>
        </div>
        <div class="modal-body">
          <div class="form-grid">
            <div class="form-field"><label>物料名称</label><div>{{ historyTarget?.['物料名称'] }}</div></div>
            <div class="form-field"><label>领用数量</label><div>{{ historyTarget?.['领用数量'] }}</div></div>
            <div class="form-field"><label>领用人员</label><div>{{ historyTarget?.['领用人员'] }}</div></div>
            <div class="form-field"><label>所属科室</label><div>{{ historyTarget?.['所属科室'] }}</div></div>
            <div class="form-field"><label>领用日期</label><div>{{ historyTarget?.['领用日期'] }}</div></div>
            <div class="form-field"><label>当前状态</label><div>{{ historyTarget?.status }}</div></div>
            <div class="form-field full"><label>用途说明</label><div>{{ historyTarget?.['用途说明'] || '—' }}</div></div>
            <div v-if="historyTarget?.['退回原因']" class="form-field full">
              <label>退回原因</label>
              <div class="error-text">{{ historyTarget?.['退回原因'] }}</div>
            </div>
          </div>

          <h4 class="section-title">状态流转</h4>
          <ol class="timeline">
            <li
              v-for="(record, index) in historyRecords(historyTarget)"
              :key="index"
              class="timeline-item"
              :class="{ 'is-return': record['动作'] === '退回物料' }"
            >
              <div class="timeline-title">
                {{ record['动作'] }}：{{ record.from || '—' }} → {{ record.to }}
              </div>
              <div class="timeline-meta">{{ record['时间'] }} · {{ record['操作人'] || '—' }}</div>
              <div v-if="record['备注']" class="timeline-meta">备注：{{ record['备注'] }}</div>
              <div v-if="record['原因']" class="timeline-reason">原因：{{ record['原因'] }}</div>
            </li>
          </ol>
        </div>
        <div class="modal-foot">
          <button class="btn primary" type="button" @click="closeHistory">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | null>
type HistoryRecord = { 动作: string; from: string | null; to: string; 原因: string; 操作人: string; 时间: string; 备注: string }

const ENDPOINT = '/api/consume'
const REAGENT_ENDPOINT = '/api/reagent'
const statuses = ['待审批', '已批准', '已领用', '已退回']
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  待审批: ['批准领用', '退回物料'],
  已批准: ['确认发放', '退回物料'],
  已领用: ['退回物料'],
  已退回: [],
}

const session = useSessionStore()

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const submitting = ref(false)
const filters = reactive<Record<string, string>>({ keyword: '', dept: '', status: '' })
const departments = ref<Record<string, string | number>[]>([])

const statCards = computed(() => [
  { label: '待审批', value: summaryValue('待审批单数') },
  { label: '已批准待发放', value: summaryValue('已批准待发放') },
  { label: '已领用', value: summaryValue('已领用单数') },
  { label: '已退回', value: summaryValue('已退回单数') },
  { label: '累计已领用量', value: summaryValue('累计已领用量') },
  { label: '累计退回数量', value: summaryValue('累计退回数量') },
])
const summary = ref<Record<string, number>>({})
function summaryValue(key: string): number {
  return summary.value[key] ?? 0
}

// ---------- 登记弹窗 ----------
const createOpen = ref(false)
const formError = ref('')
const reagentOptions = ref<Row[]>([])
const emptyForm = () => ({ 物料名称: '', 领用数量: '' as number | '', 所属科室: '', 领用人员: '', 领用日期: '', 用途说明: '' })
const form = reactive(emptyForm())

function openCreate() {
  Object.assign(form, emptyForm())
  formError.value = ''
  createOpen.value = true
}
function closeCreate() {
  createOpen.value = false
}

async function submitCreate() {
  formError.value = ''
  const values = {
    物料名称: form.物料名称.trim(),
    领用数量: form.领用数量,
    所属科室: form.所属科室.trim(),
    领用人员: form.领用人员.trim() || session.operator,
    领用日期: form.领用日期,
    用途说明: form.用途说明.trim(),
  }
  submitting.value = true
  try {
    const result = await postAction('', values)
    if (!result.ok) {
      formError.value = result.message
      return
    }
    closeCreate()
    await reload()
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '领用单登记失败'
  } finally {
    submitting.value = false
  }
}

// ---------- 退回弹窗 ----------
const returnOpen = ref(false)
const returnTarget = ref<Row | null>(null)
const returnReason = ref('')
const returnError = ref('')

function closeReturn() {
  returnOpen.value = false
  returnTarget.value = null
  returnReason.value = ''
  returnError.value = ''
}

async function confirmReturn() {
  if (!returnTarget.value) return
  returnError.value = ''
  submitting.value = true
  try {
    const result = await postAction(`/${returnTarget.value.id}/actions`, {
      action: '退回物料',
      reason: returnReason.value.trim(),
      operator: session.operator,
    })
    if (!result.ok) {
      returnError.value = result.message
      return
    }
    closeReturn()
    await reload()
  } catch (error) {
    returnError.value = error instanceof Error ? error.message : '退回操作失败'
  } finally {
    submitting.value = false
  }
}

// ---------- 流转记录弹窗 ----------
const historyOpen = ref(false)
const historyTarget = ref<Row | null>(null)

function openHistory(row: Row) {
  historyTarget.value = row
  historyOpen.value = true
}
function closeHistory() {
  historyOpen.value = false
  historyTarget.value = null
}
function historyRecords(row: Row | null): HistoryRecord[] {
  const records = row?.['流转记录']
  return Array.isArray(records) ? (records as HistoryRecord[]) : []
}
function historyCount(row: Row): number {
  return historyRecords(row).length
}

// ---------- 列表动作 ----------
function availableActions(status: string): string[] {
  return ACTIONS_BY_STATUS[status] ?? []
}

function tagClass(status: string): string {
  return {
    待审批: 'tag-pending',
    已批准: 'tag-approved',
    已领用: 'tag-issued',
    已退回: 'tag-returned',
  }[status] ?? ''
}

async function postAction(path: string, values: Record<string, unknown>): Promise<{ ok: boolean; message: string }> {
  const response = await request(`${ENDPOINT}${path}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok || !payload) {
    throw new Error('耗材领用动作未生效，请稍后重试')
  }
  return { ok: Boolean(payload.ok), message: String(payload.message ?? '') }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  if (action === '退回物料') {
    returnTarget.value = row
    returnReason.value = ''
    returnError.value = ''
    returnOpen.value = true
    return
  }
  try {
    const result = await postAction(`/${row.id}/actions`, { action, operator: session.operator })
    if (!result.ok) {
      errorMessage.value = result.message
      return
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '耗材领用操作失败'
  }
}

function resetFilters() {
  filters.keyword = ''
  filters.dept = ''
  filters.status = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.keyword) query.set('keyword', filters.keyword)
  if (filters.dept) query.set('dept', filters.dept)
  if (filters.status) query.set('status', filters.status)
  try {
    const [listResp, statsResp, reagentResp] = await Promise.all([
      request(`${ENDPOINT}?${query.toString()}`),
      request(`${ENDPOINT}/stats`),
      request(`${REAGENT_ENDPOINT}?size=200`),
    ])
    if (!listResp.ok) throw new Error('领用单列表读取失败')
    if (!statsResp.ok) throw new Error('科室统计读取失败')
    const listPayload = await listResp.json()
    const statsPayload = await statsResp.json()
    rows.value = listPayload.items ?? []
    total.value = listPayload.total ?? rows.value.length
    summary.value = statsPayload.summary ?? {}
    departments.value = statsPayload.departments ?? []
    if (reagentResp.ok) {
      const reagentPayload = await reagentResp.json().catch(() => null)
      reagentOptions.value = reagentPayload?.items ?? []
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '耗材领用列表读取失败'
  }
}

onMounted(reload)
</script>
