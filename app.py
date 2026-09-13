"""Run with: python -m streamlit run app.py"""

from decimal import Decimal

import streamlit as st
from pydantic import TypeAdapter, ValidationError

from minutes.config import load_settings
from minutes.auth_ui import require_login
from minutes.audio.assets import AudioAssets, RETENTION
from minutes.audio.ui import audio_panel, player
from minutes.demo import demo_meeting
from minutes.exporting import (
    DETAIL_LABELS, detail_lines, filename, section_lines, to_actions_csv, to_json,
    to_markdown, to_meetings_csv,
)
from minutes.llm import DeepSeekProvider, ProviderError
from minutes.models import Details, Fact, HIGH_RISK, KIND_LABELS, Meeting, Metadata, MetaValue, Participant, uid
from minutes.review import (
    evidence_errors, export_blockers, fact_warnings, finalize,
    rebuild_sections, required_targets, reviewed, set_review,
)
from minutes.storage import Store
from minutes.transcript import MAX_CHARS, new_meeting, time_label

st.set_page_config(page_title="会记 · 商务会议纪要", page_icon="📝", layout="wide")
st.markdown("""<style>
.block-container {max-width: 1400px; padding-top: 4rem;}
h1 {letter-spacing: -.04em;} h2, h3 {letter-spacing: -.02em;}
[data-testid="stMetric"] {background: white; border: 1px solid #dce6e1; border-radius: 12px; padding: 14px;}
[data-testid="stForm"] {background: #fff;}
[class*="st-key-history_row_"] [class*="st-key-sidebar_delete_"] button {
    opacity: 0; pointer-events: none;
}
[class*="st-key-history_row_"]:hover [class*="st-key-sidebar_delete_"] button,
[class*="st-key-history_row_"]:focus-within [class*="st-key-sidebar_delete_"] button {
    opacity: 1; pointer-events: auto;
}
@media (hover: none) {
    [class*="st-key-history_row_"] [class*="st-key-sidebar_delete_"] button {
        opacity: 1; pointer-events: auto;
    }
}
</style>""", unsafe_allow_html=True)

try:
    settings = load_settings()
    if settings.cloud_mvp:
        st.warning("云端 MVP · 账号、会议及录音保存在临时磁盘，服务器重建时可能全部丢失，请及时导出重要纪要。此版本的转写和文件保存发生在云服务器上。")
    require_login(settings)
    store = Store(settings.data_dir / "minutes.sqlite3")
    audio_assets = AudioAssets(settings.data_dir)
    removed_audio = audio_assets.cleanup_due()
except (ValueError, OSError) as exc:
    st.error(f"本地配置或存储不可用：{exc}")
    st.stop()

if removed_audio:
    st.info(f"已按保留策略清理 {len(removed_audio)} 份到期音频；文字证据及纪要仍保留。")


def activate(meeting):
    store.save(meeting)
    st.session_state.meeting = meeting
    st.query_params["meeting"] = meeting.meeting_id
    st.query_params["revision"] = str(meeting.revision)


def persist(meeting):
    try:
        activate(meeting)
    except (ValueError, OSError) as exc:
        st.error(f"保存失败：{exc}")
        st.stop()


def detail_defaults(kind):
    return {"kind": kind, **{
        "company": {"field": "其他情况"}, "market": {"categories": ["未明确"]},
        "commercial": {"topic": "其他"}, "issue": {"reason": "待跟进问题"},
    }.get(kind, {})}


if "meeting" not in st.session_state:
    st.session_state.meeting = None
    if st.query_params.get("meeting"):
        try:
            rev = st.query_params.get("revision")
            st.session_state.meeting = store.load(st.query_params["meeting"], int(rev) if rev else None)
        except ValueError:
            st.warning("原链接的会议版本不可用，可以从历史记录重新打开。")

# Refresh visibility on every rerun, including a tab left open before deletion.
if st.session_state.meeting is not None:
    try:
        store.load(st.session_state.meeting.meeting_id, st.session_state.meeting.revision)
    except ValueError:
        st.session_state.meeting = None
        st.query_params.clear()
        st.info("该会议已不可用；若已删除，可从回收站恢复。")

with st.sidebar:
    st.title("会记")
    st.caption("把每次交流，留成有据可查的记录。")
    if st.button("＋ 新建会议", use_container_width=True):
        st.session_state.meeting = None
        st.query_params.clear()
        st.rerun()
    if st.button("体验虚构示例", use_container_width=True, key="demo"):
        activate(demo_meeting())
        st.rerun()
    st.divider()
    st.subheader("历史会议")
    query = st.text_input("搜索公司、主题或原文", key="search")
    include_drafts = st.checkbox("包含草稿", value=True)
    history = store.search(query, include_drafts)
    if not history:
        st.caption("还没有会议记录。")
    for row in history:
        status = {"draft": "草稿", "pending_review": "待确认", "confirmed": "已确认"}[row["status"]]
        label = f"{row['title'] or '未命名会议'} · {status}"
        with st.container(key=f"history_row_{row['meeting_id']}"):
            title_col, delete_col = st.columns([5, 1], gap="small", vertical_alignment="center")
            if title_col.button(label, key=f"history_{row['meeting_id']}", use_container_width=True):
                st.session_state.meeting = store.load(row["meeting_id"], row["revision"])
                st.query_params["meeting"] = row["meeting_id"]
                st.query_params["revision"] = str(row["revision"])
                st.rerun()
            if delete_col.button("✕", key=f"sidebar_delete_{row['meeting_id']}", help=f"删除会议：{row['title'] or '未命名会议'}"):
                st.session_state.sidebar_delete_target = row["meeting_id"]
            if st.session_state.get("sidebar_delete_target") == row["meeting_id"]:
                st.warning(f"将“{row['title'] or '未命名会议'}”及全部历史版本移入回收站？可恢复，录音仍按原保留策略处理。")
                if st.button("确认删除", key=f"sidebar_confirm_{row['meeting_id']}"):
                    store.delete_meeting(row["meeting_id"])
                    st.session_state.pop("sidebar_delete_target", None)
                    st.session_state.pop(f"delete_requested_{row['meeting_id']}", None)
                    if st.session_state.meeting and st.session_state.meeting.meeting_id == row["meeting_id"]:
                        st.session_state.meeting = None
                        st.query_params.clear()
                    st.rerun()
                if st.button("取消", key=f"sidebar_cancel_{row['meeting_id']}"):
                    st.session_state.pop("sidebar_delete_target", None)
                    st.rerun()
    st.divider()
    with st.expander("回收站"):
        st.caption("可恢复会议及全部历史版本；录音仍按原保留策略清理，恢复会议不会找回已清理的录音。")
        deleted = store.trash()
        if not deleted:
            st.caption("回收站为空。")
        for row in deleted:
            st.write(row["title"] or "未命名会议")
            if st.button("恢复会议", key=f"restore_{row['meeting_id']}"):
                store.restore_meeting(row["meeting_id"])
                st.session_state.meeting = store.load(row["meeting_id"])
                st.query_params["meeting"] = row["meeting_id"]
                st.query_params["revision"] = str(st.session_state.meeting.revision)
                st.rerun()
    st.caption("v0.2.4 · 部门会议库")
    if settings.live_error():
        st.caption("DeepSeek 未就绪 · 可先体验示例")
    else:
        st.caption("DeepSeek 已配置 · 尚需真实调用验证")
    events = store.usage()
    costs = [Decimal(e["estimated_cny"]) for e in events if e.get("estimated_cny") is not None]
    st.caption(f"本月请求：{len(events)} 次")
    if costs:
        total = sum(costs)
        st.caption(f"已知费用估算：¥{total:.4f} / 预算 ¥{settings.monthly_budget}")
        if total >= settings.monthly_budget:
            st.warning("已知费用估算达到本月预算。")
    if len(costs) < len(events):
        st.caption("部分调用费用未知，不能视为零成本。")


def input_page():
    st.caption("商务拜访 / 原厂合作 / 客户沟通")
    st.title("让会议结束，也让信息留下来。")
    st.write("粘贴转写内容，或上传录音在本机转写，再按八项大纲整理。每条信息保留原文依据，确认后再导出。")
    st.info("现在可以先使用虚构示例体验完整流程。真实自动抽取需后续在本机配置 DeepSeek。")
    input_kind = st.radio("输入方式", ["转写文本", "录音文件"], horizontal=True)
    with st.form("new_meeting"):
        st.subheader("新建会议")
        a, b = st.columns(2)
        title = a.text_input("会议主题", placeholder="例如：原厂代理合作初谈")
        company = b.text_input("对方公司", placeholder="未提供可留空")
        meeting_date = st.text_input("会议日期（可留空）", placeholder="YYYY-MM-DD，不使用上传日期代替")
        if input_kind == "转写文本":
            upload = st.file_uploader("或上传 UTF-8 TXT", type=["txt"])
            text = st.text_area("会议转写文本", height=260, placeholder="可以带说话人和时间戳，也可以直接粘贴普通文本。")
            st.caption(f"自动抽取上限 {MAX_CHARS:,} 字符、300 段。上传 TXT 时优先使用文件内容，TXT 上限 5 MB。")
        else:
            upload = st.file_uploader("上传录音", type=["m4a", "mp3", "wav"])
            text = ""
            policies = list(RETENTION)
            default_policy = settings.audio_retention_policy if settings.audio_retention_policy in policies else "7days"
            policy = st.selectbox("音频保留策略", policies, index=policies.index(default_policy), format_func=RETENTION.get)
            st.caption("支持单份最多 500 MB、120 分钟录音。音频只保存在本机；删除后所有修订都无法回听，但文字证据保留。未确认的会议不会按到期规则删除。")
        submit = st.form_submit_button("保存原文，开始整理", type="primary")
    if submit:
        try:
            if input_kind == "录音文件":
                if upload is None:
                    raise ValueError("请先选择录音文件。")
                meeting = Meeting(metadata=Metadata(title=MetaValue(value=title.strip() or None),
                    company=MetaValue(value=company.strip() or None), meeting_date=MetaValue(value=meeting_date.strip() or None)))
                upload.seek(0)
                meeting.audio = audio_assets.save_upload(upload, upload.name, policy)
                activate(meeting)
            else:
                if upload is not None:
                    if upload.size > 5 * 1024 * 1024:
                        raise ValueError("TXT 超过 5 MB，请拆分后上传。")
                    text = upload.getvalue().decode("utf-8-sig")
                activate(new_meeting(text, title, company, meeting_date))
            st.rerun()
        except (UnicodeDecodeError, ValueError) as exc:
            st.error("请提供 UTF-8 编码的 TXT。" if isinstance(exc, UnicodeDecodeError) else f"输入有误：{exc}")


def edit_metadata(meeting):
    with st.expander("会议信息与说话人", expanded=meeting.processing != "complete"):
        with st.form(f"metadata_{meeting.meeting_id}_{meeting.revision}"):
            title = st.text_input("会议主题", value=meeting.metadata.title.value or "")
            company = st.text_input("对方公司", value=meeting.metadata.company.value or "")
            dt = st.text_input("会议日期", value=meeting.metadata.meeting_date.value or "", placeholder="YYYY-MM-DD，可留空")
            st.caption("说话人标签从文本中识别，姓名及所属方由你确认。未知时请保留未知。")
            people = []
            for person in meeting.participants:
                st.markdown(f"**{person.speaker_label}**")
                columns = st.columns(4)
                name = columns[0].text_input("姓名", value=person.name or "", key=f"pn_{meeting.meeting_id}_{person.participant_id}_{meeting.revision}")
                organization = columns[1].text_input("公司", value=person.company or "", key=f"pc_{meeting.meeting_id}_{person.participant_id}_{meeting.revision}")
                sides = ["未知", "我方", "对方", "第三方"]
                side = columns[2].selectbox("所属方", sides, index=sides.index(person.side), key=f"ps_{meeting.meeting_id}_{person.participant_id}_{meeting.revision}")
                role = columns[3].text_input("已明确的角色", value=person.role or "", key=f"pr_{meeting.meeting_id}_{person.participant_id}_{meeting.revision}")
                people.append((person.participant_id, name, organization, side, role))
            save = st.form_submit_button("保存会议信息")
        if save:
            try:
                draft = store.editable_copy(meeting)
                draft.metadata.title.value = title.strip() or None
                draft.metadata.company.value = company.strip() or None
                if dt.strip():
                    from datetime import date
                    date.fromisoformat(dt.strip())
                draft.metadata.meeting_date.value = dt.strip() or None
                for pid, name, organization, side, role in people:
                    p = next(p for p in draft.participants if p.participant_id == pid)
                    p.name, p.company, p.side, p.role = name or None, organization or None, side, role or None
                # Sync structural owner fields; human must re-check all affected text.
                mapping = {p.participant_id: p for p in draft.participants}
                for f in draft.facts:
                    if f.details.kind in ("action", "commitment") and f.details.owner_id in mapping:
                        f.details.owner_side = mapping[f.details.owner_id].side
                persist(draft)
                st.rerun()
            except ValueError:
                st.error("日期格式不正确，请使用 YYYY-MM-DD 或留空。")
        with st.form(f"participant_add_{meeting.meeting_id}_{meeting.revision}"):
            st.caption("文本没有识别到说话人时，可以在此补充参会人。")
            label = st.text_input("新增参会人姓名或标签")
            if st.form_submit_button("添加参会人"):
                if not label.strip():
                    st.error("请填写姓名或标签。")
                else:
                    draft = store.editable_copy(meeting)
                    draft.participants.append(Participant(participant_id=uid("p"), speaker_label=label.strip(), name=label.strip()))
                    persist(draft)
                    st.rerun()
        if meeting.participants and meeting.segments:
            with st.form(f"segment_speaker_{meeting.meeting_id}_{meeting.revision}"):
                segment_id = st.selectbox("修正某段发言人", [s.segment_id for s in meeting.segments],
                    format_func=lambda sid: next(f"{s.segment_id} · {s.text[:40]}" for s in meeting.segments if s.segment_id == sid))
                people_names = {p.participant_id: p.name or p.speaker_label for p in meeting.participants}
                person_id = st.selectbox("该段实际发言人", [None] + list(people_names), format_func=lambda pid: people_names.get(pid, "未知"))
                if st.form_submit_button("保存该段发言人"):
                    draft = store.editable_copy(meeting)
                    next(s for s in draft.segments if s.segment_id == segment_id).participant_id = person_id
                    if draft.audio and segment_id in draft.audio.ambiguous_segment_ids:
                        draft.audio.ambiguous_segment_ids.remove(segment_id)
                    persist(draft)
                    st.rerun()


def detail_editor(fact, meeting):
    values = fact.details.model_dump(mode="json")
    schema = fact.details.model_json_schema()["properties"]
    for name, value in list(values.items()):
        if name == "kind":
            continue
        label = DETAIL_LABELS.get(name, name)
        spec = schema[name]
        non_null = next((v for v in spec.get("anyOf", []) if v.get("type") != "null"), spec)
        if name == "owner_id":
            options = [None] + [p.participant_id for p in meeting.participants]
            names = {p.participant_id: p.name or p.speaker_label for p in meeting.participants}
            values[name] = st.selectbox(label, options, index=options.index(value) if value in options else 0,
                                       format_func=lambda x: names.get(x, "未知"))
        elif "enum" in non_null:
            options = non_null["enum"]
            values[name] = st.selectbox(label, options, index=options.index(value) if value in options else 0)
        elif non_null.get("type") == "boolean":
            options = [None, True, False] if "anyOf" in spec else [True, False]
            values[name] = st.selectbox(label, options, index=options.index(value),
                                       format_func=lambda v: "未明确" if v is None else ("是" if v else "否"))
        elif non_null.get("type") == "array":
            values[name] = st.multiselect(label, non_null["items"].get("enum", []), default=value)
        else:
            values[name] = st.text_input(label, value=str(value or "")) or None
    return values


@st.dialog("核对原文依据")
def show_evidence(meeting, evidence):
    segment = next(s for s in meeting.segments if s.segment_id == evidence.segment_id)
    st.caption(f"{segment.segment_id} · {time_label(segment.start_ms)}")
    st.write("引用片段")
    st.text(evidence.quote)
    st.write("完整原文段落")
    st.text(segment.text)
    player(meeting, settings, evidence.start_ms, evidence.end_ms)
    st.caption("此处显示原始转写文本，未用人工修改后的纪要替换证据。")


def fact_card(meeting, fact):
    with st.container(border=True):
        st.caption(KIND_LABELS[fact.details.kind])
        st.write(fact.statement)
        details = detail_lines(fact, meeting)
        if details:
            st.caption(" · ".join(details))
        warnings = fact_warnings(fact, meeting)
        for warning in warnings:
            st.warning(warning)
        if fact.source == "user_edited":
            st.caption("人工修改 · 原文依据仍保留")
        for i, evidence in enumerate(fact.evidence):
            if st.button(f"查看原文 {evidence.segment_id} · {time_label(evidence.start_ms)}", key=f"e_{fact.fact_id}_{i}"):
                st.session_state.focus_segment = evidence.segment_id
                show_evidence(meeting, evidence)
        with st.expander("修改内容与详细信息"):
            selected_kind = st.selectbox("信息类别", list(KIND_LABELS), index=list(KIND_LABELS).index(fact.details.kind),
                                         format_func=KIND_LABELS.get, key=f"kind_{fact.fact_id}_{meeting.revision}")
            editable_fact = fact.model_copy(deep=True)
            if selected_kind != fact.details.kind:
                editable_fact.details = TypeAdapter(Details).validate_python(detail_defaults(selected_kind))
                st.caption("更换类别后请重新填写详细字段，原文依据保留。")
            with st.form(f"edit_{fact.fact_id}_{meeting.revision}"):
                statement = st.text_area("纪要内容", value=fact.statement)
                states = {"stated": "明确陈述", "explicit_none": "明确没有", "uncertain": "待确认", "conflicting": "存在冲突"}
                state = st.selectbox("信息状态", list(states), index=list(states).index(fact.information_state), format_func=states.get)
                values = detail_editor(editable_fact, meeting)
                save = st.form_submit_button("保存条目修改")
            if save:
                try:
                    draft = store.editable_copy(meeting)
                    target = next(f for f in draft.facts if f.fact_id == fact.fact_id)
                    target.statement = statement.strip()
                    target.details = TypeAdapter(Details).validate_python(values)
                    target.information_state = state
                    target.source = "user_added" if fact.source == "user_added" else "user_edited"
                    target.revision += 1
                    if target.details.kind != "summary":
                        target.derived_from_fact_ids = []
                    target.warnings = fact_warnings(target, draft)
                    rebuild_sections(draft)
                    persist(draft)
                    st.rerun()
                except (ValidationError, ValueError):
                    st.error("请检查必填内容、日期和选项。修改未保存。")
            remove = st.checkbox("删除此条，并移除引用它的总结（旧版本仍保留）", key=f"remove_check_{fact.fact_id}")
            if st.button("删除条目", key=f"remove_{fact.fact_id}", disabled=not remove):
                draft = store.editable_copy(meeting)
                draft.facts = [f for f in draft.facts if f.fact_id != fact.fact_id and fact.fact_id not in f.derived_from_fact_ids]
                rebuild_sections(draft)
                persist(draft)
                st.rerun()
        if fact.details.kind in HIGH_RISK and meeting.status != "confirmed":
            target = f"fact:{fact.fact_id}"
            label = "已核对内容、归属、条件和日期" + ("，确认保留上述未知或待跟进状态" if warnings else "")
            checked = st.checkbox(label, value=reviewed(meeting, target), key=f"check_{fact.fact_id}_{meeting.revision}")
            if checked != reviewed(meeting, target):
                try:
                    persist(set_review(meeting, target, checked, acknowledge_unknown=bool(warnings)))
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


def review_page(meeting):
    st.title(meeting.metadata.title.value or "未命名会议")
    delete_key = f"delete_requested_{meeting.meeting_id}"
    if st.button("删除会议", key=f"delete_{meeting.meeting_id}"):
        st.session_state[delete_key] = True
    if st.session_state.get(delete_key):
        st.warning("确认将整场会议及全部历史版本移入回收站？可以恢复；录音仍按原保留策略处理。")
        confirm, cancel = st.columns(2)
        if confirm.button("确认删除", key=f"confirm_delete_{meeting.meeting_id}"):
            store.delete_meeting(meeting.meeting_id)
            st.session_state.pop(delete_key, None)
            st.session_state.meeting = None
            st.query_params.clear()
            st.rerun()
        if cancel.button("取消", key=f"cancel_delete_{meeting.meeting_id}"):
            st.session_state.pop(delete_key, None)
            st.rerun()
    if meeting.mode == "demo":
        st.info("虚构示例 · 全部内容为手工编写的演示数据，未调用 AI，也不代表真实会议。")
    st.caption(f"{meeting.metadata.company.value or '公司未提供'} · {meeting.metadata.meeting_date.value or '日期未提供'} · 修订 {meeting.revision} · 已保存到本机")
    targets = required_targets(meeting)
    confirmed_count = sum(reviewed(meeting, key) for key in targets)
    a, b, c = st.columns(3)
    a.metric("纪要条目", len(meeting.facts))
    b.metric("审核完成", f"{confirmed_count} / {len(targets)}")
    c.metric("当前状态", {"draft": "草稿", "pending_review": "待确认", "confirmed": "已确认"}[meeting.status])
    audio_panel(meeting, settings, store, persist)
    edit_metadata(meeting)
    with st.expander("自动整理与版本"):
        st.caption("自动整理会将这场会议的原文、元信息和说话人信息发送给 DeepSeek。重新生成将创建新修订，已有版本仍可在历史中打开。")
        consent = st.checkbox("允许将这场会议文本发送到 DeepSeek", key=f"consent_{meeting.meeting_id}")
        if settings.live_error():
            st.info(settings.live_error())
        audio_ready = meeting.audio is None or meeting.audio.transcription_state == "complete"
        if st.button("使用 DeepSeek 整理", type="primary", disabled=bool(settings.live_error()) or not consent or not audio_ready):
            # Persist pre-request checkpoint even if the API fails.
            checkpoint = store.editable_copy(meeting)
            checkpoint.processing = "not_started"
            checkpoint.status = "draft"
            checkpoint.mode = "live"
            persist(checkpoint)
            try:
                with st.spinner("正在整理并核对原文引用…"):
                    result = DeepSeekProvider(settings, audit=store.audit).extract(checkpoint)
                persist(result)
                st.rerun()
            except (ProviderError, ValueError) as exc:
                checkpoint.processing = "failed"
                checkpoint.last_error = str(exc)
                persist(checkpoint)
                st.rerun()
        versions = store.versions(meeting.meeting_id)
        selection = st.selectbox("历史修订", [v["revision"] for v in versions], format_func=lambda v: f"修订 {v}")
        if st.button("打开所选修订"):
            st.session_state.meeting = store.load(meeting.meeting_id, selection)
            st.query_params["revision"] = str(selection)
            st.rerun()
    if meeting.last_error:
        st.error(meeting.last_error)
    errors = evidence_errors(meeting)
    for error in errors[:5]:
        st.error(error)
    left, right = st.columns([0.85, 1.35], gap="large")
    with left:
        st.subheader("原文依据")
        focus = st.session_state.get("focus_segment")
        selected = next((s for s in meeting.segments if s.segment_id == focus), None)
        if selected:
            st.success(f"已定位：{selected.segment_id} · {time_label(selected.start_ms)}")
            st.text(selected.text)
        else:
            st.caption("点击右侧条目的“查看原文”，在此定位核对。")
        with st.container(height=550):
            for segment in meeting.segments:
                st.caption(f"{segment.segment_id} · {time_label(segment.start_ms)}")
                st.text(segment.text)
        if not meeting.audio:
            st.caption("文本模式不包含音频，时间戳只用于原文定位。")
        elif meeting.audio.ambiguous_segment_ids:
            st.caption("标记待核对的片段：" + "、".join(meeting.audio.ambiguous_segment_ids[:30]))
    with right:
        st.subheader("结构化纪要")
        for section in meeting.sections:
            with st.expander(section.title, expanded=section.section_id in ("company", "summary")):
                if section.section_id == "meeting":
                    for line in section_lines(meeting, "meeting"):
                        st.write(line)
                facts = [f for f in meeting.facts if f.fact_id in section.fact_ids]
                for fact in facts:
                    fact_card(meeting, fact)
                if section.section_id == "company":
                    fields = {f.details.field for f in facts}
                    for field in ("营收", "主要产品", "主要应用场景", "公司人数", "其他情况"):
                        if field not in fields:
                            st.caption(f"{field}：未提及")
                if not facts and section.section_id != "meeting":
                    st.caption("未提及")
                if meeting.processing == "complete" and meeting.status != "confirmed":
                    key = f"section:{section.section_id}"
                    checked = st.checkbox("本节已核对，缺失和待确认标记符合原文", value=reviewed(meeting, key),
                                          key=f"section_check_{meeting.meeting_id}_{section.section_id}_{meeting.revision}")
                    if checked != reviewed(meeting, key):
                        try:
                            persist(set_review(meeting, key, checked))
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
        with st.expander("补充遗漏内容"):
            with st.form(f"add_{meeting.meeting_id}_{meeting.revision}"):
                kinds = list(KIND_LABELS)
                kind = st.selectbox("补充到", kinds, format_func=KIND_LABELS.get)
                statement = st.text_area("补充内容", placeholder="手工增加的条目将标记为人工补充。")
                add = st.form_submit_button("添加人工补充")
            if add:
                try:
                    draft = store.editable_copy(meeting)
                    fact = Fact(fact_id=uid("f"), statement=statement.strip(),
                                details=detail_defaults(kind), source="user_added")
                    draft.facts.append(fact)
                    rebuild_sections(draft)
                    persist(draft)
                    st.rerun()
                except ValidationError:
                    st.error("请填写补充内容。")
        if meeting.processing != "complete" and (meeting.audio is None or meeting.audio.transcription_state == "complete"):
            if st.button("已完成手工整理，进入审核"):
                draft = store.editable_copy(meeting)
                draft.processing = "complete"
                draft.status = "pending_review"
                draft.mode = "manual"
                draft.last_error = None
                persist(draft)
                st.rerun()
    st.divider()
    st.subheader("核对遗漏，然后导出")
    st.caption("即使某类没有抽取结果，也请核对原文中是否确实没有遗漏。")
    if meeting.processing == "complete" and meeting.status != "confirmed":
        for kind in HIGH_RISK:
            target = f"category:{kind}"
            checked = st.checkbox(f"{KIND_LABELS[kind]}：已检查是否遗漏（包括未提及的情况）", value=reviewed(meeting, target), key=f"cat_{meeting.meeting_id}_{kind}_{meeting.revision}")
            if checked != reviewed(meeting, target):
                try:
                    persist(set_review(meeting, target, checked))
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    blockers = export_blockers(meeting)
    if meeting.status != "confirmed":
        if blockers:
            st.caption(f"还有 {len(blockers)} 项需要处理。完成后即可保存正式版本。")
        if st.button("保存为已确认版本", type="primary", disabled=bool(blockers)):
            persist(finalize(meeting))
            st.rerun()
    else:
        st.success("已确认，可下载。修改内容会创建新修订并重新审核。")
        base = filename(meeting)
        a, b, c, d = st.columns(4)
        a.download_button("Markdown 纪要", to_markdown(meeting), file_name=base + ".md", mime="text/markdown")
        b.download_button("JSON 完整记录", to_json(meeting), file_name=base + ".json", mime="application/json")
        c.download_button("CSV 会议表", to_meetings_csv(meeting), file_name=base + "_会议.csv", mime="text/csv")
        d.download_button("CSV 行动项", to_actions_csv(meeting), file_name=base + "_行动项.csv", mime="text/csv")


if st.session_state.meeting is None:
    input_page()
else:
    review_page(st.session_state.meeting)
