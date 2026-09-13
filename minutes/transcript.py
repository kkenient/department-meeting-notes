"""Text-only input has no audio dependency; original lines remain unchanged."""

import re

from .models import Meeting, Metadata, MetaValue, Participant, Segment

TIMESTAMP = re.compile(r"^\s*\[?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\]?\s*")
SPEAKER = re.compile(r"^([^：:\n]{1,24})[：:]\s*")
MAX_CHARS = 12_000
MAX_SEGMENTS = 300


def parse_text(text: str) -> tuple[list[Segment], list[Participant]]:
    if not text.strip():
        raise ValueError("请先粘贴会议文本或上传 TXT。")
    segments, participants = [], {}
    for line in text.splitlines():
        if not line.strip():
            continue
        time_match = TIMESTAMP.match(line)
        start = None
        remainder = line
        if time_match:
            h, m, s = (int(x or 0) for x in time_match.groups())
            if m < 60 and s < 60:
                start = (h * 3600 + m * 60 + s) * 1000
                remainder = line[time_match.end():]
        speaker_match = SPEAKER.match(remainder)
        pid = None
        if speaker_match:
            label = speaker_match.group(1).strip()
            if label not in participants:
                participants[label] = Participant(
                    participant_id=f"p{len(participants) + 1}", speaker_label=label)
            pid = participants[label].participant_id
        segments.append(Segment(segment_id=f"s{len(segments) + 1}", text=line,
                                order=len(segments), participant_id=pid, start_ms=start))
    return segments, list(participants.values())


def new_meeting(text: str, title: str = "", company: str = "", meeting_date: str = "") -> Meeting:
    segments, participants = parse_text(text)
    return Meeting(segments=segments, participants=participants, metadata=Metadata(
        title=MetaValue(value=title.strip() or None),
        company=MetaValue(value=company.strip() or None),
        meeting_date=MetaValue(value=meeting_date.strip() or None),
    ))


def check_input_limit(meeting: Meeting) -> None:
    if sum(len(s.text) for s in meeting.segments) > MAX_CHARS or len(meeting.segments) > MAX_SEGMENTS:
        raise ValueError("当前文本版本支持最多 12,000 字符、300 个非空段落。请拆分输入；原文已保留，未截断。")


def time_label(ms: int | None) -> str:
    if ms is None:
        return "无时间戳"
    seconds = ms // 1000
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
