"""Provider metadata catalog independent from execution adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .contracts import AuthType, ProviderState, ProviderType


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    id: str
    display_name: str
    provider_type: ProviderType
    auth_type: AuthType
    supported_project_types: tuple[str, ...] = ("platformio", "arduino", "generic")
    supported_modes: tuple[str, ...] = ("files", "patch")
    state: ProviderState = ProviderState.NOT_CONFIGURED
    enabled: bool = True
    model: str | None = None

    def to_safe_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["provider_type"] = self.provider_type.value
        value["auth_type"] = self.auth_type.value
        value["state"] = self.state.value
        return value


class ProviderCatalog:
    def __init__(self, descriptors: tuple[ProviderDescriptor, ...] | None = None) -> None:
        self._items = {item.id: item for item in descriptors or default_descriptors()}

    def get(self, provider_id: str) -> ProviderDescriptor:
        try:
            return self._items[provider_id]
        except KeyError as exc:
            raise ValueError("PROVIDER_NOT_FOUND") from exc

    def list(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(self._items.values())


def default_descriptors() -> tuple[ProviderDescriptor, ...]:
    return (
        ProviderDescriptor("verified_template", "Verified template", ProviderType.TEMPLATE, AuthType.NONE, supported_modes=("files",), state=ProviderState.READY),
        ProviderDescriptor("openai", "OpenAI API", ProviderType.API, AuthType.API_KEY),
        ProviderDescriptor("openrouter", "OpenRouter API", ProviderType.API, AuthType.API_KEY),
        ProviderDescriptor("groq", "Groq API", ProviderType.API, AuthType.API_KEY),
        ProviderDescriptor("anthropic", "Anthropic API", ProviderType.API, AuthType.API_KEY, enabled=False, state=ProviderState.DISABLED),
        ProviderDescriptor("cerebras", "Cerebras API", ProviderType.API, AuthType.API_KEY, enabled=False, state=ProviderState.DISABLED),
        ProviderDescriptor("codex", "Codex CLI", ProviderType.AGENT, AuthType.CLI_SESSION),
    )
