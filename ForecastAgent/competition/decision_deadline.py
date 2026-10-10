"""Keep a validated first decision when an optional reread risks late delivery."""
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from ForecastAgent.competition.queue import save

@contextmanager
def guard(chain,request):
    deadlines=[datetime.fromisoformat(request[k].replace('Z','+00:00'))
        for k in ('actual_close_time','close_time','scheduled_close_time','spot_scoring_time') if request.get(k)]
    original=chain.call
    def call(state,folder,registry):
        if Path(folder).name=='second' and deadlines:
            remaining=(min(deadlines)-datetime.now(timezone.utc)).total_seconds()
            if remaining<=150:
                save(Path(folder).parent/'deadline-second-read-skipped.json',{
                    'remaining_seconds':remaining,'valid_first_decision_retained':True,
                    'new_provider_requests':0,'budget_reset':False})
                raise RuntimeError('Optional reread omitted to retain time for validated delivery')
        return original(state,folder,registry)
    chain.call=call
    try:yield
    finally:chain.call=original
