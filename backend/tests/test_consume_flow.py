"""耗材领用状态流转的端到端校验：审批拦截、库存扣减、退回留痕与落盘保留。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import Store, store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_store(tmp_path, monkeypatch):
    """每个用例都从示例数据重新开始，并把落盘文件指到临时目录。"""
    monkeypatch.setattr(store, "_data_file", tmp_path / "store_state.json")
    store.reset()
    yield


def _create(number: str, material: str, qty: float, dept: str, date: str = "2026-09-10") -> dict:
    resp = client.post("/api/consume", json={"values": {
        "领用单号": number,
        "物料名称": material,
        "领用数量": qty,
        "领用人员": "测试员",
        "所属科室": dept,
        "领用日期": date,
        "用途说明": "流程验证",
    }})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"], payload["message"]
    return payload["entry"]


def _action(entry_id: int, action: str, reason: str = "") -> dict:
    resp = client.post(
        f"/api/consume/{entry_id}/actions",
        json={"values": {"action": action, "退回原因": reason, "操作人": "测试审批人"}},
    )
    assert resp.status_code == 200
    return resp.json()


def _stock(material: str) -> float:
    resp = client.get("/api/reagent", params={"size": 200})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        if item["物料名称"] == material:
            return item["结存数量"]
    raise AssertionError(f"台账里没有 {material}")


def _entry(entry_id: int) -> dict:
    resp = client.get(f"/api/consume/{entry_id}")
    assert resp.status_code == 200
    return resp.json()


def test_full_flow_deducts_stock_and_syncs_stats():
    # 种子单 CONS-0001：试剂耗材样例1 / 理化检验科 / 2026-09-01 / 4 个，台账结存 10
    assert _stock("试剂耗材样例1") == 10
    result = _action(1, "批准领用")
    assert result["ok"], result["message"]
    assert result["entry"]["status"] == "已批准"
    assert result["entry"]["领用状态"] == "已批准"

    result = _action(1, "确认发放")
    assert result["ok"], result["message"]
    assert result["entry"]["status"] == "已领用"
    assert _stock("试剂耗材样例1") == 6

    records = result["entry"]["流转记录"]
    assert [r["动作"] for r in records][-2:] == ["批准领用", "确认发放"]
    assert records[-1]["操作人"] == "测试审批人"

    stats = client.get("/api/consume/stats").json()
    dept = next(d for d in stats["departments"] if d["所属科室"] == "理化检验科")
    assert dept["已领用数量"] == 4
    summary = {item["label"]: item["value"] for item in stats["summary"]}
    assert summary["待审批领用"] == 0


def test_issued_entry_cannot_be_approved_again():
    assert _action(1, "批准领用")["ok"]
    assert _action(1, "确认发放")["ok"]
    result = _action(1, "批准领用")
    assert not result["ok"]
    assert "不能重复批准" in result["message"]
    assert _entry(1)["status"] == "已领用"


def test_return_requires_reason_and_returned_entry_cannot_be_issued():
    result = _action(1, "退回物料", reason="")
    assert not result["ok"]
    assert "退回原因" in result["message"]

    result = _action(1, "退回物料", reason="科室计划调整，暂不需要")
    assert result["ok"], result["message"]
    entry = result["entry"]
    assert entry["status"] == "已退回"
    assert entry["退回原因"] == "科室计划调整，暂不需要"
    assert entry["流转记录"][-1]["原因"] == "科室计划调整，暂不需要"
    assert entry["流转记录"][-1]["从状态"] == "待审批"
    assert entry["流转记录"][-1]["到状态"] == "已退回"

    blocked = _action(1, "确认发放")
    assert not blocked["ok"]
    assert "已退回" in blocked["message"]
    assert "不能直接改为已领用" in blocked["message"]
    blocked = _action(1, "批准领用")
    assert not blocked["ok"]
    assert "已退回" in blocked["message"]


def test_approval_blocked_when_period_total_exceeds_stock_limit():
    # 同物料同科室同日：种子单已占 4，再申领 7，合计 11 > 结存 10，审批要拦下
    assert _action(1, "批准领用")["ok"]
    extra = _create("CONS-9001", "试剂耗材样例1", 7, "理化检验科", date="2026-09-01")
    result = _action(extra["id"], "批准领用")
    assert not result["ok"]
    assert "超过库存上限" in result["message"]
    assert _entry(extra["id"])["status"] == "待审批"
    assert _stock("试剂耗材样例1") == 10


def test_approval_blocked_with_clear_message_when_stock_insufficient():
    entry = _create("CONS-9002", "试剂耗材样例1", 99, "临床检验科", date="2026-09-11")
    result = _action(entry["id"], "批准领用")
    assert not result["ok"]
    assert "库存不足" in result["message"]
    assert _stock("试剂耗材样例1") == 10


def test_issue_blocked_when_stock_dropped_after_approval():
    # 不同科室各批 6，先后发放：第二单发放时结存只剩 4，必须拦下且不能扣成负数
    first = _create("CONS-9003", "试剂耗材样例1", 6, "理化检验科", date="2026-09-12")
    second = _create("CONS-9004", "试剂耗材样例1", 6, "临床检验科", date="2026-09-12")
    assert _action(first["id"], "批准领用")["ok"]
    assert _action(second["id"], "批准领用")["ok"]
    assert _action(first["id"], "确认发放")["ok"]
    assert _stock("试剂耗材样例1") == 4
    result = _action(second["id"], "确认发放")
    assert not result["ok"]
    assert "库存不足" in result["message"]
    assert _stock("试剂耗材样例1") == 4


def test_return_after_issue_restocks_and_marks_abnormal():
    assert _action(1, "批准领用")["ok"]
    assert _action(1, "确认发放")["ok"]
    assert _stock("试剂耗材样例1") == 6
    result = _action(1, "退回物料", reason="领用过量，退回多余物料")
    assert result["ok"], result["message"]
    assert _stock("试剂耗材样例1") == 10
    assert result["entry"]["abnormal"] is True
    assert result["entry"]["pending"] is False


def test_unknown_material_blocked_at_approval():
    entry = _create("CONS-9005", "台账外物料", 1, "理化检验科")
    result = _action(entry["id"], "批准领用")
    assert not result["ok"]
    assert "未在试剂台账中登记" in result["message"]


def test_reagent_ledger_untouched_for_unrelated_materials():
    assert _action(1, "批准领用")["ok"]
    assert _action(1, "确认发放")["ok"]
    assert _stock("试剂耗材样例2") == 20
    assert _stock("试剂耗材样例3") == 30


def test_flow_records_survive_store_reload():
    assert _action(1, "批准领用")["ok"]
    assert _action(1, "确认发放")["ok"]
    assert _action(1, "退回物料", reason="审批后科室取消")["ok"]

    # 模拟服务重启：用同一个落盘文件重建仓库，流转记录与结存都要还在
    reloaded = Store(store.data_file)
    entry = next(row for row in reloaded.rows("consume") if row["id"] == 1)
    assert entry["status"] == "已退回"
    assert entry["退回原因"] == "审批后科室取消"
    assert [r["动作"] for r in entry["流转记录"]][-3:] == ["批准领用", "确认发放", "退回物料"]
    reagent = next(row for row in reloaded.rows("reagent") if row["物料名称"] == "试剂耗材样例1")
    assert reagent["结存数量"] == 10


def test_create_validation_and_duplicate_serial():
    resp = client.post("/api/consume", json={"values": {"物料名称": "试剂耗材样例1"}})
    assert resp.status_code == 200
    assert not resp.json()["ok"]
    assert "缺少必填字段" in resp.json()["message"]

    resp = client.post("/api/consume", json={"values": {
        "领用单号": "CONS-9006", "物料名称": "试剂耗材样例1", "领用数量": -3,
        "领用人员": "测试员", "所属科室": "理化检验科",
    }})
    assert not resp.json()["ok"]
    assert "领用数量" in resp.json()["message"]

    resp = client.post("/api/consume", json={"values": {
        "领用单号": "CONS-0001", "物料名称": "试剂耗材样例1", "领用数量": 1,
        "领用人员": "测试员", "所属科室": "理化检验科",
    }})
    assert not resp.json()["ok"]
    assert "已存在" in resp.json()["message"]


def test_list_filter_and_export_route():
    assert _action(1, "退回物料", reason="质量存疑")["ok"]
    resp = client.get("/api/consume", params={"status": "已退回"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["领用单号"] == "CONS-0001"

    resp = client.get("/api/consume", params={"keyword": "CONS-0002"})
    assert resp.json()["total"] == 1

    resp = client.get("/api/consume/export")
    assert resp.status_code == 200
    assert resp.json()["module"] == "consume"
    assert resp.json()["total"] == 3
