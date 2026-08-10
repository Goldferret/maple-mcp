"""LLM model provider factory for MAPLE agents."""

import os


def create_model(provider: str = None, model_id: str = None):
    """Create a Strands-compatible LLM model.
    
    Args:
        provider: "openai", "anthropic", or "ollama". Defaults to MODEL_PROVIDER env var.
        model_id: Model identifier. Defaults to EXECUTOR_AGENT_MODEL env var.
        
    Returns:
        A configured Strands model instance.
    """
    provider = provider or os.getenv("MODEL_PROVIDER", "openai")
    
    if provider == "ollama":
        from strands.models.ollama import OllamaModel
        return OllamaModel(
            host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            model_id=model_id or os.getenv("EXECUTOR_AGENT_MODEL", "qwen3:30b-a3b"),
            temperature=0.1,
            max_tokens=8000,
        )
    elif provider == "anthropic":
        from strands.models.anthropic import AnthropicModel
        return AnthropicModel(
            client_args={"api_key": os.getenv("ANTHROPIC_API_KEY")},
            model_id=model_id or os.getenv("EXECUTOR_AGENT_MODEL", "claude-sonnet-4-5-20250929"),
            max_tokens=4000,
            params={"temperature": 0.7},
        )
    else:
        from strands.models.openai import OpenAIModel
        return OpenAIModel(
            client_args={"api_key": os.getenv("OPENAI_API_KEY")},
            model_id=model_id or os.getenv("EXECUTOR_AGENT_MODEL", "gpt-4o"),
            params={"max_tokens": 4000, "temperature": 0.7},
        )
