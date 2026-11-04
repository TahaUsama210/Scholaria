"""Map internal failures to messages that are safe to show to end users.

Raw exception strings can leak hostnames, SQL or provider error bodies; the
client gets one of these messages plus the request id to quote in a report.
"""

import openai
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

GENERIC_MESSAGE = "Something went wrong while generating the answer. Please try again."


def user_safe_message(exc: BaseException) -> str:
    if isinstance(exc, openai.RateLimitError):
        return "The language model is rate-limited right now. Please try again in a moment."
    if isinstance(exc, openai.APITimeoutError):
        return "The language model took too long to respond. Please try again."
    if isinstance(exc, openai.APIConnectionError):
        return "The language model is unreachable right now. Please try again later."
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return "The assistant's language model is misconfigured. Please contact the maintainer."
    if isinstance(exc, openai.APIStatusError):
        return "The language model returned an error. Please try again."
    if isinstance(exc, DBAPIError | PoolTimeoutError | OSError | TimeoutError):
        return "The knowledge base is temporarily unavailable. Please try again shortly."
    return GENERIC_MESSAGE
