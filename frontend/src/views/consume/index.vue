<template>
  <section class="page" data-module="consume">
    <header class="page-head">
      <div>
        <h2>耗材领用管理</h2>
        <p class="page-desc">领用单按 待审批 → 已批准 → 已领用 流转，退回须填写退回原因；审批与发放都会核验试剂台账库存。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="toggleCreate">登记领用单</button>
        <button class="btn" type="button" @click="exportRows">导出耗材领用清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form v-if="showCreate" class="create-panel" @submit.prevent="submitCreate">
      <label class="filter-item">
        <span>领用单号</span>
        <input v-model="createForm.领用单号" placeholder="如 CONS-0004" required />
      </label>
      <label class="filter-item">
        <span>物料名称</span>
        <select v-model="createForm.物料名称" required>
          <option value="" disabled>请选择台账物料</option>
          <option v-for="name in reagentOptions" :key="name" :value="name">{{ name }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>领用数量</span>
        <input v-model.number="createForm.领用数量" type="number" min="1" step="1" required />
      </label>
      <label class="filter-item">
        <span>领用人员</span>
        <input v-model="createForm.领用人员" placeholder="领用人姓名" required />
      </label>
      <label class="filter-item">
        <span>所属科室</span>
        <input v-model="createForm.所属科室" placeholder="如 理化检验科" required />
      </label>
      <label class="filter-item">
        <span>领用日期</span>
        <input v-model="createForm.领用日期" type="date" required />
      </label>
      <label class="filter-item">
        <span>用途说明</span>
        <input v-model="createForm.用途说明" placeholder="选填" />
      </label>
      <button class="btn primary" type="submit">提交登记</button>
      <button class="btn ghost" type="button" @click="toggleCreate">取消</button>
    </form>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>领用单号</span>
        <input v-model="filters.keyword" placeholder="按领用单号检索" />
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
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="row in rows" :key="String(row.id)">
          <tr>
            <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
            <td class="row-actions">
              <button
                v-for="action in availableActions(row)"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
              <button class="link" type="button" @click="toggleFlow(row)">
                {{ expandedId === row.id ? '收起记录' : '流转记录' }}
              </button>
            </td>
          </tr>
          <tr v-if="expandedId === row.id" class="flow-row">
            <td :colspan="columns.length + 1">
              <table class="data-table flow-table">
                <thead>
                  <tr><th>时间</th><th>动作</th><th>操作人</th><th>从状态</th><th>到状态</th><th>原因</th></tr>
                </thead>
                <tbody>
                  <tr v-for="(record, index) in flowRecords(row)" :key="index">
                    <td>{{ record.时间 }}</td>
                    <td>{{ record.动作 }}</td>
                    <td>{{ record.操作人 }}</td>
                    <td>{{ record.从状态 || '—' }}</td>
                    <td>{{ record.到状态 }}</td>
                    <td>{{ record.原因 || '—' }}</td>
                  </tr>
                  <tr v-if="!flowRecords(row).length">
                    <td colspan="6" class="empty-state">暂无流转记录</td>
                  </tr>
                </tbody>
              </table>
            </td>
          </tr>
        </template>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无耗材领用数据，可先登记领用单</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">科室领用统计</h3>
    <table class="data-table">
      <thead>
        <tr><th v-for="column in deptColumns" :key="column">{{ column }}</th></tr>
      </thead>
      <tbody>
        <tr v-for="dept in departments" :key="String(dept.所属科室)">
          <td v-for="column in deptColumns" :key="column">{{ dept[column] }}</td>
        </tr>
        <tr v-if="!departments.length">
          <td :colspan="deptColumns.length" class="empty-state">暂无科室领用数据</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条耗材领用记录</span>
      <span v-if="noticeMessage" class="ok-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type FlowRecord = {
  时间: string
  动作: string
  操作人: string
  从状态: string
  到状态: string
  原因: string
}

type Row = {
  id: number | string
  status?: string
  流转记录?: FlowRecord[]
  [key: string]: string | number | null | FlowRecord[] | undefined
}

type DeptStat = Record<string, string | number>

const ENDPOINT = '/api/consume'
const columns = ["领用单号", "物料名称", "领用数量", "领用人员", "领用日期", "用途说明", "所属科室", "领用状态", "退回原因"]
const deptColumns = ["所属科室", "领用单数", "申领合计", "已领用数量", "待审批数", "已退回数"]
const statuses = ["待审批", "已批准", "已领用", "已退回"]
const ACTION_BY_STATUS: Record<string, string[]> = {
  '待审批': ['批准领用', '退回物料'],
  '已批准': ['确认发放', '退回物料'],
  '已领用': ['退回物料'],
  '已退回': [],
}

const session = useSessionStore()

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref({ keyword: '', status: '' })
const stats = ref([{ label: '待审批领用', value: 0 }, { label: '本月领用单', value: 0 }, { label: '退回单数', value: 0 }])
const departments = ref<DeptStat[]>([])
const expandedId = ref<number | string | null>(null)
const showCreate = ref(false)
const reagentOptions = ref<string[]>([])

const blankForm = () => ({
  领用单号: '',
  物料名称: '',
  领用数量: 1,
  领用人员: '',
  所属科室: '',
  领用日期: new Date().toISOString().slice(0, 10),
  用途说明: '',
})
const createForm = ref(blankForm())

function availableActions(row: Row): string[] {
  return ACTION_BY_STATUS[String(row.status ?? '')] ?? []
}

function flowRecords(row: Row): FlowRecord[] {
  return Array.isArray(row.流转记录) ? row.流转记录 : []
}

function toggleFlow(row: Row) {
  expandedId.value = expandedId.value === row.id ? null : row.id
}

function toggleCreate() {
  showCreate.value = !showCreate.value
  if (showCreate.value) {
    createForm.value = blankForm()
  }
}

function resetFilters() {
  filters.value = { keyword: '', status: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  let reason = ''
  if (action === '退回物料') {
    reason = (window.prompt(`请输入领用单 ${row['领用单号']} 的退回原因`) ?? '').trim()
    if (!reason) {
      errorMessage.value = '退回物料必须填写退回原因，未执行退回'
      return
    }
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, 退回原因: reason, 操作人: session.operator } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? '耗材领用动作未生效，请稍后重试')
    }
    noticeMessage.value = payload.message ?? '操作已完成'
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '耗材领用操作失败'
  }
}

async function submitCreate() {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm.value } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? '领用单登记失败')
    }
    noticeMessage.value = payload.message ?? '领用单已登记'
    showCreate.value = false
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '领用单登记失败'
  }
}

async function loadStats() {
  try {
    const payload = await fetchJson<{ summary: { label: string; value: number }[]; departments: DeptStat[] }>(`${ENDPOINT}/stats`)
    stats.value = payload.summary
    departments.value = payload.departments
  } catch {
    // 统计读取失败时保留上一次结果，不打断列表操作
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.value.keyword) query.set('keyword', filters.value.keyword)
  if (filters.value.status) query.set('status', filters.value.status)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('领用单列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '耗材领用列表读取失败'
  }
}

onMounted(async () => {
  await Promise.all([reload(), loadStats()])
  try {
    const payload = await fetchJson<{ items: Row[] }>('/api/reagent?size=200')
    reagentOptions.value = payload.items.map((item) => String(item['物料名称'] ?? '')).filter(Boolean)
  } catch {
    reagentOptions.value = []
  }
})
</script>
