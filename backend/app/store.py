"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
耗材领用的状态流转需要跨刷新、跨重启留痕，所以在内存表之外再落一份 JSON 快照：
首次启动用 seed 初始化，之后每次写操作都原子写回，重启后仍能读到最新状态。
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from app.seed import SEED_ROWS

SNAPSHOT_PATH = os.environ.get(
    "LAB_SNAPSHOT_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "snapshot.json"),
)


def _normalize_consume(row: dict[str, Any]) -> dict[str, Any]:
    """领用单统一补齐状态展示字段与流转记录，已有试剂台账等取值一律不动。"""
    status = str(row.get("status") or "待审批")
    # 历史 seed 里「领用状态」是占位文字，统一改成真实单据状态
    row["领用状态"] = status
    history = row.get("流转记录")
    if not isinstance(history, list) or not history:
        chain = ["待审批", "已批准", "已领用"]
        built: list[dict[str, Any]] = []
        for index, step in enumerate(chain):
            if step == status:
                built.append({
                    "动作": "登记领用单" if index == 0 else ("批准领用" if step == "已批准" else "确认发放"),
                    "from": None if index == 0 else chain[index - 1],
                    "to": step,
                    "原因": "",
                    "操作人": str(row.get("领用人员") or ""),
                    "时间": str(row.get("领用日期") or ""),
                    "备注": "系统补录",
                })
                break
        if status == "已退回":
            built.append({
                "动作": "退回物料",
                "from": built[-1]["to"] if built else None,
                "to": "已退回",
                "原因": "历史数据，未登记退回原因",
                "操作人": str(row.get("领用人员") or ""),
                "时间": str(row.get("领用日期") or ""),
                "备注": "系统补录",
            })
        row["流转记录"] = built
    return row


def _load_snapshot() -> dict[str, list[dict[str, Any]]] | None:
    try:
        with open(SNAPSHOT_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


class Store:
    def __init__(self) -> None:
        snapshot = _load_snapshot()
        self._tables: dict[str, list[dict[str, Any]]] = {}
        for name, seed_rows in SEED_ROWS.items():
            if snapshot is not None and name in snapshot and isinstance(snapshot[name], list):
                rows = [dict(row) for row in snapshot[name]]
            else:
                rows = [dict(row) for row in seed_rows]
            if name == "consume":
                rows = [_normalize_consume(row) for row in rows]
            self._tables[name] = rows

    def save(self) -> None:
        """把当前所有表落盘成 JSON 快照，保证刷新或重启后流转记录仍在。"""
        directory = os.path.dirname(SNAPSHOT_PATH)
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=".snapshot-", suffix=".json", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self._tables, handle, ensure_ascii=False, indent=2)
                os.replace(tmp_path, SNAPSHOT_PATH)
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        except OSError:
            # 落盘失败不应中断业务动作；内存里的本次操作仍然生效
            pass

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
