"""Resolve human-authored agent execution settings before admitting any work."""

from hames.agent import AgentExecution
from hames.config import HamesConfig
from hames.providers import Provider


async def resolve_agent_execution(
    execution: AgentExecution,
    providers: dict[str, Provider],
    config: HamesConfig,
) -> tuple[str, str, str, int, str]:
    provider = providers.get(execution.provider)
    if provider is None:
        raise ValueError(f"unknown agent provider: {execution.provider}")
    models = await provider.list_models()
    model = next((item for item in models if item.id == execution.model), None)
    if model is None:
        raise ValueError(f"unknown agent model: {execution.provider}/{execution.model}")
    profile = config.providers.get(execution.provider)
    efforts = model.reasoning_efforts
    if not efforts and profile and profile.model == model.id:
        efforts = profile.supported_reasoning_efforts
    effort = execution.reasoning_effort
    if effort and effort != "off":
        if model.reasoning_supported is False or effort not in efforts:
            raise ValueError(
                f"unsupported agent effort {effort!r} for {execution.provider}/{model.id}; "
                f"supported: {', '.join(efforts) or 'none advertised'}"
            )
    if profile and profile.context_window_tokens is not None:
        window, source = profile.context_window_tokens, "profile"
    elif model.context_length is not None:
        window, source = model.context_length, "provider"
    else:
        window, source = config.context.fallback_window_tokens, "fallback"
    return execution.provider, model.id, effort, window, source
