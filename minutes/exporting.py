import csv
import io
import re

from .models import KIND_LABELS, Meeting
from .review import require_exportable
from .transcript import time_label

DETAIL_LABELS = {
    "field": "信息类别", "amount": "金额或数量", "currency": "币种", "unit": "单位",
    "period": "期间", "scope": "统计口径", "categories": "市场类别", "product": "产品",
    "application": "具体应用", "stage": "阶段", "name": "名称", "region": "区域",
    "cooperation": "合作模式", "policy": "渠道政策", "comparison": "比较对象",
    "condition": "前提条件", "claimed_by": "信息来源", "raised_by": "提出人",
    "response": "回应", "resolved": "是否解决", "scale": "规模",
    "major_confirmed": "大客户身份已明确", "major_basis": "大客户依据", "topic": "条款类别",
    "tax_basis": "含税口径", "owner_id": "责任人", "owner_side": "所属方",
    "original_time": "原始时间", "deadline": "截止日期", "certainty": "承诺语气",
    "group": "总结类别", "reason": "待确认原因",
}
SUMMARY_GROUPS = ["会议要点", "合作进展与分歧", "商务条件", "我方承诺", "下一步动作", "其他关键信息与待确认事项"]


def detail_lines(fact, meeting: Meeting) -> list[str]:
    lines = []
    for key, value in fact.details.model_dump(mode="json").items():
        if key == "kind" or value is None or value == []:
            continue
        if key == "owner_id":
            person = next((p for p in meeting.participants if p.participant_id == value), None)
            value = (person.name or person.speaker_label) if person else "未知"
        if isinstance(value, bool):
            value = "是" if value else "否"
        if isinstance(value, list):
            value = "、".join(value)
        lines.append(f"{DETAIL_LABELS.get(key, key)}：{value}")
    return lines


def fact_text(fact, meeting: Meeting) -> str:
    tags = []
    if fact.source == "user_added":
        tags.append("人工补充")
    elif fact.source == "user_edited":
        tags.append("人工修改，请以审核内容为准")
    if fact.information_state == "uncertain":
        tags.append("待确认")
    elif fact.information_state == "conflicting":
        tags.append("存在冲突")
    elif fact.information_state == "explicit_none":
        tags.append("明确没有")
    parts = [fact.statement] + detail_lines(fact, meeting)
    if tags:
        parts.append("；".join(tags))
    return "；".join(parts)


def section_lines(meeting: Meeting, section_id: str) -> list[str]:
    if section_id == "meeting":
        meta = meeting.metadata
        lines = [f"会议日期：{meta.meeting_date.value or '未提供'}（用户提供）",
                 f"对方公司：{meta.company.value or '未提供'}（用户提供）"]
        for side in ("我方", "对方", "第三方", "未知"):
            people = [p for p in meeting.participants if p.side == side]
            values = [" / ".join(filter(None, [p.name or p.speaker_label, p.company, p.role])) for p in people]
            lines.append(f"{side}参会人：{'；'.join(values) or '未提供'}")
        return lines
    section = next(s for s in meeting.sections if s.section_id == section_id)
    facts = [f for f in meeting.facts if f.fact_id in section.fact_ids]
    lines = [fact_text(f, meeting) for f in facts]
    if section_id == "company":
        present = {f.details.field for f in facts}
        lines += [f"{field}：未提及" for field in ("营收", "主要产品", "主要应用场景", "公司人数", "其他情况")
                  if field not in present]
    return lines or ["未提及"]


def escape_md(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>()#!|])", r"\\\1", text).replace("\n", " ")


def to_markdown(meeting: Meeting) -> str:
    require_exportable(meeting)
    lines = [f"# {escape_md(meeting.metadata.title.value or '会议纪要')}", "",
             f"版本：{meeting.revision} · 已人工确认", ""]
    if meeting.mode == "demo":
        lines += ["> 演示纪要：全部内容为虚构示例，不是真实会议。", ""]
    evidence_index = []

    def add_fact(fact):
        refs = []
        for ev in fact.evidence:
            eid = f"E{len(evidence_index) + 1}"
            evidence_index.append((eid, ev))
            refs.append(f"[{eid}](#{eid.lower()})")
        lines.append(f"- {escape_md(fact_text(fact, meeting))} {' '.join(refs)}".rstrip())

    for section in meeting.sections:
        lines += [f"## {section.title}", ""]
        facts = [f for f in meeting.facts if f.fact_id in section.fact_ids]
        if section.section_id == "meeting":
            lines += [f"- {escape_md(line)}" for line in section_lines(meeting, "meeting")]
        elif section.section_id == "summary":
            for group in SUMMARY_GROUPS:
                lines += [f"### {group}", ""]
                selected = []
                for f in facts:
                    kind = f.details.kind
                    actual = f.details.group if kind == "summary" else KIND_LABELS[kind]
                    if kind in ("competitor", "issue"):
                        actual = SUMMARY_GROUPS[-1]
                    if actual == group:
                        selected.append(f)
                for f in selected:
                    add_fact(f)
                if not selected:
                    lines.append("- 未提及")
                lines.append("")
        else:
            for f in facts:
                add_fact(f)
            if section.section_id == "company":
                present = {f.details.field for f in facts}
                lines += [f"- {field}：未提及" for field in ("营收", "主要产品", "主要应用场景", "公司人数", "其他情况")
                          if field not in present]
            elif not facts:
                lines.append("- 未提及")
        lines.append("")
    lines += ["## 原文依据", ""]
    for eid, ev in evidence_index:
        lines += [f"### {eid}", "", f"{ev.segment_id} · {time_label(ev.start_ms)}", "",
                  f"> {escape_md(ev.quote)}", ""]
    return "\n".join(lines).rstrip() + "\n"


def csv_safe(value) -> str:
    text = "" if value is None else str(value)
    if text.lstrip(" \t\r\n\ufeff").startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def encode_csv(headers, rows) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows([[csv_safe(v) for v in row] for row in rows])
    return stream.getvalue().encode("utf-8-sig")


def to_meetings_csv(meeting: Meeting) -> bytes:
    require_exportable(meeting)
    return encode_csv(["会议ID", "版本", "日期", "公司", "主题", "数据来源"] + [s.title for s in meeting.sections], [[
        meeting.meeting_id, meeting.revision, meeting.metadata.meeting_date.value,
        meeting.metadata.company.value, meeting.metadata.title.value,
        "虚构演示" if meeting.mode == "demo" else "用户会议",
        *["\n".join(section_lines(meeting, s.section_id)) for s in meeting.sections],
    ]])


def to_actions_csv(meeting: Meeting) -> bytes:
    require_exportable(meeting)
    rows = []
    people = {p.participant_id: p for p in meeting.participants}
    for f in meeting.facts:
        if f.details.kind != "action":
            continue
        d = f.details
        p = people.get(d.owner_id)
        rows.append([meeting.meeting_id, meeting.revision, f.fact_id, f.statement,
                     (p.name or p.speaker_label) if p else "未知", d.owner_side, d.original_time,
                     d.deadline, d.condition, "\n".join(e.quote for e in f.evidence),
                     "已确认", "虚构演示" if meeting.mode == "demo" else "用户会议"])
    return encode_csv(["会议ID", "版本", "动作ID", "动作", "责任人", "所属方", "原始时间", "截止日期",
                       "前提条件", "原文依据", "确认状态", "数据来源"], rows)


def to_json(meeting: Meeting) -> bytes:
    require_exportable(meeting)
    return meeting.model_dump_json(indent=2).encode("utf-8")


def filename(meeting: Meeting) -> str:
    parts = [meeting.metadata.meeting_date.value or "日期未知", meeting.metadata.company.value or "公司未知",
             meeting.metadata.title.value or "会议纪要", meeting.meeting_id[-8:], f"v{meeting.revision}"]
    safe = [re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", p).strip(" .")[:60] or "未知" for p in parts]
    return "_".join(safe)
