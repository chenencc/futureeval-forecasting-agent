"""One collection instruction contract without competing workflow checkboxes."""
import json


def collection_system(task, skills):
    from ForecastAgent.runtime.temporal_policy import unrestricted
    temporal = ('Use current available information without historical publication filters, body quarantine or date masking. '
        'The original request as_of_utc is provenance only, not the simulated present or a data cutoff. '
        'Keep event deadlines and resolution criteria as subject metadata; collect current status even after those dates. '
        'Do not claim a cutoff-safe backtest. Archives are optional provenance, not required reading permissions.'
        if unrestricted(task.bundle) else
        'In historical tests the as_of_utc cutoff is the simulated present. Collect observations before that cutoff, relevant prior history, and then-current forward-looking drivers. Never require realized prices/results after the cutoff, or treat the future resolution window as the observation range. A forecast horizon is not an available-data window.')
    body_policy = ('Current and archived readable bodies are permitted. Publication dates do not limit acquisition. '
        'Preserve capture dates, original bytes, units and revision caveats. Unreadable bodies remain failures.'
        if unrestricted(task.bundle) else
        'Blocked/audit-only historical bodies have no readable content. Publication filters do not establish historical versions. Archives require two shared HTTP attempts; current datasets remain exploratory vintages with unit/revision caveats. Dataset end_date must precede the cutoff UTC day. Model memory and question revisions remain leakage risks. Never claim a clean backtest.')
    from pathlib import Path
    filename = 'raw_recall.md' if getattr(task,'raw_recall',False) else 'collection.md'
    template = (Path(__file__).parents[1] / 'prompts' / filename).read_text(encoding='utf-8')
    from ForecastAgent.runtime.intelligent_acquisition import enabled
    if enabled(task):
        template = (Path(__file__).parents[1] / 'prompts' / 'intelligent_materials.md').read_text(encoding='utf-8')
        from ForecastAgent.runtime.intelligent_acquisition import repaired
        if repaired(task):
            template = template.replace('Bind each need to question_spans containing\nan exact quote and its field: question, resolution_criteria, fine_print, or background.',
                'Bind each need to question_refs using original_question_handles IDs. The program\ncopies immutable original fields; never retype rule quotes or guess the field.\nWhole-field binding is structural only: describe the exact target and exceptions in condition.')
            template += '\nDispose of the pinned pending_material_review spans with review_passages before more navigation. Keep useful context or reject with a reason; no automatic acceptance. Planning has one correction opportunity. Forced closure exports gaps without another model request.'
    return template.replace('{temporal}',temporal).replace('{body_policy}',body_policy) + '\nFrozen task budget: '+json.dumps({'tavily_basic_lifetime':task.search_limit,
        'exa_lifetime':task.exa_limit, 'exa_policy':task.bundle.get('search_policy', {}).get('exa', 'optional'), 'initial_shared_http':8, 'extract_batches':1,
        'model_decisions_per_dispatch':12, 'transport_failures_per_dispatch':4, 'model_http_per_dispatch':16, 'model_http_lifetime':72})+'\nAvailable skill catalog: '+json.dumps(skills)
