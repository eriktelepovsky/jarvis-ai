"""How myOrb reads the brief aloud: the browser's own voices, or Microsoft Azure Speech.

With Azure, the server turns the text into an MP3 and keeps it, so a brief that is played again
costs nothing; any browser can play it. A new brief is read in advance in its own language and
in the languages chosen to preload, so pressing Read aloud plays it at once. The browser's
voices differ per device, so which one reads is chosen on each device and kept there.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any
from xml.sax.saxutils import escape

import httpx2
from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from myorb.brief.writer import READ_LANGUAGES, BriefError
from myorb.connections import store
from myorb.connections.providers import ProviderError, request_json, status_error
from myorb.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

Db = Annotated[AsyncSession, Depends(get_db)]

PROVIDER = "voice"
REGION = re.compile(r"^[a-z0-9]{3,30}$")
VOICE_NAME = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-[A-Za-z0-9]+Neural$")
# Natural voices to start with; any other Neural voice of the language can be chosen.
DEFAULT_VOICES = {
    "en": "en-GB-SoniaNeural",
    "sk": "sk-SK-ViktoriaNeural",
    "cs": "cs-CZ-VlastaNeural",
    "de": "de-DE-KatjaNeural",
    "fr": "fr-FR-DeniseNeural",
    "es": "es-ES-ElviraNeural",
    "it": "it-IT-ElsaNeural",
    "pl": "pl-PL-ZofiaNeural",
    "hu": "hu-HU-NoemiNeural",
}
SAMPLES = {
    "en": "Good morning. Here is your brief for today.",
    "sk": "Dobré ráno. Tu je tvoj prehľad na dnes.",
    "cs": "Dobré ráno. Tady je tvůj přehled na dnešek.",
    "de": "Guten Morgen. Hier ist dein Überblick für heute.",
    "fr": "Bonjour. Voici ton résumé du jour.",
    "es": "Buenos días. Aquí tienes tu resumen de hoy.",
    "it": "Buongiorno. Ecco il tuo riepilogo di oggi.",
    "pl": "Dzień dobry. Oto twoje podsumowanie na dziś.",
    "hu": "Jó reggelt. Itt a mai összefoglalód.",
}
OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"


@dataclass
class VoiceSettings:
    provider: str = "browser"  # "browser" or "azure"
    azure_key: str = ""
    azure_region: str = ""
    voices: dict[str, str] = field(default_factory=dict)
    # Languages a new brief is read in as soon as it is written, besides its own.
    preload: list[str] = field(default_factory=list)

    @property
    def azure(self) -> bool:
        return self.provider == "azure" and bool(self.azure_key)

    def voice(self, language: str) -> str:
        return self.voices.get(language) or DEFAULT_VOICES[language]


async def load_voice(request: Request, db: AsyncSession) -> VoiceSettings:
    return await voice_settings(db, request.app.state.secret_box)


async def voice_settings(db: AsyncSession, box: Any) -> VoiceSettings:
    data = await store.get_provider_settings(db, box, PROVIDER)
    return VoiceSettings(**data) if data else VoiceSettings()


# Azure Speech


def _base(region: str) -> str:
    return f"https://{region}.tts.speech.microsoft.com/cognitiveservices"


async def azure_voices(http: httpx2.AsyncClient, key: str, region: str) -> list[dict[str, Any]]:
    """The Neural voices for the languages myOrb reads; also checks the key and region."""
    voices = await request_json(
        http,
        "GET",
        f"{_base(region)}/voices/list",
        "Azure Speech",
        headers={"Ocp-Apim-Subscription-Key": key},
    )
    prefixes = tuple(f"{code}-" for code in READ_LANGUAGES)
    return [
        v
        for v in voices
        if v.get("VoiceType") == "Neural" and v.get("Locale", "").startswith(prefixes)
    ]


async def synthesize(
    http: httpx2.AsyncClient, settings: VoiceSettings, language: str, text: str
) -> bytes:
    """Speech for the text as MP3, in the voice chosen for the language."""
    voice = settings.voice(language)
    locale = "-".join(voice.split("-")[:2])
    paragraphs = "".join(
        f"<p>{escape(part.strip())}</p>" for part in text.split("\n\n") if part.strip()
    )
    ssml = (
        f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='{locale}'>"
        f"<voice name='{escape(voice)}'>{paragraphs}</voice></speak>"
    )
    try:
        response = await http.post(
            f"{_base(settings.azure_region)}/v1",
            content=ssml.encode(),
            headers={
                "Ocp-Apim-Subscription-Key": settings.azure_key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
                "User-Agent": "myOrb",
            },
            timeout=60,
        )
    except httpx2.HTTPError as error:
        raise ProviderError("Azure Speech could not be reached.") from error
    if response.status_code >= 400:
        raise status_error(response, "Azure Speech")
    return response.content


# Pages


def _languages() -> list[tuple[str, str, str]]:
    return [(code, name, tag) for code, (name, tag) in READ_LANGUAGES.items()]


async def _page(
    request: Request,
    settings: VoiceSettings,
    *,
    error: str | None = None,
    message: str | None = None,
    status_code: int = 200,
) -> Response:
    voices: dict[str, list[dict[str, Any]]] = {}
    claude = await request.app.state.brief.settings()
    if settings.azure and error is None:
        try:
            for voice in await azure_voices(
                request.app.state.http, settings.azure_key, settings.azure_region
            ):
                voices.setdefault(voice["Locale"][:2], []).append(voice)
        except ProviderError as problem:
            error = str(problem)
    return request.app.state.templates.TemplateResponse(
        request,
        "voice.html",
        {
            "settings": settings,
            "languages": _languages(),
            "azure_voices": voices,
            "samples": SAMPLES,
            # Always read in advance: the language the brief is written in.
            "brief_language": claude.language if claude else None,
            "error": error,
            "message": message,
        },
        status_code=status_code,
    )


@router.get("/integrations/voice", response_class=HTMLResponse)
async def voice_page(request: Request, db: Db, saved: bool = False) -> Response:
    message = "The voice settings were saved." if saved else None
    return await _page(request, await load_voice(request, db), message=message)


@router.post("/integrations/voice")
async def save_voice(
    request: Request,
    db: Db,
    provider: Annotated[str, Form()] = "browser",
    azure_key: Annotated[str, Form()] = "",
    azure_region: Annotated[str, Form()] = "",
) -> Response:
    existing = await load_voice(request, db)
    form = await request.form()
    voices = {
        code: str(form[f"voice_{code}"])
        for code in READ_LANGUAGES
        if str(form.get(f"voice_{code}", "")).strip()
    }
    # The checkboxes are in the voice list, shown once a key works: kept until then.
    preload = (
        [code for code in READ_LANGUAGES if form.get(f"preload_{code}")]
        if "voices_shown" in form
        else existing.preload
    )
    settings = VoiceSettings(
        provider="azure" if provider == "azure" else "browser",
        azure_key=azure_key.strip() or existing.azure_key,
        azure_region=azure_region.strip().lower() or existing.azure_region,
        voices={**existing.voices, **voices},
        preload=preload,
    )
    if settings.provider == "azure":
        error = None
        if not settings.azure_key or not settings.azure_region:
            error = "Enter the key and the region of your Azure Speech resource."
        elif not REGION.match(settings.azure_region):
            error = "The region is a short name like westeurope, as shown in the Azure portal."
        else:
            try:
                await azure_voices(
                    request.app.state.http, settings.azure_key, settings.azure_region
                )
            except ProviderError as problem:
                error = str(problem)
                if "could not be reached" in error:
                    error += " Check the region."
        if error:
            return await _page(request, settings, error=error, status_code=400)
    data = settings.__dict__
    await store.save_provider_settings(db, request.app.state.secret_box, PROVIDER, data)
    return RedirectResponse("/integrations/voice?saved=true", status_code=303)


async def speech_file(
    app: FastAPI, settings: VoiceSettings, language: str, text: str, name: str
) -> Path:
    """The MP3 for the text, made once and kept; the voice is part of the name."""
    voice = settings.voice(language)
    digest = hashlib.sha256(f"{voice}\n{text}".encode()).hexdigest()[:16]
    folder = app.state.settings.data_dir / "audio"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}-{language}-{digest}.mp3"
    if not path.exists():
        audio = await synthesize(app.state.http, settings, language, text)
        path.write_bytes(audio)
    return path


async def _speech(request: Request, settings: VoiceSettings, language: str, text: str, name: str):
    path = await speech_file(request.app, settings, language, text, name)
    return FileResponse(path, media_type="audio/mpeg")


async def preload_brief(app: FastAPI, brief_id: int, brief_language: str) -> None:
    """Read a new brief in advance, in its own language and the ones chosen to preload
    (translated by Claude first). A failure only means waiting when it is played."""
    async with app.state.sessionmaker() as db:
        settings = await voice_settings(db, app.state.secret_box)
    if not settings.azure:
        return
    languages = [brief_language] + [
        code for code in settings.preload if code in READ_LANGUAGES and code != brief_language
    ]
    for language in languages:
        try:
            paragraphs = await app.state.brief.translation(brief_id, language)
            await speech_file(app, settings, language, "\n\n".join(paragraphs), f"brief-{brief_id}")
        except (BriefError, ProviderError) as error:
            logger.warning(
                "Reading brief %s in %s in advance failed: %s", brief_id, language, error
            )
        except Exception:
            logger.exception("Reading brief %s in %s in advance failed", brief_id, language)


@router.get("/integrations/voice/sample")
async def sample(request: Request, db: Db, language: str, voice: str = "") -> Response:
    """A short sentence in a voice, to try it in the settings before saving."""
    settings = await load_voice(request, db)
    if language not in READ_LANGUAGES or not settings.azure:
        raise HTTPException(status_code=404)
    if voice:
        if not VOICE_NAME.match(voice) or not voice.startswith(f"{language}-"):
            raise HTTPException(status_code=400, detail="Unknown voice")
        settings.voices = {**settings.voices, language: voice}
    try:
        return await _speech(request, settings, language, SAMPLES[language], "sample")
    except ProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.get("/brief/{brief_id}/audio")
async def brief_audio(request: Request, db: Db, brief_id: int, language: str) -> Response:
    """The brief's summary read by Azure Speech, translated first when needed."""
    settings = await load_voice(request, db)
    if language not in READ_LANGUAGES or not settings.azure:
        raise HTTPException(status_code=404)
    try:
        paragraphs = await request.app.state.brief.translation(brief_id, language)
        return await _speech(
            request, settings, language, "\n\n".join(paragraphs), f"brief-{brief_id}"
        )
    except (BriefError, ProviderError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
