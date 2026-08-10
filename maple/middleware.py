"""MAPLE MCP middleware — extracts identity from Authorization header."""

from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.exceptions import ToolError

from maple.auth import is_valid_token


class AuthMiddleware(Middleware):
    """Extracts bearer token from Authorization header and stashes in context.
    
    Every tool call passes through this middleware. The token is made
    available to tools via `await ctx.get_state("maple_identity")`.
    
    Rejects requests with missing or malformed tokens.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        headers = get_http_headers()
        authorization = headers.get("authorization", "")

        scheme, _, token = authorization.partition(" ")
        token = token.strip()

        if scheme.lower() != "bearer" or not token:
            raise ToolError(
                "Authorization required. Include 'Authorization: Bearer <token>' header. "
                "Run 'maple chat' to auto-generate credentials."
            )

        if not is_valid_token(token):
            raise ToolError(
                "Invalid token format. Expected a UUIDv4 string. "
                "Delete ~/.maple/credentials and retry to regenerate."
            )

        if context.fastmcp_context is None:
            raise ToolError("Request context unavailable")

        await context.fastmcp_context.set_state("maple_identity", token)
        return await call_next(context)
