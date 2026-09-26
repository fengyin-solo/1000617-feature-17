# 实验室样品检测平台

面向样品受理、任务派发、检测执行、仪器校准与报告出具的一体化实验室检测管理后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 样品受理 | `sample` | 样品 | 样品编号、样品名称、样品类别 |
| 委托单位 | `client` | 委托单位 | 单位编码、单位名称、单位类型 |
| 检测项目 | `project` | 检测项目 | 项目编码、项目名称、检测方法 |
| 检测任务 | `task` | 检测任务 | 任务编号、关联样品、检测项目 |
| 检测执行 | `execute` | 执行记录 | 记录编号、关联任务、前处理方式 |
| 检测结果 | `result` | 检测结果 | 结果编号、关联任务、检测值 |
| 结果复核 | `review` | 复核记录 | 复核编号、关联结果、复核项目 |
| 仪器设备 | `instrument` | 仪器设备 | 设备编号、设备名称、设备型号 |
| 校准记录 | `calibration` | 校准记录 | 校准编号、关联设备、校准方式 |
| 试剂耗材 | `reagent` | 试剂物料 | 物料编号、物料名称、规格纯度 |
| 耗材领用 | `consume` | 领用单 | 领用单号、物料名称、领用数量 |
| 环境监控 | `environment` | 环境记录 | 记录编号、监控区域、温度值 |
| 报告出具 | `report` | 检测报告 | 报告编号、关联样品、报告类型 |
| 报告变更 | `issue` | 变更记录 | 变更编号、关联报告、变更类型 |
| 质量控制 | `qc` | 质控记录 | 质控编号、质控类型、关联项目 |
| 投诉处理 | `complaint` | 投诉记录 | 投诉编号、投诉单位、投诉事由 |
| 样品流转 | `stockin` | 流转记录 | 流转编号、关联样品、流转环节 |
| 检测结算 | `settlement` | 结算单 | 结算单号、委托单位、结算周期 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

### 耗材领用状态机

领用单按 `待审批 → 已批准 → 已领用` 正向流转，三个状态均可「退回物料」到终态
`已退回`（必须填写退回原因）；已领用不能再次批准，已退回不能直接改为已领用。
审批环节按「同物料 + 同科室 + 同领用日期」汇总在途申领（待审批、已批准），超过
试剂台账结存上限即拦截；确认发放时二次校验库存并扣减试剂物料结存，库存不足只提示
不扣减，退回已领用物料时回补结存。每次状态变化都写入单据的 `流转记录`，并通过
`backend/data/snapshot.json` 落盘（可用环境变量 `LAB_SNAPSHOT_PATH` 改路径），
刷新或重启后流转记录与结存仍然保留；科室统计见 `GET /api/consume/stats`。
