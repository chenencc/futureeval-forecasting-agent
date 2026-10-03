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
    from ForecastAgent.runtime.capacity import limits, DEFAULT
    capacity = limits(task)
    if capacity != DEFAULT:
        template = template.replace('Up to THREE actual Tavily attempts and ONE actual Exa attempt',
            f"Up to {task.search_limit} actual Tavily attempts and {task.exa_limit} actual Exa attempts")
        template = template.replace('Within EIGHT shared source HTTP attempts',
            f"Within {capacity['initial_http']} shared source HTTP attempts")
        template = template.replace('at most ONE Tavily BASIC Extract rescue batch',
            f"at most {capacity['extract_batches']} Tavily BASIC Extract rescue batches")
        template = template.replace('the single required Exa attempt', 'at least one required complementary Exa attempt')
    material_policy = ('\nA readable directory, download help page or publication index is context, not the requested data. '
        'Inspect material_requirements and follow observed target-date file links or recorded archive links. '
        'Retain issuer/period-matched primary releases and observed supporting attachments. '
        'Do not fabricate file URLs, treat a date in the URL as proof of body coverage, or repeat an already readable directory. '
        'If the target file cannot be acquired, finish with an explicit target_data_file_missing gap; never imply the observation was collected.\n')
    return template.replace('{temporal}',temporal).replace('{body_policy}',body_policy) + material_policy + '\nFrozen task budget: '+json.dumps({**capacity,
        'tavily_basic_lifetime':task.search_limit, 'exa_lifetime':task.exa_limit,
        'exa_policy':task.bundle.get('search_policy', {}).get('exa', 'optional')})+'\nAvailable skill catalog: '+json.dumps(skills)
