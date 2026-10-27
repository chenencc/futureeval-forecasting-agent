"""Preserve explicit platform grids across shared capture and existing analysis."""
from copy import deepcopy
from ForecastAgent.competition.live import live_request as native_request,rule_identity as native_identity
from ForecastAgent.competition.queue import descriptor,digest,questions,utc

class PlatformStateError(RuntimeError):
    def __init__(self,code):self.code=code;super().__init__(code)

def live_request(post,question):
    request=native_request(post,question)
    grid=question.get('forecast_grid')
    if grid is not None and question['type'] in ('numeric','discrete','date'):
        request['scaling']=deepcopy(request['scaling']);request['scaling']['continuous_range']=deepcopy(grid)
        from ForecastAgent.analysis.distributions import range_metadata
        range_metadata(request)  # Validate exact count, monotonicity and boundary match.
    return request
def rule_identity(post,question):
    return digest(dict(native_identity=native_identity(post,question),forecast_grid=question.get('forecast_grid')))
def current(client,task,*,allow_closed_receipt=False):
    post=client.post(task['post_id']);matches=[q for q in questions(post) if str(q['id'])==task['id']]
    if len(matches)!=1:raise PlatformStateError('QUESTION_IDENTITY_CHANGED')
    q=matches[0]
    if not allow_closed_receipt:
        if q.get('status')!='open' or post.get('user_permission') not in ('forecaster','curator','admin','creator'):raise PlatformStateError('QUESTION_CLOSED_OR_PERMISSION')
        if rule_identity(post,q)!=task['rule_identity']:raise PlatformStateError('QUESTION_RULES_OR_SCALE_CHANGED')
        deadline=descriptor(post,q)['deadline_utc']
        if not deadline or utc(deadline)<=utc():raise PlatformStateError('QUESTION_DEADLINE')
        task['deadline_utc']=deadline
    return post,q
