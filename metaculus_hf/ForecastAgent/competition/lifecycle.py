"""Separate forecast availability from eventual event resolution."""
from ForecastAgent.competition.queue import utc


def describe(post, question, now=None):
    """Use authoritative question status; a hidden/null outcome is not a status."""
    status = question.get('status', 'unknown')
    phase = {'upcoming': 'upcoming', 'open': 'open',
        'closed': 'closed_waiting_resolution', 'resolved': 'resolved'}.get(status, 'unknown')
    approved = post.get('curation_status', 'approved') == 'approved'
    if not approved:
        phase = 'unpublished_or_unapproved'
    times = [utc(question[key]) for key in ('scheduled_close_time', 'actual_close_time') if question.get(key)]
    close = min(times).isoformat() if times else None
    expected = question.get('scheduled_resolve_time')
    return {'lifecycle': phase, 'question_status': status,
        'open_time': question.get('open_time'), 'submission_deadline_utc': close,
        'spot_scoring_time': question.get('spot_scoring_time'),
        'scheduled_resolve_time': expected, 'actual_resolve_time': question.get('actual_resolve_time'),
        'resolution_overdue': phase == 'closed_waiting_resolution' and bool(expected and utc(expected) <= utc(now)),
        'open': phase == 'open', 'requires_worker_permission_check': phase == 'open'}
