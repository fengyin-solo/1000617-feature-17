"""耗材领用业务规则：状态流转、字段校验、库存联动与科室统计都收在这里。

状态机：
    待审批 ──批准领用──▶ 已批准 ──确认发放──▶ 已领用
      │                    │                    │
      └────────退回物料─────┴────────退回物料────┘
                           ▼
                        已退回（终态，需记录退回原因）

约束：
- 已领用的领用单不能再次批准；已退回的不能直接改为已领用；
- 同一物料、同一科室、同一领用日期，在途申领（待审批+已批准）累计超过
  试剂台账结存上限时，审批环节拦下并说明原因；
- 确认发放时再次校验库存，不足则明确提示，绝不把结存扣成负数。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.store import store

MODULE = "consume"
REAGENT_MODULE = "reagent"
REQUIRED_FIELDS = ["物料名称", "领用数量", "领用人员", "所属科室"]
OPTIONAL_FIELDS = ["用途说明", "领用日期"]
STATUS_PENDING = "待审批"
STATUS_APPROVED = "已批准"
STATUS_ISSUED = "已领用"
STATUS_RETURNED = "已退回"
STATUS_ORDER = [STATUS_PENDING, STATUS_APPROVED, STATUS_ISSUED, STATUS_RETURNED]
# 各状态允许执行的动作
ALLOWED_ACTIONS: dict[str, set[str]] = {
    STATUS_PENDING: {"批准领用", "退回物料"},
    STATUS_APPROVED: {"确认发放", "退回物料"},
    STATUS_ISSUED: {"退回物料"},
    STATUS_RETURNED: set(),
}
FROZEN_REAGENT_STATUS = "已冻结"


def _to_int(value: Any) -> int | None:
    """把前端传来的数量尽量转成正整数；转不出来返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not number.is_integer():
        return None
    return int(number)


class ConsumeService:
    # ---------- 查询 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        dept: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("领用单号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if dept:
            rows = [row for row in rows if dept in str(row.get("所属科室", ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    # ---------- 登记 ----------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        qty = _to_int(values.get("领用数量"))
        if qty is None or qty <= 0:
            return None, "领用数量必须是大于 0 的整数，请核对后重新登记"

        rows = store.rows(MODULE)
        new_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        entry: dict[str, Any] = {"id": new_id}
        entry["领用单号"] = str(values.get("领用单号") or "").strip() or self._gen_order_no(rows)
        entry["物料名称"] = str(values["物料名称"]).strip()
        entry["领用数量"] = qty
        entry["领用人员"] = str(values["领用人员"]).strip()
        entry["所属科室"] = str(values["所属科室"]).strip()
        entry["用途说明"] = str(values.get("用途说明") or "").strip()
        entry["领用日期"] = str(values.get("领用日期") or "").strip() or date.today().isoformat()
        entry["status"] = STATUS_PENDING
        entry["领用状态"] = STATUS_PENDING
        entry["pending"] = True
        entry["abnormal"] = False
        entry["退回原因"] = ""
        entry["流转记录"] = [{
            "动作": "登记领用单",
            "from": None,
            "to": STATUS_PENDING,
            "原因": "",
            "操作人": entry["领用人员"],
            "时间": self._now(),
            "备注": "",
        }]
        rows.append(entry)
        store.save()
        return entry, ""

    def _gen_order_no(self, rows: list[dict[str, Any]]) -> str:
        prefix = f"CONS-{date.today().strftime('%Y%m')}-"
        seq = 1
        for row in rows:
            no = str(row.get("领用单号") or "")
            if no.startswith(prefix):
                tail = no[len(prefix):]
                if tail.isdigit():
                    seq = max(seq, int(tail) + 1)
        return f"{prefix}{seq:03d}"

    # ---------- 动作流转 ----------
    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        reason: str = "",
        operator: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"领用单 {entry_id} 不存在或已归档"
        action = str(action or "").strip()
        current = str(entry.get("status") or "")
        if action not in {"批准领用", "确认发放", "退回物料"}:
            return None, f"动作「{action}」不属于耗材领用可执行范围"
        if action not in ALLOWED_ACTIONS.get(current, set()):
            return None, self._illegal_message(current, action)

        reason = str(reason or "").strip()
        qty = _to_int(entry.get("领用数量")) or 0
        material = str(entry.get("物料名称") or "")

        if action == "批准领用":
            blocked = self._approval_block_reason(entry, material, qty)
            if blocked:
                return None, blocked
            return self._commit(entry, action, current, STATUS_APPROVED, reason, operator, "库存与在途申领校验通过")

        if action == "确认发放":
            shortage = self._issue_block_reason(material, qty)
            if shortage:
                return None, shortage
            self._deduct_stock(material, qty)
            return self._commit(entry, action, current, STATUS_ISSUED, reason, operator, f"已从试剂台账扣减 {qty}")

        # 退回物料：必须填写退回原因
        if not reason:
            return None, "退回物料必须填写退回原因，便于后续追溯"
        note = ""
        if current == STATUS_ISSUED:
            self._restore_stock(material, qty)
            note = f"已回补试剂结存 {qty}"
        entry["退回原因"] = reason
        return self._commit(entry, action, current, STATUS_RETURNED, reason, operator, note)

    def _commit(
        self,
        entry: dict[str, Any],
        action: str,
        from_status: str,
        to_status: str,
        reason: str,
        operator: str,
        note: str,
    ) -> tuple[dict[str, Any], str]:
        entry["status"] = to_status
        entry["领用状态"] = to_status
        entry["pending"] = to_status not in (STATUS_ISSUED, STATUS_RETURNED)
        entry["abnormal"] = to_status == STATUS_RETURNED
        record = {
            "动作": action,
            "from": from_status,
            "to": to_status,
            "原因": reason,
            "操作人": operator or str(entry.get("领用人员") or ""),
            "时间": self._now(),
            "备注": note,
        }
        history = entry.setdefault("流转记录", [])
        history.append(record)
        store.save()
        verb = {"批准领用": "批准", "确认发放": "发放", "退回物料": "退回"}[action]
        return entry, f"领用单 {entry.get('领用单号')} 已{verb}，当前状态：{to_status}"

    # ---------- 状态约束提示 ----------
    def _illegal_message(self, current: str, action: str) -> str:
        if action == "批准领用":
            if current == STATUS_APPROVED:
                return "该领用单已批准，不能重复批准；可直接确认发放或退回物料"
            if current == STATUS_ISSUED:
                return "该领用单已领用并扣减库存，不能再次批准"
            if current == STATUS_RETURNED:
                return "该领用单已退回，不能再次批准；如需重新领用请重新登记领用单"
        if action == "确认发放":
            if current == STATUS_PENDING:
                return "该领用单尚在待审批，需先完成批准领用才能确认发放"
            if current == STATUS_ISSUED:
                return "该领用单已领用，不能重复发放"
            if current == STATUS_RETURNED:
                return "该领用单已退回，不能直接改为已领用；如需重新领用请重新登记领用单"
        if action == "退回物料":
            if current == STATUS_RETURNED:
                return "该领用单已退回，无需重复退回"
        return f"当前状态「{current}」不允许执行「{action}」"

    # ---------- 库存联动 ----------
    def _reagent_rows(self, material: str) -> list[dict[str, Any]]:
        return [row for row in store.rows(REAGENT_MODULE) if str(row.get("物料名称") or "") == material]

    def _stock_balance(self, material: str) -> int:
        return sum(_to_int(row.get("结存数量")) or 0 for row in self._reagent_rows(material))

    def _approval_block_reason(self, entry: dict[str, Any], material: str, qty: int) -> str:
        reagents = self._reagent_rows(material)
        if not reagents:
            return f"审批未通过：试剂台账中查不到物料「{material}」，请先在试剂耗材模块建档后再审批"
        usable = [row for row in reagents if row.get("status") != FROZEN_REAGENT_STATUS]
        if not usable:
            return f"审批未通过：物料「{material}」全部批次已冻结，暂不可领用"

        # 同一物料 + 同一科室 + 同一领用日期，在途申领（待审批、已批准）累计占用
        dept = str(entry.get("所属科室") or "")
        day = str(entry.get("领用日期") or "")
        occupied = 0
        for row in store.rows(MODULE):
            if row is entry:
                continue
            if (
                str(row.get("物料名称") or "") == material
                and str(row.get("所属科室") or "") == dept
                and str(row.get("领用日期") or "") == day
                and row.get("status") in (STATUS_PENDING, STATUS_APPROVED)
            ):
                occupied += _to_int(row.get("领用数量")) or 0

        cap = sum(_to_int(row.get("结存数量")) or 0 for row in reagents)
        if occupied + qty > cap:
            return (
                f"审批未通过：同一科室同一时段在途申领已占用 {occupied}，"
                f"本次再领 {qty} 将达 {occupied + qty}，超过物料「{material}」"
                f"库存上限 {cap}；请核减数量或待既有领用单发放/退回后再审批"
            )
        return ""

    def _issue_block_reason(self, material: str, qty: int) -> str:
        reagents = self._reagent_rows(material)
        if not reagents:
            return f"发放失败：试剂台账中查不到物料「{material}」，无法扣减库存"
        usable = [row for row in reagents if row.get("status") != FROZEN_REAGENT_STATUS]
        balance = sum(_to_int(row.get("结存数量")) or 0 for row in usable)
        if balance < qty:
            return (
                f"发放失败：物料「{material}」当前可用结存仅 {balance}，"
                f"不足本次领用数量 {qty}，库存未做扣减；请先办理入库或核减领用数量"
            )
        return ""

    def _deduct_stock(self, material: str, qty: int) -> None:
        """按批次扣减结存，任何一批都不允许扣成负数。"""
        remain = qty
        for row in self._reagent_rows(material):
            if row.get("status") == FROZEN_REAGENT_STATUS:
                continue
            stock = _to_int(row.get("结存数量")) or 0
            if stock <= 0:
                continue
            take = min(stock, remain)
            row["结存数量"] = stock - take
            if row["结存数量"] == 0 and row.get("status") != FROZEN_REAGENT_STATUS:
                row["status"] = "已耗尽"
                row["物料状态"] = "已耗尽"
                row["pending"] = False
            remain -= take
            if remain == 0:
                break

    def _restore_stock(self, material: str, qty: int) -> None:
        """退回已发放物料时回补结存；台账里没有对应物料时只记录退回，不凭空建账。"""
        rows = self._reagent_rows(material)
        if not rows:
            return
        target = next((row for row in rows if row.get("status") != FROZEN_REAGENT_STATUS), rows[0])
        stock = _to_int(target.get("结存数量")) or 0
        target["结存数量"] = stock + qty
        if target.get("status") == "已耗尽":
            target["status"] = "正常可用"
            target["物料状态"] = "正常可用"
            target["pending"] = True

    # ---------- 科室统计 ----------
    def department_stats(self) -> dict[str, Any]:
        rows = store.rows(MODULE)
        summary = {
            STATUS_PENDING: 0,
            STATUS_APPROVED: 0,
            STATUS_ISSUED: 0,
            STATUS_RETURNED: 0,
        }
        issued_qty = 0
        returned_qty = 0
        dept_map: dict[str, dict[str, Any]] = {}
        for row in rows:
            status = str(row.get("status") or "")
            qty = _to_int(row.get("领用数量")) or 0
            if status in summary:
                summary[status] += 1
            if status == STATUS_ISSUED:
                issued_qty += qty
            if status == STATUS_RETURNED:
                returned_qty += qty
            dept_name = str(row.get("所属科室") or "未填写科室")
            bucket = dept_map.setdefault(dept_name, {
                "所属科室": dept_name,
                "申领总数": 0,
                STATUS_PENDING: 0,
                STATUS_APPROVED: 0,
                STATUS_ISSUED: 0,
                STATUS_RETURNED: 0,
                "已领用量": 0,
                "退回数量": 0,
            })
            bucket["申领总数"] += 1
            if status in bucket:
                bucket[status] += 1
            if status == STATUS_ISSUED:
                bucket["已领用量"] += qty
            if status == STATUS_RETURNED:
                bucket["退回数量"] += qty
        departments = sorted(dept_map.values(), key=lambda item: item["申领总数"], reverse=True)
        return {
            "summary": {
                "待审批单数": summary[STATUS_PENDING],
                "已批准待发放": summary[STATUS_APPROVED],
                "已领用单数": summary[STATUS_ISSUED],
                "已退回单数": summary[STATUS_RETURNED],
                "累计已领用量": issued_qty,
                "累计退回数量": returned_qty,
            },
            "departments": departments,
        }

    # ---------- 工具 ----------
    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
