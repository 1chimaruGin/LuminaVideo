"""Intelligence plane: planner, language packs, safety, cost estimator.

The estimator is separate from the router on purpose: it answers "what will this cost"
without committing to a provider, so the UI can price a plan before running it.
"""
