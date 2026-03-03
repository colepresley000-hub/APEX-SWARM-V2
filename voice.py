"""
APEX SWARM - Voice Module
===========================
Talk to your swarm. Agents talk back.

Features:
  1. Speech-to-Text (STT) — transcribe voice messages from Telegram/Discord
     - OpenAI Whisper API
     - Groq Whisper (faster, cheaper)
     - Deepgram (real-time)
  2. Text-to-Speech (TTS) — agents respond with voice
     - OpenAI TTS (alloy, echo, fable, onyx, nova, shimmer)
     - ElevenLabs (ultra-realistic)
     - Google Cloud TTS
  3. Voice Commands — natural language → agent deployment
  4. Voice Notes — agents send voice summaries back
  5. Conversation Mode — back-and-forth voice chat with agents

File: voice.py
"""

import asyncio
import base64
import io
import json
import logging
import os
import tempfile
from typing import Optional

import httpx

logger = logging.getLogger("apex-swarm")

# ─── CONFIG ───────────────────────────────────────────────

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Default providers
STT_PROVIDER = os.getenv("STT_PROVIDER", "auto")  # auto, openai, groq, deepgram
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "auto")   # auto, openai, elevenlabs
TTS_VOICE = os.getenv("TTS_VOICE", "nova")          # OpenAI: alloy, echo, fable, onyx, nova, shimmer
TTS_ELEVENLABS_VOICE = os.getenv("TTS_ELEVENLABS_VOICE", "Rachel")


# ─── SPEECH-TO-TEXT ──────────────────────────────────────

class SpeechToText:
    """Transcribe audio from any source."""

    @staticmethod
    def _get_provider() -> str:
        if STT_PROVIDER != "auto":
            return STT_PROVIDER
        if GROQ_API_KEY:
            return "groq"  # fastest
        if OPENAI_API_KEY:
            return "openai"
        if DEEPGRAM_API_KEY:
            return "deepgram"
        return "none"

    @staticmethod
    async def transcribe(audio_bytes: bytes, filename: str = "audio.ogg", language: str = None) -> dict:
        """Transcribe audio bytes to text. Returns {text, language, duration, provider}."""
        provider = SpeechToText._get_provider()

        if provider == "groq":
            return await SpeechToText._transcribe_groq(audio_bytes, filename, language)
        elif provider == "openai":
            return await SpeechToText._transcribe_openai(audio_bytes, filename, language)
        elif provider == "deepgram":
            return await SpeechToText._transcribe_deepgram(audio_bytes, filename, language)
        else:
            return {"error": "No STT provider configured. Set OPENAI_API_KEY or GROQ_API_KEY.", "text": ""}

    @staticmethod
    async def _transcribe_groq(audio_bytes: bytes, filename: str, language: str = None) -> dict:
        """Groq Whisper — fastest transcription."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                files = {"file": (filename, audio_bytes, "audio/ogg")}
                data = {"model": "whisper-large-v3-turbo", "response_format": "json"}
                if language:
                    data["language"] = language

                resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    files=files,
                    data=data,
                )

            if resp.status_code != 200:
                return {"error": f"Groq STT error: {resp.status_code}", "text": ""}

            result = resp.json()
            return {
                "text": result.get("text", ""),
                "language": result.get("language", ""),
                "provider": "groq",
            }
        except Exception as e:
            logger.error(f"Groq STT failed: {e}")
            return {"error": str(e), "text": ""}

    @staticmethod
    async def _transcribe_openai(audio_bytes: bytes, filename: str, language: str = None) -> dict:
        """OpenAI Whisper transcription."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                files = {"file": (filename, audio_bytes, "audio/ogg")}
                data = {"model": "whisper-1", "response_format": "json"}
                if language:
                    data["language"] = language

                resp = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    files=files,
                    data=data,
                )

            if resp.status_code != 200:
                return {"error": f"OpenAI STT error: {resp.status_code}", "text": ""}

            result = resp.json()
            return {
                "text": result.get("text", ""),
                "language": result.get("language", ""),
                "provider": "openai",
            }
        except Exception as e:
            logger.error(f"OpenAI STT failed: {e}")
            return {"error": str(e), "text": ""}

    @staticmethod
    async def _transcribe_deepgram(audio_bytes: bytes, filename: str, language: str = None) -> dict:
        """Deepgram transcription."""
        try:
            params = {"model": "nova-2", "smart_format": "true"}
            if language:
                params["language"] = language

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.deepgram.com/v1/listen",
                    headers={
                        "Authorization": f"Token {DEEPGRAM_API_KEY}",
                        "Content-Type": "audio/ogg",
                    },
                    params=params,
                    content=audio_bytes,
                )

            if resp.status_code != 200:
                return {"error": f"Deepgram error: {resp.status_code}", "text": ""}

            result = resp.json()
            transcript = result.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
            return {
                "text": transcript,
                "language": result.get("results", {}).get("channels", [{}])[0].get("detected_language", ""),
                "provider": "deepgram",
            }
        except Exception as e:
            logger.error(f"Deepgram STT failed: {e}")
            return {"error": str(e), "text": ""}


# ─── TEXT-TO-SPEECH ──────────────────────────────────────

class TextToSpeech:
    """Convert agent responses to audio."""

    @staticmethod
    def _get_provider() -> str:
        if TTS_PROVIDER != "auto":
            return TTS_PROVIDER
        if OPENAI_API_KEY:
            return "openai"
        if ELEVENLABS_API_KEY:
            return "elevenlabs"
        return "none"

    @staticmethod
    async def synthesize(text: str, voice: str = None, speed: float = 1.0) -> dict:
        """Convert text to audio bytes. Returns {audio_bytes, format, provider, duration_estimate}."""
        provider = TextToSpeech._get_provider()

        # Truncate very long text for TTS
        if len(text) > 4000:
            text = text[:4000] + "... Message truncated for voice output."

        if provider == "openai":
            return await TextToSpeech._synthesize_openai(text, voice, speed)
        elif provider == "elevenlabs":
            return await TextToSpeech._synthesize_elevenlabs(text, voice)
        else:
            return {"error": "No TTS provider configured. Set OPENAI_API_KEY or ELEVENLABS_API_KEY.", "audio_bytes": None}

    @staticmethod
    async def _synthesize_openai(text: str, voice: str = None, speed: float = 1.0) -> dict:
        """OpenAI TTS."""
        voice = voice or TTS_VOICE
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "tts-1",
                        "input": text,
                        "voice": voice,
                        "speed": speed,
                        "response_format": "opus",
                    },
                )

            if resp.status_code != 200:
                return {"error": f"OpenAI TTS error: {resp.status_code}", "audio_bytes": None}

            return {
                "audio_bytes": resp.content,
                "format": "opus",
                "provider": "openai",
                "voice": voice,
                "duration_estimate": len(text) / 15,  # rough chars/sec estimate
            }
        except Exception as e:
            logger.error(f"OpenAI TTS failed: {e}")
            return {"error": str(e), "audio_bytes": None}

    @staticmethod
    async def _synthesize_elevenlabs(text: str, voice: str = None) -> dict:
        """ElevenLabs TTS — ultra-realistic voices."""
        voice_id = voice or TTS_ELEVENLABS_VOICE
        try:
            # First resolve voice name to ID if needed
            if not voice_id.startswith("EXA") and len(voice_id) > 10:
                # Looks like a voice ID already
                pass
            else:
                # Use a default voice ID — Rachel
                voice_id = "21m00Tcm4TlvDq8ikWAM"

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                    headers={
                        "xi-api-key": ELEVENLABS_API_KEY,
                        "Content-Type": "application/json",
                    },
                    json={
                        "text": text,
                        "model_id": "eleven_monolingual_v1",
                        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                    },
                )

            if resp.status_code != 200:
                return {"error": f"ElevenLabs error: {resp.status_code}", "audio_bytes": None}

            return {
                "audio_bytes": resp.content,
                "format": "mp3",
                "provider": "elevenlabs",
                "voice": voice_id,
                "duration_estimate": len(text) / 15,
            }
        except Exception as e:
            logger.error(f"ElevenLabs TTS failed: {e}")
            return {"error": str(e), "audio_bytes": None}


# ─── TELEGRAM VOICE HANDLER ─────────────────────────────

async def download_telegram_voice(file_id: str) -> Optional[bytes]:
    """Download a voice message from Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Get file path
            resp = await client.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile",
                params={"file_id": file_id},
            )
            if resp.status_code != 200:
                return None
            file_path = resp.json().get("result", {}).get("file_path", "")
            if not file_path:
                return None

            # Download file
            resp = await client.get(
                f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
            )
            if resp.status_code != 200:
                return None
            return resp.content
    except Exception as e:
        logger.error(f"Telegram voice download failed: {e}")
        return None


async def send_telegram_voice(chat_id, audio_bytes: bytes, caption: str = ""):
    """Send a voice message to Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            files = {"voice": ("response.ogg", audio_bytes, "audio/ogg")}
            data = {"chat_id": str(chat_id)}
            if caption:
                data["caption"] = caption[:1024]
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVoice",
                files=files,
                data=data,
            )
    except Exception as e:
        logger.error(f"Telegram voice send failed: {e}")


async def send_discord_voice(channel_id: str, audio_bytes: bytes, filename: str = "response.ogg"):
    """Send an audio file to Discord."""
    discord_token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not discord_token:
        return
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            files = {"file": (filename, audio_bytes, "audio/ogg")}
            await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers={"Authorization": f"Bot {discord_token}"},
                files=files,
            )
    except Exception as e:
        logger.error(f"Discord voice send failed: {e}")


# ─── VOICE PIPELINE ─────────────────────────────────────

class VoicePipeline:
    """Full voice pipeline: receive voice → transcribe → execute → synthesize → send back."""

    def __init__(self):
        self.stt = SpeechToText()
        self.tts = TextToSpeech()
        self._voice_enabled_chats: set = set()  # chats with voice response mode on

    def enable_voice_response(self, channel_id: str):
        self._voice_enabled_chats.add(channel_id)

    def disable_voice_response(self, channel_id: str):
        self._voice_enabled_chats.discard(channel_id)

    def is_voice_enabled(self, channel_id: str) -> bool:
        return channel_id in self._voice_enabled_chats

    async def process_voice_message(
        self,
        audio_bytes: bytes,
        platform: str,
        channel_id: str,
        user_id: str = "",
        filename: str = "voice.ogg",
        respond_with_voice: bool = True,
    ) -> dict:
        """Full pipeline: transcribe → return text (caller handles agent execution)."""

        # Step 1: Transcribe
        transcript = await self.stt.transcribe(audio_bytes, filename)
        if transcript.get("error") or not transcript.get("text"):
            return {
                "error": transcript.get("error", "Transcription failed"),
                "text": "",
                "platform": platform,
                "channel_id": channel_id,
            }

        text = transcript["text"]
        logger.info(f"🎙️ Voice transcribed ({transcript.get('provider', '?')}): {text[:100]}")

        return {
            "text": text,
            "language": transcript.get("language", ""),
            "provider": transcript.get("provider", ""),
            "platform": platform,
            "channel_id": channel_id,
            "user_id": user_id,
        }

    async def synthesize_and_send(
        self,
        text: str,
        platform: str,
        channel_id: str,
        voice: str = None,
    ):
        """Synthesize text and send as voice to the channel."""
        # Generate audio
        result = await self.tts.synthesize(text, voice=voice)
        if result.get("error") or not result.get("audio_bytes"):
            logger.error(f"TTS failed: {result.get('error')}")
            return

        audio = result["audio_bytes"]
        logger.info(f"🔊 Voice synthesized ({result.get('provider', '?')}, {result.get('voice', '?')})")

        # Send to platform
        if platform == "telegram":
            await send_telegram_voice(int(channel_id), audio)
        elif platform == "discord":
            await send_discord_voice(channel_id, audio)
        # Slack doesn't support native voice messages easily — skip

    async def get_voice_status(self) -> dict:
        """Return voice system status."""
        stt_provider = self.stt._get_provider()
        tts_provider = self.tts._get_provider()
        return {
            "stt": {
                "provider": stt_provider,
                "available": stt_provider != "none",
                "supported": ["openai", "groq", "deepgram"],
            },
            "tts": {
                "provider": tts_provider,
                "available": tts_provider != "none",
                "supported": ["openai", "elevenlabs"],
                "voice": TTS_VOICE,
            },
            "voice_enabled_chats": len(self._voice_enabled_chats),
        }


# ─── AVAILABLE VOICES ───────────────────────────────────

VOICE_OPTIONS = {
    "openai": {
        "alloy": "Neutral, balanced",
        "echo": "Warm, male",
        "fable": "Expressive, British",
        "onyx": "Deep, authoritative",
        "nova": "Friendly, female (default)",
        "shimmer": "Soft, gentle",
    },
    "elevenlabs": {
        "Rachel": "American, calm (default)",
        "Domi": "American, assertive",
        "Bella": "American, warm",
        "Antoni": "American, male",
        "Elli": "American, young female",
        "Josh": "American, deep male",
        "Arnold": "American, older male",
        "Sam": "American, raspy male",
    },
}


# ─── GLOBAL INSTANCE ────────────────────────────────────

voice_pipeline = VoicePipeline()
