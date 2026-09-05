"""Pure domain layer.

Nothing in this package may import SQLAlchemy, httpx, boto3, or any other I/O library.
Everything here is deterministic and unit-testable without a database. The rest of the
system depends on this package; this package depends on nothing but the stdlib and pydantic.
"""
