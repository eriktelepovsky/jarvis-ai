import httpx2
import pytest
from conftest import MakeClient, complete_setup
from fastapi.testclient import TestClient
from test_integrations import FakeApis

HOST = "westeurope.tts.speech.microsoft.com"
KEY = "azure-key"
VOICES = [
    {
        "ShortName": "sk-SK-ViktoriaNeural",
        "LocalName": "Viktória",
        "Gender": "Female",
        "Locale": "sk-SK",
        "VoiceType": "Neural",
    },
    {
        "ShortName": "sk-SK-LukasNeural",
        "LocalName": "Lukáš",
        "Gender": "Male",
        "Locale": "sk-SK",
        "VoiceType": "Neural",
    },
    {
        "ShortName": "en-GB-SoniaNeural",
        "LocalName": "Sonia",
        "Gender": "Female",
        "Locale": "en-GB",
        "VoiceType": "Neural",
    },
    {
        "ShortName": "ja-JP-NanamiNeural",
        "LocalName": "Nanami",
        "Gender": "Female",
        "Locale": "ja-JP",
        "VoiceType": "Neural",
    },
]


class FakeAzure:
    def __init__(self, apis: FakeApis) -> None:
        self.spoken: list[str] = []
        apis.routes[(HOST, "/cognitiveservices/voices/list")] = self.voices
        apis.routes[(HOST, "/cognitiveservices/v1")] = self.speak

    def voices(self, request: httpx2.Request) -> httpx2.Response:
        if request.headers.get("ocp-apim-subscription-key") != KEY:
            return httpx2.Response(401)
        return httpx2.Response(200, json=VOICES)

    def speak(self, request: httpx2.Request) -> httpx2.Response:
        assert request.headers["x-microsoft-outputformat"].endswith("mp3")
        self.spoken.append(request.content.decode())
        return httpx2.Response(200, content=b"ID3 mp3 bytes")


@pytest.fixture
def apis() -> FakeApis:
    return FakeApis()


@pytest.fixture
def client(make_client: MakeClient, apis: FakeApis) -> TestClient:
    client = make_client(transport=httpx2.MockTransport(apis))
    complete_setup(client)
    return client


def test_browser_voices_by_default(client: TestClient):
    page = client.get("/integrations/voice").text
    assert 'value="browser" checked' in page
    assert 'data-browser-voice="sk" data-tag="sk-SK"' in page
    assert 'href="/integrations/voice"' in client.get("/integrations").text


def test_azure_key_and_region_are_checked(client: TestClient, apis: FakeApis):
    FakeAzure(apis)
    form = {"provider": "azure", "azure_key": "wrong", "azure_region": "westeurope"}
    assert "rejected the credentials" in client.post("/integrations/voice", data=form).text
    form["azure_region"] = "West Europe"
    assert "short name like westeurope" in client.post("/integrations/voice", data=form).text


def test_azure_voices_and_samples(client: TestClient, apis: FakeApis):
    azure = FakeAzure(apis)
    form = {"provider": "azure", "azure_key": KEY, "azure_region": "westeurope"}
    page = client.post("/integrations/voice", data=form).text
    assert "The voice settings were saved." in page
    assert (
        '<option value="sk-SK-ViktoriaNeural" selected>Viktória · Female · sk-SK</option>' in page
    )
    assert "Nanami" not in page  # only languages Jarvis reads
    assert "✓ Azure Speech" in client.get("/integrations").text

    # Choosing another voice keeps the key.
    client.post("/integrations/voice", data={"provider": "azure", "voice_sk": "sk-SK-LukasNeural"})
    sample = client.get("/integrations/voice/sample", params={"language": "sk"})
    assert sample.headers["content-type"] == "audio/mpeg"
    assert "<voice name='sk-SK-LukasNeural'>" in azure.spoken[-1]
    assert "Dobré ráno." in azure.spoken[-1]
    client.get("/integrations/voice/sample", params={"language": "sk"})
    assert len(azure.spoken) == 1  # kept, not made again

    bad = client.get(
        "/integrations/voice/sample", params={"language": "sk", "voice": "en-GB-SoniaNeural"}
    )
    assert bad.status_code == 400


def test_brief_audio_needs_azure(client: TestClient):
    assert client.get("/brief/1/audio", params={"language": "en"}).status_code == 404
