class DataError(Exception):
    """Base class for data-layer failures. Callers must fail closed on these."""


class DataValidationError(DataError):
    """Input data failed validation; it must not be used."""

    def __init__(self, issues):
        self.issues = list(issues)
        lines = "\n".join(f"  - [{i.severity}] {i.check}: {i.detail}" for i in self.issues)
        super().__init__(f"Data validation failed:\n{lines}")


class SnapshotIntegrityError(DataError):
    """A stored snapshot does not match its content hash."""
