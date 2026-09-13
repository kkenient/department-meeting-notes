"""Business invariants and the common export gate (independent of UI)."""

import hashlib
import json

from .models import (
    Evidence, Extraction, Fact, HIGH_RISK, KIND_SECTION, Meeting, Review,
    SECTIONS, now, uid,
)


def rebuild_sections(meeting: Meeting) -> None:
    for section in meeting.sections:
        section.fact_ids = [f.fact_id for f in meeting.facts
                            if KIND_SECTION[f.details.kind] == section.section_id]


def edit_copy(meeting: Meeting) -> Meeting:
    """All content changes invalidate all reviews; confirmed history is untouched."""
    result = meeting.model_copy(deep=True)
    result.revision += 1
    result.reviews = []
    result.status = "pending_review" if result.processing == "complete" else "draft"
    result.updated_at = now()
    return result


def evidence_errors(meeting: Meeting) -> list[str]:
    segments = {s.segment_id: s for s in meeting.segments}
    facts = {f.fact_id: f for f in meeting.facts}
    errors = []
    participants = {p.participant_id: p for p in meeting.participants}
    if len(segments) != len(meeting.segments) or len(facts) != len(meeting.facts):
        errors.append("段落或事实 ID 重复")
    if len(participants) != len(meeting.participants):
        errors.append("参会人 ID 重复")
    for segment in meeting.segments:
        if segment.participant_id and segment.participant_id not in participants:
            errors.append("段落关联了不存在的参会人")
    for fact in meeting.facts:
        if not fact.evidence and fact.source != "user_added":
            errors.append(f"{fact.fact_id} 缺少原文证据")
        for ev in fact.evidence:
            seg = segments.get(ev.segment_id)
            if (not seg or seg.text[ev.char_start:ev.char_end] != ev.quote
                    or ev.char_end > len(seg.text)
                    or (seg and (ev.start_ms != seg.start_ms or ev.end_ms != seg.end_ms))):
                errors.append(f"{fact.fact_id} 的证据无法匹配原文")
        if fact.details.kind == "summary" and fact.source != "user_added":
            if not fact.derived_from_fact_ids:
                errors.append(f"{fact.fact_id} 总结缺少关联事实")
        for ref in fact.derived_from_fact_ids:
            if ref not in facts or ref == fact.fact_id or facts[ref].details.kind == "summary":
                errors.append(f"{fact.fact_id} 总结关联无效")
            elif not any(ev == source for ev in fact.evidence for source in facts[ref].evidence):
                errors.append(f"{fact.fact_id} 总结证据未覆盖关联事实")
        if fact.details.kind in ("commitment", "action"):
            owner = participants.get(fact.details.owner_id)
            if fact.details.owner_id and not owner:
                errors.append(f"{fact.fact_id} 责任人不存在")
            if owner and owner.side != fact.details.owner_side:
                errors.append(f"{fact.fact_id} 责任人所属方与映射不一致")
            if not owner and fact.details.owner_side != "未知":
                errors.append(f"{fact.fact_id} 无责任人时所属方必须未知")
            if fact.details.kind == "commitment" and fact.details.owner_side not in ("我方", "未知"):
                errors.append(f"{fact.fact_id} 非我方承诺应放入动作或待确认事项")
        if fact.details.kind == "customer" and fact.details.major_confirmed and not fact.details.major_basis:
            errors.append(f"{fact.fact_id} 大客户判断缺少依据")
    expected_sections = [(sid, title, i) for i, (sid, title) in enumerate(SECTIONS)]
    if [(s.section_id, s.title, s.order) for s in meeting.sections] != expected_sections:
        errors.append("八节大纲结构不正确")
    for section in meeting.sections:
        expected = [f.fact_id for f in meeting.facts if KIND_SECTION[f.details.kind] == section.section_id]
        if section.fact_ids != expected:
            errors.append("大纲与事实索引不一致")
    return errors


def fact_warnings(fact: Fact, meeting: Meeting) -> list[str]:
    warnings = []
    if meeting.audio and any(e.segment_id in meeting.audio.ambiguous_segment_ids for e in fact.evidence):
        warnings.append("原文存在说话人切换或重叠发言，归属需人工核对。")
    if fact.information_state in ("uncertain", "conflicting"):
        warnings.append("信息不确定或存在冲突，请核对并明确保留待跟进状态。")
    if fact.details.kind in ("commitment", "action"):
        if fact.details.owner_side == "未知" or not fact.details.owner_id:
            warnings.append("责任人或所属方未知，请确认未知状态或补充身份。")
        if fact.details.original_time and fact.details.deadline is None:
            warnings.append("时间未精确归一化，保留原始时间表达。")
        if fact.details.kind == "commitment" and fact.details.certainty != "明确承诺":
            warnings.append("承诺语气待核对，不能把意向当成确定承诺。")
    if fact.details.kind == "customer" and not fact.details.major_confirmed:
        warnings.append("提及客户，大客户身份待确认。")
    if fact.source == "user_added":
        warnings.append("人工补充，未声称来自会议原文。")
    return warnings


def install_extraction(meeting: Meeting, extraction: Extraction, mode: str = "live") -> Meeting:
    result = edit_copy(meeting)
    segments = {s.segment_id: s for s in result.segments}
    if len({f.fact_id for f in extraction.facts}) != len(extraction.facts):
        raise ValueError("抽取结果存在重复事实 ID。")
    ids = {f.fact_id: uid("f") for f in extraction.facts}
    built = []
    for candidate in extraction.facts:
        evidence = []
        for quote in candidate.evidence:
            seg = segments.get(quote.segment_id)
            if not seg:
                raise ValueError("抽取引用了不存在的原文段落。")
            start = -1
            for _ in range(quote.occurrence + 1):
                start = seg.text.find(quote.quote, start + 1)
                if start < 0:
                    raise ValueError("抽取的引用与原文不一致，未采纳结果。")
            evidence.append(Evidence(segment_id=seg.segment_id, quote=quote.quote,
                                     char_start=start, char_end=start + len(quote.quote),
                                     start_ms=seg.start_ms, end_ms=seg.end_ms))
        try:
            refs = [ids[r] for r in candidate.derived_from_fact_ids]
        except KeyError:
            raise ValueError("总结关联了不存在的事实。") from None
        fact = Fact(fact_id=ids[candidate.fact_id], statement=candidate.statement,
                    details=candidate.details, information_state=candidate.information_state,
                    evidence=evidence, derived_from_fact_ids=refs,
                    source="demo" if mode == "demo" else "model")
        fact.warnings = fact_warnings(fact, result)
        built.append(fact)
    result.facts = built
    result.mode = mode
    result.processing = "complete"
    result.status = "pending_review"
    result.last_error = None
    rebuild_sections(result)
    errors = evidence_errors(result)
    if errors:
        raise ValueError("；".join(errors))
    return result


def required_targets(meeting: Meeting) -> dict[str, str]:
    targets = {f"section:{s.section_id}": s.title for s in meeting.sections}
    targets.update({f"category:{kind}": f"{kind} 类别遗漏核对" for kind in HIGH_RISK})
    targets.update({f"fact:{f.fact_id}": f.statement for f in meeting.facts if f.details.kind in HIGH_RISK})
    return targets


def reviewed(meeting: Meeting, target: str) -> bool:
    return any(r.target_id == target and r.revision == meeting.revision
               and r.content_hash == content_hash(meeting) for r in meeting.reviews)


def content_hash(meeting: Meeting) -> str:
    data = meeting.model_dump(mode="json", exclude={"reviews", "status", "updated_at", "last_error"})
    # Preserve v0.1.0 review fingerprints for existing text-only records.
    if meeting.audio is None:
        data.pop("audio", None)
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def set_review(meeting: Meeting, target: str, checked: bool, acknowledge_unknown: bool = False) -> Meeting:
    if target not in required_targets(meeting):
        raise ValueError("未知确认对象。")
    result = meeting.model_copy(deep=True)
    result.reviews = [r for r in result.reviews if r.target_id != target]
    if checked:
        if result.processing != "complete" or evidence_errors(result):
            raise ValueError("请先完成整理并解决证据或数据错误。")
        if target.startswith("fact:"):
            fact = next(f for f in result.facts if f.fact_id == target[5:])
            if fact_warnings(fact, result) and not acknowledge_unknown:
                raise ValueError("该条目有待确认事项，请明确确认保留未知状态或先修改。")
        result.reviews.append(Review(target_id=target, revision=result.revision, content_hash=content_hash(result),
                                     decision="unknown_acknowledged" if acknowledge_unknown else "confirmed"))
    result.status = "pending_review"
    result.updated_at = now()
    return result


def export_blockers(meeting: Meeting) -> list[str]:
    errors = evidence_errors(meeting)
    if meeting.audio and meeting.audio.transcription_state != "complete":
        errors.append("音频尚未完整转写，不能正式导出。")
    if meeting.processing != "complete":
        errors.append("整理尚未完成，不能正式导出。")
    errors.extend(f"未确认：{label}" for key, label in required_targets(meeting).items()
                  if not reviewed(meeting, key))
    return errors


def finalize(meeting: Meeting) -> Meeting:
    errors = export_blockers(meeting)
    if errors:
        raise ValueError("；".join(errors))
    result = meeting.model_copy(deep=True)
    result.status = "confirmed"
    result.updated_at = now()
    return result


def require_exportable(meeting: Meeting) -> None:
    blockers = export_blockers(meeting)
    if meeting.status != "confirmed":
        blockers.append("请先保存为已确认版本。")
    if blockers:
        raise ValueError("；".join(blockers))
