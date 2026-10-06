"""Expose grouped and conditional subquestions to the frozen worker interface."""
import copy
from pathlib import Path
from ForecastAgent.competition.queue import load, save, questions
from ForecastAgent.competition.tournaments import configured_tournament


def normalize(post):
    post=copy.deepcopy(post);group=post.get('group_of_questions') or {}
    for question in group.get('questions',[]):
        for field in ('resolution_criteria','fine_print','description'):
            if not question.get(field) and group.get(field):question[field]=group[field]
    conditional=post.get('conditional')
    if conditional:
        premise=conditional.get('condition') or {}; target=conditional.get('condition_child') or {}
        title=premise.get('title');rules=premise.get('resolution_criteria')
        if not title or not rules:raise ValueError('Conditional premise title and exact rules are required')
        children=[]
        for key,outcome in (('question_yes','Yes'),('question_no','No')):
            question=copy.deepcopy(conditional.get(key))
            if not question:raise ValueError('Conditional branch missing')
            target_rules=question.get('resolution_criteria') or target.get('resolution_criteria') or post.get('resolution_criteria')
            if not target_rules:raise ValueError('Conditional target rules missing')
            question['resolution_criteria']=(f'This is a conditional forecast, GIVEN that "{title}" resolves {outcome}. '
                 'Do not forecast the unconditional event or the probability of the premise.\n'
                 f'Premise resolution rules:\n{rules}\nTarget resolution rules:\n{target_rules}')
            question['title']=f'Given "{title}" resolves {outcome}: '+(question.get('title') or target.get('title') or post['title'])
            children.append(question)
        if post.get('question') or group.get('questions'):
            raise ValueError('Ambiguous combined conditional and group surface')
        post['group_of_questions']={'questions':children}
    ids=[str(q['id']) for q in questions(post)]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate subquestion ID in one post')
    return post


class Client:
    """Preserve actual platform transport; normalize only read-only documents."""
    def __init__(self,base):self.base=base
    def account(self):return self.base.account()
    def post(self,ident):return normalize(self.base.post(ident))
    def comments(self,ident):return self.base.comments(ident)
    def request(self,*args,**kwargs):return self.base.request(*args,**kwargs)


def snapshots(source,destination):
    source=Path(source);destination=Path(destination)
    if (source/'surface-normalization.json').exists():return source
    indexes=[p for p in source.rglob('index.json') if load(p).get('tournament')==configured_tournament()]
    if not indexes:raise ValueError('Tournament snapshot missing')
    index_path=max(indexes,key=lambda p:load(p)['retrieved_at_utc']);index=copy.deepcopy(load(index_path))
    rows={str(r['question_id']):r for r in index.get('questions',[])};converted=[];errors={}
    for path in sorted(index_path.parent.glob('*.json')):
        if not path.stem.isdecimal():continue
        document=load(path)
        if not document.get('post'):continue
        try:post=normalize(document['post'])
        except ValueError as exc:
            post=copy.deepcopy(document['post'])
            conditional=post.get('conditional') or {}
            children=[v for k,v in conditional.items()
                      if k in {'question_yes','question_no'} and isinstance(v,dict)]
            if children:post['group_of_questions']={'questions':children}
            for question in questions(post):errors[str(question['id'])]=str(exc)
        if str(post['id'])!=path.stem:raise ValueError('Snapshot filename and post disagree')
        document['post']=post;save(destination/path.name,document);converted.append(post['id'])
        for question in questions(post):
            ident=str(question['id']);row=rows.get(ident)
            if row and row['post_id']!=post['id']:raise ValueError('Subquestion belongs to multiple posts')
            rows[ident]={'question_id':question['id'],'post_id':post['id'],'type':question.get('type'),
                         'open':question.get('status')=='open'}
    index['questions']=list(rows.values());index['open_question_count']=sum(bool(r.get('open')) for r in rows.values())
    save(destination/'index.json',index)
    save(destination/'surface-errors.json',errors)
    save(destination/'surface-normalization.json',{'version':'1.0.2','source_index':str(index_path),
          'converted_post_ids':converted,'subquestion_count':len(rows),'original_snapshots_unchanged':True})
    return destination
