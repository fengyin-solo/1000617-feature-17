"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
耗材领用与试剂台账会被领用流程回写（结存扣减、流转记录），这两张表额外落一份
JSON 文件，刷新页面或重启服务后状态与流转记录都还在。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.seed import SEED_ROWS

DEFAULT_DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "store_state.json"
PERSISTED_MODULES = ("consume", "reagent")


class Store:
    def __init__(self, data_file: Path | None = None) -> None:
        env_file = os.environ.get("LIMS_STORE_FILE", "").strip()
        self._data_file = data_file or (Path(env_file) if env_file else DEFAULT_DATA_FILE)
        self._tables: dict[str, list[dict[str, Any]]] = {}
        self.reset()

    @property
    def data_file(self) -> Path:
        return self._data_file

    def reset(self) -> None:
        """重新载入示例数据，再叠加落盘状态；演示与测试都靠它回到干净起点。"""
        self._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
        self._load_persisted()

    def _load_persisted(self) -> None:
        if not self._data_file.exists():
            return
        try:
            payload = json.loads(self._data_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for name in PERSISTED_MODULES:
            rows = payload.get(name)
            if isinstance(rows, list):
                self._tables[name] = rows

    def save(self) -> None:
        """把需要长期保留的模块写入 JSON 文件；先写临时文件再替换，避免写一半。"""
        payload = {name: self._tables.get(name, []) for name in PERSISTED_MODULES}
        self._data_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self._data_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_file.replace(self._data_file)

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
