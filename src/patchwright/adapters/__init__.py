"""Concrete RepoAdapter implementations.

RepoAdapter Protocol lives in core/repo.py; this package holds backends.
The lazy-import pattern in core.repo.default_repo_adapter keeps this
package out of the import path until an operator actually selects an
adapter — so missing optional system deps (`gh`, etc.) never break
import.
"""
