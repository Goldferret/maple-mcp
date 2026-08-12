"""A custom tool for integration testing."""


def hello_world(name: str = "World") -> dict:
    """Say hello — a test custom tool.

    Args:
        name: Who to greet
    """
    return {"greeting": f"Hello, {name}!"}
