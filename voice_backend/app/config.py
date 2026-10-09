from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Speech-to-text provider. "inworld" reuses INWORLD_API_KEY; "fennec" needs FENNEC_API_KEY.
    asr_provider: Literal["inworld", "fennec"] = "inworld"

    # Inworld STT (used when ASR_PROVIDER=inworld)
    inworld_stt_model_id: str = "inworld/inworld-stt-1"
    # Turn detection: raise the silences if the agent cuts you off, lower them for snappier replies.
    inworld_stt_eot_confidence: float = 0.5
    inworld_stt_min_silence_ms: int = 200
    inworld_stt_max_silence_ms: int = 800

    # Fennec ASR (used when ASR_PROVIDER=fennec)
    fennec_api_key: str = ""

    # Mic audio sent by the browser (shared by both ASR providers)
    fennec_sample_rate: int = 16000
    fennec_channels: int = 1

    # Baseten
    baseten_api_key: str = ""
    baseten_base_url: str = "https://inference.baseten.co/v1"
    baseten_model: str = "Qwen/Qwen3-235B-A22B-Instruct-2507"

    # Inworld (TTS, and STT when ASR_PROVIDER=inworld)
    inworld_api_key: str = ""
    inworld_model_id: str = "inworld-tts-2-flash"
    inworld_voice_id: str = "Olivia"
    inworld_sample_rate: int = 48000
    # Language for both STT and TTS: "es", "en", ... Empty = let Inworld auto-detect.
    inworld_language: str = "es"
    # Tone/delivery direction for TTS, written in English. Only inworld-tts-2 honours it. Empty = none.
    inworld_instruction: str = ""

    # Agent persona. Empty = use the default prompt in agent/llm_client.py
    agent_system_prompt: str = ""

    # Twilio phone calls (optional). Calls stay disabled until the first three are set.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    # Your Twilio number in international format, e.g. +15551234567
    twilio_phone_number: str = ""
    # A secret you choose. Required to place outbound calls (POST /twilio/call and the /llamar page).
    call_api_key: str = ""
    # Comma-separated numbers that may be called, in international format. Empty = any number.
    twilio_allowed_numbers: str = ""
    # Public https address of this deployment as Twilio reaches it. Empty = taken from the request.
    public_base_url: str = ""

    # Phone calls: record them at Twilio and tell the other person so in the greeting.
    call_recording: bool = True
    # Hard limit for one phone call, in seconds.
    call_max_seconds: int = 420

    # Telesales campaign (/campana). Results are kept in a private Vercel Blob store; Vercel adds this
    # variable when the store is connected to the project.
    blob_read_write_token: str = ""
    # Local folder used instead of the Blob store (development only).
    campaign_local_dir: str = ""
    # Campaign calls are only placed Monday to Saturday between these local hours.
    call_hours: str = "8-18"
    # Costa Rica is UTC-6 all year.
    call_utc_offset: int = -6

    def missing_keys(self) -> list[str]:
        """Names of the required env vars that are not set (never returns values)."""
        required = {
            "BASETEN_API_KEY": self.baseten_api_key,
            "INWORLD_API_KEY": self.inworld_api_key,
        }
        if self.asr_provider == "fennec":
            required["FENNEC_API_KEY"] = self.fennec_api_key
        return [name for name, value in required.items() if not value.strip()]


settings = Settings()
