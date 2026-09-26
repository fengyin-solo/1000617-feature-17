"""耗材领用业务规则：状态流转、库存校验、流转留痕与科室统计都收在这里。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "consume"
REAGENT_MODULE = "reagent"
REQUIRED_FIELDS = ["领用单号", "物料名称", "领用数量", "领用人员", "所属科室"]
OPTIONAL_FIELDS = ["领用日期", "用途说明"]
STATUS_ORDER = ["待审批", "已批准", "已领用", "已退回"]

# 状态机：每个动作只允许从特定来源状态发起，已领用/已退回是收口状态。
ACTION_RULES = {
    "批准领用": {"sources": ("待审批",), "target": "已批准"},
    "确认发放": {"sources": ("已批准",), "target": "已领用"},
    "退回物料": {"sources": ("待审批", "已批准", "已领用"), "target": "已退回"},
}

# 高频误操作给出具体原因，而不是一句笼统的"不允许"。
BLOCKED_MESSAGES = {
    ("已批准", "批准领用"): "领用单 {no} 已是已批准状态，不能重复批准",
    ("已领用", "批准领用"): "领用单 {no} 已领用，不能重复批准",
    ("已领用", "确认发放"): "领用单 {no} 已发放完成，不能重复发放",
    ("已退回", "批准领用"): "领用单 {no} 已退回，流程已关闭，不能重新批准",
    ("已退回", "确认发放"): "领用单 {no} 已退回，不能直接改为已领用",
    ("已退回", "退回物料"): "领用单 {no} 已是已退回状态，不能重复退回",
    ("待审批", "确认发放"): "领用单 {no} 还未批准，请先批准领用再确认发放",
}

# 参与库存占用统计的状态：已退回的单据不再占用库存。
ACTIVE_STATUSES = ("待审批", "已批准", "已领用")


def _to_number(value: Any) -> float | None:
    """把领用数量、结存数量这类字段解析成数字；解析不了返回 None。"""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _fmt(number: float) -> int | float:
    """整数数量按整数展示，避免出现 4.0 这种读数。"""
    return int(number) if float(number).is_integer() else number


class ConsumeService:
    # ---------- 查询 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for row in rows:
            self._ensure_shape(row)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("领用单号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is not None:
            self._ensure_shape(entry)
        return entry

    # ---------- 登记 ----------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, [f"缺少必填字段：{'、'.join(missing)}"]
        quantity = _to_number(values.get("领用数量"))
        if quantity is None or quantity <= 0:
            return None, ["领用数量必须是大于 0 的数字"]
        serial = str(values["领用单号"]).strip()
        if any(str(row.get("领用单号")) == serial for row in store.rows(MODULE)):
            return None, [f"领用单号 {serial} 已存在，不能重复登记"]
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in REQUIRED_FIELDS + OPTIONAL_FIELDS:
            value = values.get(field)
            if value is not None and str(value).strip() != "":
                entry[field] = value
        entry["领用数量"] = _fmt(quantity)
        entry.setdefault("领用日期", datetime.now().strftime("%Y-%m-%d"))
        entry["status"] = STATUS_ORDER[0]
        entry["领用状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["流转记录"] = []
        self._append_flow(entry, action="登记领用", source="", target=STATUS_ORDER[0], reason="", operator="")
        rows.append(entry)
        store.save()
        return entry, []

    # ---------- 状态流转 ----------

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
        self._ensure_shape(entry)
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于耗材领用可执行范围"
        rule = ACTION_RULES[action]
        current = str(entry.get("status") or "")
        if current not in rule["sources"]:
            return None, self._blocked_message(entry, action, current)
        reason = reason.strip()
        if action == "退回物料" and not reason:
            return None, "退回物料必须填写退回原因，说明为什么退回"
        if action == "批准领用":
            message = self._check_stock_limit(entry)
            if message:
                return None, message
        elif action == "确认发放":
            message = self._deduct_stock(entry)
            if message:
                return None, message
        elif action == "退回物料":
            self._restock_if_issued(entry, current)
            entry["退回原因"] = reason
        target = str(rule["target"])
        entry["status"] = target
        entry["领用状态"] = target
        entry["pending"] = target in ("待审批", "已批准")
        entry["abnormal"] = target == "已退回"
        self._append_flow(entry, action=action, source=current, target=target, reason=reason, operator=operator)
        store.save()
        return entry, f"领用单已{action}"

    # ---------- 统计 ----------

    def summary(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        month = datetime.now().strftime("%Y-%m")
        return [
            {"label": "待审批领用", "value": sum(1 for row in rows if row.get("status") == "待审批")},
            {"label": "本月领用单", "value": sum(1 for row in rows if str(row.get("领用日期", "")).startswith(month))},
            {"label": "退回单数", "value": sum(1 for row in rows if row.get("status") == "已退回")},
        ]

    def department_stats(self) -> list[dict[str, Any]]:
        """按科室汇总领用情况；状态流转后重算，保证列表、结存与统计口径一致。"""
        groups: dict[str, dict[str, Any]] = {}
        for row in store.rows(MODULE):
            dept = str(row.get("所属科室") or "").strip() or "未填写科室"
            group = groups.setdefault(dept, {
                "所属科室": dept,
                "领用单数": 0,
                "申领合计": 0.0,
                "已领用数量": 0.0,
                "待审批数": 0,
                "已退回数": 0,
            })
            group["领用单数"] += 1
            status = row.get("status")
            quantity = _to_number(row.get("领用数量")) or 0.0
            if status in ACTIVE_STATUSES:
                group["申领合计"] += quantity
            if status == "已领用":
                group["已领用数量"] += quantity
            elif status == "待审批":
                group["待审批数"] += 1
            elif status == "已退回":
                group["已退回数"] += 1
        stats = []
        for dept in sorted(groups):
            group = groups[dept]
            group["申领合计"] = _fmt(group["申领合计"])
            group["已领用数量"] = _fmt(group["已领用数量"])
            stats.append(group)
        return stats

    # ---------- 内部规则 ----------

    def _ensure_shape(self, entry: dict[str, Any]) -> None:
        """兜底老数据：补流转记录字段，并让领用状态与流转状态保持一致。"""
        entry.setdefault("流转记录", [])
        status = str(entry.get("status") or STATUS_ORDER[0])
        entry["status"] = status
        entry["领用状态"] = status

    def _blocked_message(self, entry: dict[str, Any], action: str, current: str) -> str:
        template = BLOCKED_MESSAGES.get((current, action))
        if template:
            return template.format(no=entry.get("领用单号", entry.get("id")))
        return f"领用单 {entry.get('领用单号', entry.get('id'))} 当前状态为{current}，不能执行「{action}」"

    def _find_reagent(self, material: Any) -> dict[str, Any] | None:
        name = str(material or "").strip()
        for row in store.rows(REAGENT_MODULE):
            if str(row.get("物料名称", "")).strip() == name:
                return row
        return None

    def _check_stock_limit(self, entry: dict[str, Any]) -> str:
        """审批环节拦截：同一物料同一科室同时段申领合计不能超过库存上限。"""
        material = str(entry.get("物料名称") or "").strip()
        reagent = self._find_reagent(material)
        if reagent is None:
            return f"物料「{material}」未在试剂台账中登记，无法核验库存上限，审批未通过"
        stock = _to_number(reagent.get("结存数量")) or 0.0
        quantity = _to_number(entry.get("领用数量")) or 0.0
        if quantity > stock:
            return (
                f"库存不足：{material}当前结存 {_fmt(stock)}，本次申领 {_fmt(quantity)}，"
                "审批未通过，库存不会被扣减"
            )
        claimed = quantity
        for row in store.rows(MODULE):
            if row is entry or row.get("status") not in ACTIVE_STATUSES:
                continue
            if (
                str(row.get("物料名称", "")).strip() == material
                and str(row.get("所属科室", "")).strip() == str(entry.get("所属科室", "")).strip()
                and str(row.get("领用日期", "")).strip() == str(entry.get("领用日期", "")).strip()
            ):
                claimed += _to_number(row.get("领用数量")) or 0.0
        if claimed > stock:
            return (
                f"同一物料同一科室同时段申领合计 {_fmt(claimed)} 超过库存上限 {_fmt(stock)}"
                f"（{entry.get('所属科室')} {entry.get('领用日期')} 已申领 {_fmt(claimed - quantity)}，"
                f"本次申领 {_fmt(quantity)}），审批未通过"
            )
        return ""

    def _deduct_stock(self, entry: dict[str, Any]) -> str:
        """发放时扣减结存；库存不足要明确提示，不能扣成负数。"""
        material = str(entry.get("物料名称") or "").strip()
        reagent = self._find_reagent(material)
        if reagent is None:
            return f"物料「{material}」未在试剂台账中登记，无法发放，发放未执行"
        stock = _to_number(reagent.get("结存数量")) or 0.0
        quantity = _to_number(entry.get("领用数量")) or 0.0
        if quantity > stock:
            return (
                f"库存不足：{material}当前结存 {_fmt(stock)}，需发放 {_fmt(quantity)}，"
                "发放未执行，库存不会被扣成负数"
            )
        remaining = stock - quantity
        reagent["结存数量"] = _fmt(remaining)
        if remaining == 0 and reagent.get("status") not in ("已冻结", "已耗尽"):
            reagent["status"] = "已耗尽"
            reagent["pending"] = False
        return ""

    def _restock_if_issued(self, entry: dict[str, Any], current: str) -> None:
        """已领用的单据退回时把数量回补到台账；未发放过的单据不涉及库存。"""
        if current != "已领用":
            return
        reagent = self._find_reagent(entry.get("物料名称"))
        if reagent is None:
            return
        stock = _to_number(reagent.get("结存数量")) or 0.0
        quantity = _to_number(entry.get("领用数量")) or 0.0
        reagent["结存数量"] = _fmt(stock + quantity)
        if reagent.get("status") == "已耗尽":
            reagent["status"] = "正常可用"
            reagent["pending"] = True

    def _append_flow(
        self,
        entry: dict[str, Any],
        *,
        action: str,
        source: str,
        target: str,
        reason: str,
        operator: str,
    ) -> None:
        entry.setdefault("流转记录", []).append({
            "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "动作": action,
            "操作人": operator.strip() or "值班管理员",
            "从状态": source,
            "到状态": target,
            "原因": reason,
        })
