"""Persisted workflow intent; missing fields retain historical forecast behavior."""
COLLECTION_ONLY = 'collection-only-v1'


def collection_only(run):
    return run['execution_profile']['parameters'].get('workflow_policy') == COLLECTION_ONLY
