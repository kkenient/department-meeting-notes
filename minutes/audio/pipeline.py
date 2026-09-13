"""A complete local transcription becomes a new reviewable draft."""

import math
import time

from .assets import AudioAssets, AudioError
from .providers import DiarizationUnavailable, FasterWhisperTranscriber, PyannoteDiarizer, probe_audio
from ..models import Participant, Segment
from ..review import edit_copy, rebuild_sections


def assign_speakers(segments, turns):
    people, uncertain = {}, []
    for seg in segments:
        overlap = {}
        for turn in turns:
            if not (math.isfinite(turn.start) and math.isfinite(turn.end) and turn.end > turn.start >= 0):
                raise AudioError("说话人模型返回的时间范围无效。")
            amount = max(0, min(seg.end_ms / 1000, turn.end) - max(seg.start_ms / 1000, turn.start))
            if amount > 0:
                overlap.setdefault(turn.speaker, []).append((max(seg.start_ms / 1000, turn.start),
                                                             min(seg.end_ms / 1000, turn.end)))
        duration = (seg.end_ms - seg.start_ms) / 1000
        coverage = {}
        for speaker, intervals in overlap.items():
            merged = []
            for start, end in sorted(intervals):
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(end, merged[-1][1])
                else:
                    merged.append([start, end])
            coverage[speaker] = sum(end - start for start, end in merged)
        # Multiple speakers in a transcription segment cannot be safely assigned to one person.
        if len(overlap) != 1 or max(coverage.values(), default=0) < duration * .8:
            uncertain.append(seg.segment_id)
            continue
        speaker = next(iter(overlap))
        if speaker not in people:
            people[speaker] = Participant(participant_id=f"p{len(people)+1}",
                                          speaker_label=f"说话人 {len(people)+1}")
        seg.participant_id = people[speaker].participant_id
    return list(people.values()), uncertain


def transcribe_meeting(meeting, settings, transcriber=None, diarizer=None, probe=probe_audio):
    if meeting.audio is None:
        raise AudioError("这场会议未关联音频。")
    path = AudioAssets(settings.data_dir).verified_path(meeting.audio)
    started = time.monotonic()
    duration = probe(path)
    rows = (transcriber or FasterWhisperTranscriber(settings)).transcribe(path)
    if not rows:
        raise AudioError("未识别出有效发言。请检查录音；不会生成空白的成功结果。")
    segments = []
    previous_start = -1
    for row in rows:
        if not (math.isfinite(row.start) and math.isfinite(row.end)
                and row.end > row.start >= 0 and row.start >= previous_start
                and row.end * 1000 <= duration + 1000):
            raise AudioError("转写时间戳不符合录音范围，未采纳结果。")
        previous_start = row.start
        start, end = round(row.start * 1000), min(round(row.end * 1000), duration)
        if end <= start:
            raise AudioError("转写片段时长无效。")
        segments.append(Segment(segment_id=f"s{len(segments)+1}", text=row.text, order=len(segments),
                                start_ms=start, end_ms=end))
    result = edit_copy(meeting)
    result.segments = segments
    result.facts = []
    result.corrections = []
    result.participants = []
    result.processing = "not_started"
    result.status = "draft"
    result.last_error = None
    audio = result.audio
    audio.duration_ms = duration
    audio.transcription_state = "complete"
    audio.model, audio.device, audio.compute_type = settings.asr_model, settings.asr_device, settings.asr_compute_type
    audio.warnings = []
    try:
        turns = (diarizer or PyannoteDiarizer(settings)).diarize(path)
        result.participants, audio.ambiguous_segment_ids = assign_speakers(segments, turns)
        audio.diarization_state = "complete"
        if audio.ambiguous_segment_ids:
            audio.warnings.append("部分片段未能确定单一说话人（可能有切换、重叠或覆盖不足），请逐段核对。")
    except Exception as exc:
        audio.diarization_state = "unavailable" if isinstance(exc, DiarizationUnavailable) else "failed"
        audio.ambiguous_segment_ids = [s.segment_id for s in segments]
        audio.warnings.append("说话人分离未完成，已保留全部转写，请手工确认发言人。")
        # Roll back any assignments made before a diarizer/assignment error.
        for segment in segments:
            segment.participant_id = None
        result.participants = []
    audio.elapsed_ms = round((time.monotonic() - started) * 1000)
    rebuild_sections(result)
    return result
