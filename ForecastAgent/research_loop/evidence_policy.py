"""Model-neutral evidence distinctions for development reading and graph stages."""

VERSION = 'event-versus-evidence-policy-v1'
GUIDE = '''Keep the event separate from what a document establishes.
For an event question, an unstated event status is unknown, not an observed NO.
For an evidence-support question, report supported, contradicted, or unstated;
do not silently reinterpret that question as an event forecast.
A source saying "not reported", "not published", or "status not supplied" can
be quoted to explain a gap. That quote is an observation about reporting,
not proof the underlying event did not occur. Preserve its reference if useful.
Rules define the target; they never supply observed dates, actors, or counts.
Keep source observations, rule definitions, and inference assumptions separate.'''

FORMAT_GUIDE = '''Return field values, not a copy of the schema. Array fields must be
JSON arrays and string items must be strings, not objects containing text or
maxLength. Keep optional notes short. Do not repeat full resolution rules in
commentary; the complete rules are already supplied to the final scorer.'''
