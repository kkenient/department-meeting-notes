"""Audio providers load optional libraries lazily; local models only during inference."""

from dataclasses import dataclass
import csv
import math
import os
from pathlib import Path
from typing import Protocol

from .assets import AudioError
from ..config import ROOT, Settings

MAX_SECONDS = 120 * 60


@dataclass
class Utterance:
    text: str
    start: float
    end: float


@dataclass
class SpeakerTurn:
    speaker: str
    start: float
    end: float


class Transcriber(Protocol):
    def transcribe(self, path: Path) -> list[Utterance]: ...


class Diarizer(Protocol):
    def diarize(self, path: Path) -> list[SpeakerTurn]: ...


class DiarizationUnavailable(AudioError):
    pass


def probe_audio(path: Path, max_seconds=MAX_SECONDS) -> int:
    """Decode frames to verify actual duration, not untrusted container metadata."""
    try:
        import av
    except ImportError:
        raise AudioError("缺少音频解码依赖，请按说明安装 requirements-audio.txt。") from None
    total = 0.0
    try:
        with av.open(str(path)) as container:
            if len(container.streams.audio) != 1:
                raise AudioError("需要恰好一条音频流，请先导出为普通录音文件。")
            for frame in container.decode(audio=0):
                if not frame.sample_rate:
                    raise AudioError("音频采样率无效。")
                total += frame.samples / frame.sample_rate
                if total > max_seconds:
                    raise AudioError("音频超过 120 分钟，请拆分后上传；未截断。")
    except AudioError:
        raise
    except Exception:
        raise AudioError("音频无法完整解码，可能已损坏或不支持该编码。原文件已保留。") from None
    if total <= 0 or not math.isfinite(total):
        raise AudioError("音频没有可解码的声音数据。")
    return max(1, round(total * 1000))


def hotwords(path: Path = ROOT / "hotwords.csv") -> str:
    if not path.exists():
        return ""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        words = [r["standard"].strip() for r in csv.DictReader(stream) if r.get("standard", "").strip()]
    return "、".join(dict.fromkeys(words))[:1500]


class FasterWhisperTranscriber:
    def __init__(self, settings: Settings, model_factory=None):
        self.settings = settings
        self.model_factory = model_factory

    def transcribe(self, path: Path) -> list[Utterance]:
        factory = self.model_factory
        if factory is None:
            try:
                from faster_whisper import WhisperModel
                factory = WhisperModel
            except (ImportError, OSError):
                raise AudioError("本地转写组件不可用，请安装 requirements-audio.txt 并检查运行库。") from None
        try:
            model = factory(self.settings.asr_model, device=self.settings.asr_device,
                            compute_type=self.settings.asr_compute_type,
                            download_root=str(self.settings.data_dir / "models"), local_files_only=True)
            rows, _ = model.transcribe(str(path), language=self.settings.asr_language or None,
                                      beam_size=5, vad_filter=True, initial_prompt=hotwords() or None)
            # Consumption is atomic: generator failure must never accept a partial transcript.
            return [Utterance(s.text.strip(), s.start, s.end) for s in rows if s.text.strip()]
        except AudioError:
            raise
        except Exception:
            raise AudioError("本地转写失败。请检查模型是否已下载、设备及算力配置；原音频已保留，可重试。") from None


class PyannoteDiarizer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def diarize(self, path: Path) -> list[SpeakerTurn]:
        directory = Path(self.settings.diarization_model_dir)
        if not self.settings.diarization_model_dir or not directory.is_dir():
            raise DiarizationUnavailable("未配置本地说话人模型，已保留转写，请手工标注发言人。")
        # Disable model-library telemetry; do not transmit meeting metadata.
        os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
        os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            # faster-whisper may have imported Hub earlier; its constants are cached.
            from huggingface_hub import constants as hub_constants
            hub_constants.HF_HUB_OFFLINE = True
            hub_constants.HF_HUB_DISABLE_TELEMETRY = True
            from pyannote.audio import Pipeline
            import torch
            from faster_whisper.audio import decode_audio
        except (ImportError, OSError):
            raise DiarizationUnavailable("说话人组件未安装或不可用，请手工标注发言人。") from None
        try:
            pipeline = Pipeline.from_pretrained(str(directory.resolve()))
            waveform = torch.from_numpy(decode_audio(str(path))).unsqueeze(0)
            output = pipeline({"waveform": waveform, "sample_rate": 16000})
            annotation = output.speaker_diarization
            return [SpeakerTurn(speaker, turn.start, turn.end)
                    for turn, _, speaker in annotation.itertracks(yield_label=True)]
        except Exception:
            raise AudioError("说话人分离失败，转写结果已保留，请手工标注发言人。") from None
