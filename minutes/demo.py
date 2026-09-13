"""Hand-authored fictional fixture, never an extractor for arbitrary user text."""

from .models import Extraction
from .review import install_extraction
from .transcript import new_meeting

DEMO_TEXT = """[00:00:05] 我方陈经理：今天与示例芯科讨论代理合作。
[00:00:30] 对方林经理：我们主要做工业传感器芯片，公司目前约80人，营收这次不方便透露。
[00:02:10] 对方林经理：现有产品在工业设备上已量产，汽车市场还在计划拓展阶段。
[00:03:20] 对方林经理：目前华东有一家代理商示例电子，负责工业产品，没有约定独家代理。
[00:04:40] 对方林经理：我们认为低功耗是这款产品的优势，但还没有提供对比测试数据。
[00:05:50] 对方林经理：我们担心新代理商与现有代理商发生客户冲突，今天还没有解决。
[00:07:00] 对方林经理：目前主要客户是示例自动化，已经导入工业传感器芯片，采购量暂不透露。
[00:08:10] 对方林经理：测试样品交期约两周，批量价格尚未讨论。
[00:09:20] 我方陈经理：如果内部审核通过，我方会提供一份目标客户清单，具体日期待定。
[00:10:00] 我方陈经理：下一步由我收集目标客户名单，完成日期暂未确定。"""


def demo_meeting():
    meeting = new_meeting(DEMO_TEXT, "示例芯科 · 代理合作初谈（虚构）", "示例芯科", "2026-09-10")
    for p in meeting.participants:
        p.name = "陈经理" if p.participant_id == "p1" else "林经理"
        p.side = "我方" if p.participant_id == "p1" else "对方"
        p.company = "示例代理平台" if p.participant_id == "p1" else "示例芯科"
    facts = []

    def add(i, statement, details, state="stated"):
        facts.append({"fact_id": f"f{len(facts)+1}", "statement": statement, "details": details,
                      "information_state": state, "evidence": [{"segment_id": f"s{i}",
                      "quote": meeting.segments[i-1].text, "occurrence": 0}]})

    add(2, "对方主要产品为工业传感器芯片。", {"kind": "company", "field": "主要产品"})
    add(2, "对方称公司目前约80人。", {"kind": "company", "field": "公司人数", "amount": "约80", "unit": "人", "scope": "公司总人数"})
    add(3, "工业设备应用已量产；汽车市场仅计划拓展。", {"kind": "market", "categories": ["工业"], "stage": "已量产", "application": "工业设备"})
    add(3, "汽车市场处于计划拓展阶段。", {"kind": "market", "categories": ["汽车"], "stage": "计划拓展"})
    add(4, "华东代理商为示例电子，负责工业产品，未约定独家。", {"kind": "agent", "name": "示例电子", "region": "华东", "product": "工业产品", "policy": "没有约定独家代理"})
    add(5, "对方称产品有低功耗优势，尚未提供对比测试数据。", {"kind": "advantage", "claimed_by": "对方林经理", "condition": "尚无对比测试数据"})
    add(6, "对方顾虑新旧代理商发生客户冲突，尚未解决。", {"kind": "concern", "raised_by": "对方林经理", "resolved": False})
    add(7, "对方主要客户为示例自动化，已导入工业传感器芯片。", {"kind": "customer", "name": "示例自动化", "product": "工业传感器芯片", "stage": "已导入", "major_confirmed": True, "major_basis": "原文明确称为主要客户"})
    add(8, "测试样品交期约两周，批量价格尚未讨论。", {"kind": "commercial", "topic": "交期", "period": "约两周", "condition": "测试样品"})
    add(9, "内部审核通过后，我方提供目标客户清单，日期待定。", {"kind": "commitment", "owner_id": "p1", "owner_side": "我方", "condition": "内部审核通过", "original_time": "具体日期待定", "certainty": "明确承诺"})
    add(10, "陈经理收集目标客户名单，完成日期暂未确定。", {"kind": "action", "owner_id": "p1", "owner_side": "我方", "original_time": "完成日期暂未确定"})
    return install_extraction(meeting, Extraction.model_validate({"facts": facts}), mode="demo")
