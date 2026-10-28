"""Explicit opt-in for input experiments; changing formatting changes probabilities."""
import os
STABLE='stable-v1'
EXPERIMENTAL='role-aware-v2'
CANONICAL='canonical-v3'
def name():
    value=os.environ.get('METACULUS_DECISION_INPUT_PROFILE',STABLE)
    if value not in {STABLE,EXPERIMENTAL,CANONICAL}:raise ValueError('UNKNOWN_DECISION_INPUT_PROFILE')
    return value
def experimental():return name()==EXPERIMENTAL
def canonical():return name()==CANONICAL
