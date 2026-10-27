"""Typed Mammoth Decisions transport; never parse generated prose as a decision."""
import json,math,re
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from .analysis_policy import DECISION_MODEL,classify

MODEL=DECISION_MODEL
ENDPOINT='https://openrouter.ai/api/alpha/decisions'
VALIDATOR='mammoth-typed-probabilities/1'

class InvalidDecision(ValueError):
    code='DECISION_RESPONSE_INVALID'

def probability(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:raise InvalidDecision('INVALID_PROBABILITY')
    return value

def validate(response,questions):
    if not isinstance(response,dict) or not re.fullmatch(r'baseten/mammoth-1(?:-\d{8})?(?::free)?',str(response.get('model',''))):raise InvalidDecision('DECISION_MODEL_IDENTITY')
    answers=response.get('answers')
    if not isinstance(answers,dict) or set(answers)!=set(questions):raise InvalidDecision('DECISION_ANSWER_KEYS')
    for key,q in questions.items():
        a=answers[key]
        if not isinstance(a,dict) or a.get('type')!=q['type']:raise InvalidDecision('DECISION_ANSWER_TYPE')
        if q['type']=='noul':probability(a.get('noul'));continue
        if q['type'] not in ('choice','score'):raise InvalidDecision('DECISION_UNSUPPORTED_TYPE')
        expected=set(str(i) for i in range(len(q['criteria']))) if q['type']=='score' else set(q['criteria'])
        values=a.get('probabilities')
        if not isinstance(values,dict) or set(values)!=expected:raise InvalidDecision('DECISION_OPTION_IDENTITY')
        total=sum(probability(x) for x in values.values())
        if abs(total-1)>.02:raise InvalidDecision('DECISION_DISTRIBUTION_SUM')
        probability(a.get('confidence'))
        if q['type']=='choice':
            if a.get('choice') not in expected:raise InvalidDecision('DECISION_CHOICE')
        else:
            score=a.get('score');expected_score=sum(int(k)*v for k,v in values.items())
            if type(score) not in (int,float) or not math.isfinite(score) or abs(score-expected_score)>.03:raise InvalidDecision('DECISION_ORDINAL_EXPECTATION')
    return response

def decide(state,questions,api_key,observer):
    if not isinstance(questions,dict) or not 1<=len(questions)<=200:raise ValueError('DECISION_QUESTION_COUNT')
    payload=dict(model=MODEL,state=state,questions=questions)
    record=dict(endpoint=ENDPOINT,request=payload,status='reserved',validator=VALIDATOR)
    token=observer('reserve',record)
    req=Request(ENDPOINT,data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+api_key,'Content-Type':'application/json','X-Title':'Metaculus typed decision analysis'},method='POST')
    try:
        with urlopen(req,timeout=120) as response:record.update(http_status=response.status,response_body=response.read().decode())
        try:record['response']=validate(json.loads(record['response_body']),questions)
        except (ValueError,TypeError,KeyError) as exc:raise InvalidDecision('DECISION_RESPONSE_INVALID') from exc
        record['status']='received';return record['response']
    except HTTPError as exc:
        body=exc.read(16384).decode(errors='replace');fault=classify(exc.code,body,exc.headers)
        record.update(status='http_error',http_status=exc.code,response_body=body,error_code=fault.code);raise fault from None
    except Exception as exc:
        record.update(status='invalid_or_transport_error',error_code=getattr(exc,'code',type(exc).__name__));raise
    finally:observer('complete',record,token)
