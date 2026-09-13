"""Streamlit controls for local audio; no model loads during page rendering."""

from pathlib import Path

import streamlit as st

from .assets import AudioAssets, AudioError, RETENTION
from .pipeline import transcribe_meeting
from ..transcript import time_label


def player(meeting, settings, start_ms=0, end_ms=None):
    if not meeting.audio:
        return
    try:
        assets = AudioAssets(settings.data_dir)
        path = assets.playable_path(meeting.audio)
        if path is None:
            st.info("原音频已删除或丢失，无法回听；文字证据仍保留。")
        else:
            path = assets.verified_path(meeting.audio)
            formats = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav"}
            # st.audio uses seconds; keep segment boundaries for short evidence playback.
            st.audio(str(path), format=formats[path.suffix.lower()], start_time=(start_ms or 0) / 1000,
                     end_time=end_ms / 1000 if end_ms is not None else None)
    except AudioError as exc:
        st.error(str(exc))


def audio_panel(meeting, settings, store, persist):
    if not meeting.audio:
        return
    assets = AudioAssets(settings.data_dir)
    audio = meeting.audio
    with st.expander("录音与本地转写", expanded=audio.transcription_state != "complete"):
        st.write(audio.original_name)
        state = {"not_started": "等待转写", "complete": "已完成转写", "failed": "转写失败，可重试"}
        st.caption(f"{audio.size_bytes / 1024 / 1024:.1f} MB · {state[audio.transcription_state]} · 时长 {time_label(audio.duration_ms) if audio.duration_ms else '待校验'}")
        player(meeting, settings)
        for warning in audio.warnings:
            st.warning(warning)
        st.caption("音频在本机处理。转写完成后，先检查发言人，再单独决定是否把文字发送到 DeepSeek。重新转写会创建新修订，旧纪要不覆盖。")
        st.caption(f"当前模型：{settings.asr_model} · {settings.asr_device} / {settings.asr_compute_type}。未缓存的模型需先按使用说明下载，点击转写不会自动下载。")
        if settings.cloud_mvp:
            st.info("云端免费资源有限，建议先用短录音测试。首次转写前需下载模型，服务器重建后可能需要重新下载。")
            if st.button("准备云端转写模型", key=f"prepare_model_{meeting.meeting_id}"):
                try:
                    from faster_whisper.utils import download_model
                    with st.spinner("正在下载官方 small 转写模型…"):
                        download_model("small", cache_dir=str(settings.data_dir / "models"))
                    st.success("small 模型已准备，可以开始转写。")
                except Exception:
                    st.error("模型下载失败，请稍后重试。文字整理仍可使用。")
        if st.button("开始本地转写" if audio.transcription_state != "complete" else "重新转写为新草稿", key=f"transcribe_{meeting.meeting_id}"):
            checkpoint = store.editable_copy(meeting)
            checkpoint.audio.transcription_state = "not_started"
            checkpoint.processing = "not_started"
            checkpoint.status = "draft"
            checkpoint.last_error = None
            persist(checkpoint)
            try:
                with st.spinner("正在校验录音、转写并尝试区分发言人…"):
                    result = transcribe_meeting(checkpoint, settings)
                persist(result)
                st.rerun()
            except (AudioError, OSError) as exc:
                checkpoint.audio.transcription_state = "failed"
                checkpoint.last_error = str(exc) if isinstance(exc, AudioError) else "无法读取本地音频。原录音记录和旧版本已保留。"
                persist(checkpoint)
                st.rerun()
        try:
            row = assets.row(audio.asset_id)
        except AudioError as exc:
            st.error(str(exc))
            return
        with st.form(f"retention_{audio.asset_id}"):
            policy = st.selectbox("音频保留策略", list(RETENTION), index=list(RETENTION).index(row["retention"]), format_func=RETENTION.get)
            st.caption("删除音频会影响这场会议所有历史修订的回听，文字证据保留。到期清理在应用启动或页面操作时执行。")
            if st.form_submit_button("保存音频保留策略"):
                assets.update_retention(audio.asset_id, policy)
                st.rerun()
