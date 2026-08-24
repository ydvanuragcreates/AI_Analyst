"""
Request-scoped context, shared between HTTP middleware, the logging
filter, and background worker code.

`contextvars` (not a mutable global) so each concurrent request/task gets
its own isolated values -- this is what lets every log line emitted
while handling request X automatically carry request X's request_id,
without every function down the call stack having to accept and thread
through a `request_id` parameter explicitly.
"""

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)


def get_request_id() -> str | None:
    return request_id_var.get()


def get_user_id() -> str | None:
    return user_id_var.get()
