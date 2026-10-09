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

    # Agent persona. Empty = use the default prompt in agent/llm_client.py
    agent_system_prompt: str = ""

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
