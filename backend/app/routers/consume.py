"""耗材领用接口：维护领用单，覆盖批准领用、确认发放、退回物料等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.consume import ConsumeService

router = APIRouter(prefix="/api/consume", tags=["耗材领用"])

service = ConsumeService()

LIST_FIELDS = ["领用单号", "物料名称", "领用数量", "领用人员", "领用日期", "用途说明", "所属科室", "领用状态"]
STATUSES = ["待审批", "已批准", "已领用", "已退回"]


@router.get("/stats")
def department_stats() -> dict[str, Any]:
    """科室领用统计：状态汇总与分科室申领、领用、退回数量。"""
    return service.department_stats()


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按领用单号检索"),
    status: str | None = Query(default=None, description="待审批、已批准、已领用、已退回"),
    dept: str | None = Query(default=None, description="按所属科室检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按领用单号、状态与科室过滤耗材领用列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, dept=dept, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出耗材领用清单：返回当前全量数据（含流转记录）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "consume", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条领用单明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"领用单 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条领用单，缺字段或数量非法时说明原因而不是静默丢弃。"""
    entry, message = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="领用单已登记，状态为待审批", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条领用单执行批准领用、确认发放、退回物料；不允许的流转会被拦下并说明原因。

    退回物料时需在 values.reason（或顶层 remark）中填写退回原因。
    """
    values = payload.values
    action = str(values.get("action") or "").strip()
    reason = str(values.get("reason") or payload.remark or "").strip()
    operator = str(values.get("operator") or "").strip()
    entry, message = service.run_action(entry_id, action, reason=reason, operator=operator)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
