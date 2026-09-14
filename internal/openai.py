"""Compatibility exports for the original ``internal/openai`` package name."""

from .openai_client import FunctionCall, Message, OpenAIClient, Tool, ToolCall, ToolFunction

Client = OpenAIClient
New = OpenAIClient

__all__ = ["Client", "FunctionCall", "Message", "OpenAIClient", "Tool", "ToolCall", "ToolFunction", "New"]
