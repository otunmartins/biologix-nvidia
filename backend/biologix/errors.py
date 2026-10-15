"""Exception types.

The distinctions matter for honesty: an adapter that is *unavailable* must
never be confused with one that ran and returned a neutral result.
"""

from __future__ import annotations


class BiologixError(Exception):
    """Base class for all errors raised by this package."""


class AdapterUnavailable(BiologixError):
    """A model adapter cannot run: no credentials, no GPU, no service.

    Callers must surface this rather than substituting a default value.
    """

    def __init__(self, adapter: str, reason: str, remedy: str | None = None) -> None:
        self.adapter = adapter
        self.reason = reason
        self.remedy = remedy
        msg = f"{adapter} unavailable: {reason}"
        if remedy:
            msg += f" — {remedy}"
        super().__init__(msg)


class AdapterCallFailed(BiologixError):
    """An adapter was available but the call failed."""

    def __init__(self, adapter: str, reason: str, status: int | None = None) -> None:
        self.adapter = adapter
        self.reason = reason
        self.status = status
        super().__init__(f"{adapter} call failed: {reason}")


class UpstreamServiceError(AdapterCallFailed):
    """The service accepted the request and then failed inside itself.

    Kept apart from a plain :class:`AdapterCallFailed` because the remedy is
    different: there is nothing wrong with the request, so re-reading the API
    contract or editing the payload cannot help. Hosted NIMs have been
    observed reporting these with a 4xx status — a GPU crash arriving as
    "unprocessable entity" — so the status code alone must not decide whose
    fault a failure was.
    """

    def __init__(
        self,
        adapter: str,
        reason: str,
        status: int | None = None,
        *,
        retryable: bool = False,
    ) -> None:
        self.retryable = retryable
        super().__init__(adapter, reason, status=status)


class ValidationFailure(BiologixError):
    """Input did not satisfy a structural or chemical requirement.

    ``remedy`` is optional but worth supplying wherever the caller could
    plausibly fix the input: a refusal that names a cause without an action
    leaves a user stuck, and the API and UI both surface this field.
    """

    def __init__(
        self, message: str, *, field: str | None = None, remedy: str | None = None
    ) -> None:
        self.field = field
        self.remedy = remedy
        super().__init__(message)


class PolymerizationError(ValidationFailure):
    """A repeat unit violated polymerisation rules."""


class SequenceTooLong(ValidationFailure):
    """A target chain exceeds a model's length limit.

    Raised instead of truncating, because a truncated target is a different
    molecule and any downstream number would be mislabelled.
    """

    def __init__(self, chain_id: str, length: int, limit: int, model: str) -> None:
        self.chain_id = chain_id
        self.length = length
        self.limit = limit
        self.model = model
        super().__init__(
            f"chain {chain_id!r} has {length} residues but {model} accepts at most "
            f"{limit}; refusing to truncate. Split the target explicitly or use a "
            f"model with a longer context."
        )


class ModelNotTrained(BiologixError):
    """A predictor was asked for a prediction before it had training data.

    Carries the shortfall so the UI can say what is missing instead of
    rendering an invented number.
    """

    def __init__(self, name: str, n_samples: int, n_required: int) -> None:
        self.name = name
        self.n_samples = n_samples
        self.n_required = n_required
        super().__init__(
            f"predictor {name!r} is not trained: {n_samples} usable training "
            f"examples, {n_required} required. No prediction available."
        )


class PlatformUnavailable(BiologixError):
    """An explicitly requested compute platform does not exist here."""

    def __init__(self, requested: str, available: list[str]) -> None:
        self.requested = requested
        self.available = available
        super().__init__(
            f"compute platform {requested!r} was explicitly requested but is not "
            f"available. Available platforms: {', '.join(available) or 'none'}. "
            f"Not silently falling back."
        )


class JobCancelled(BiologixError):
    """A background job was cancelled by request."""
