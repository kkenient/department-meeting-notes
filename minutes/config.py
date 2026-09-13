from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    cloud_mvp: bool = False
    auth_required: bool = False
    registration_invite: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False)
    base_url: str = "https://api.deepseek.com"
    model: str = ""
    data_dir: Path = ROOT / "data"
    monthly_budget: Decimal = Decimal("50")
    input_price: Decimal | None = None
    output_price: Decimal | None = None
    price_updated_at: str = ""
    asr_model: str = "small"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    asr_language: str = "zh"
    diarization_model_dir: str = ""
    audio_retention_policy: str = "7days"

    def live_error(self) -> str | None:
        if not self.api_key:
            return "尚未配置 DEEPSEEK_API_KEY。可先体验示例或手工整理。"
        if not self.model:
            return "尚未配置 DEEPSEEK_MODEL，请填写当前可用的模型 ID。"
        if self.base_url.rstrip("/") not in ("https://api.deepseek.com", "https://api.deepseek.com/v1"):
            return "当前版本仅支持 DeepSeek 官方 HTTPS 接口地址。"
        return None


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env", override=False)

    def number(key: str, default: str = "") -> Decimal | None:
        raw = os.getenv(key, default).strip()
        if not raw:
            return None
        try:
            value = Decimal(raw)
            if not value.is_finite() or value < 0:
                raise InvalidOperation
            return value
        except InvalidOperation:
            raise ValueError(f"配置 {key} 必须是非负数字。") from None

    path = Path(os.getenv("DATA_DIR", "data"))
    if not path.is_absolute():
        path = ROOT / path
    return Settings(cloud_mvp=os.getenv("CLOUD_MVP", "false").lower() == "true",
                    auth_required=os.getenv("AUTH_REQUIRED", "false").lower() == "true",
                    registration_invite=os.getenv("REGISTRATION_INVITE", ""),
                    api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
                    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
                    model=os.getenv("DEEPSEEK_MODEL", "").strip(), data_dir=path,
                    monthly_budget=number("MONTHLY_BUDGET_CNY", "50"),
                    input_price=number("INPUT_PRICE_CNY_PER_MILLION"),
                    output_price=number("OUTPUT_PRICE_CNY_PER_MILLION"),
                    price_updated_at=os.getenv("PRICE_UPDATED_AT", ""),
                    asr_model=os.getenv("ASR_MODEL", "small").strip(),
                    asr_device=os.getenv("ASR_DEVICE", "cpu").strip(),
                    asr_compute_type=os.getenv("ASR_COMPUTE_TYPE", "int8").strip(),
                    asr_language=os.getenv("ASR_LANGUAGE", "zh").strip(),
                    diarization_model_dir=os.getenv("DIARIZATION_MODEL_DIR", "").strip(),
                    audio_retention_policy=os.getenv("AUDIO_RETENTION_POLICY", "7days").strip())
