"""Failures that belong to a whole chat turn rather than to one of its steps."""


class TurnPipelineError(Exception):
    """Tags which pipeline step failed during a chat turn.

    Raised, not logged, at the point of failure - logging happens once, centrally,
    by the caller.

    Lives here, in the layer every step already depends on, rather than in the module
    of any one step. It is raised from four places that have nothing else in common -
    embedding and retrieval, the FAQ specialist's generation call, the merge's composing
    call and the booking loop's, and the turn's own persistence write - and the step it
    names is what decides whether `critical.dependency_unreachable` fires. Keeping it in
    `rag.retriever` made the RAG module a dependency of the merge step and the booking
    loop, neither of which retrieves anything, and it was load-bearing coupling rather
    than untidiness: importing `compose_answer` reached `qdrant_repository` through this
    class alone, and that module reads `get_settings()` at import time, so a test that
    imported the composer at module level froze the cached settings on the dev Qdrant
    collection before its own override could run.
    """

    def __init__(self, pipeline_step: str, cause: Exception) -> None:
        """Tag `cause` with the pipeline step that raised it."""
        super().__init__(str(cause))
        self.pipeline_step = pipeline_step
        self.cause = cause
