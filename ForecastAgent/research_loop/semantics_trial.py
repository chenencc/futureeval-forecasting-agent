"""Replay existing briefs: one optional review and one final score per frozen case."""
import argparse
from pathlib import Path

from ForecastAgent.analysis.pilot import digest, load, save, WARNING
from ForecastAgent.research_loop import brief_semantics as semantics, forecast_brief_refs as refs
from ForecastAgent.research_loop import decision_http, live_trial, decision
from ForecastAgent.runtime.task_lock import task_lock


def receipts(root):
    return sorted(Path(root).rglob('http/*.json'))


def controls(parent):
    """Freeze all existing control bytes, including provider failures and usage."""
    return {str(p.relative_to(parent)):live_trial.sha(p) for p in sorted(parent.rglob('*.json'))}


def run(parent,root,*,execute=False,max_http=12):
    parent=Path(parent);root=Path(root)
    cases=sorted(parent.glob('live/cases/*'))
    if not 1<=len(cases)<=5 or not 0<=max_http<=15:raise ValueError('Use 1-5 frozen cases and <=15 total HTTP attempts')
    # A preparation file created before HTTP must bind the entire parent tree.
    parent_identity=controls(parent)
    identity={'schema':semantics.PROTOCOL,'parent':str(parent.resolve()),'control_files':parent_identity,
              'implementation':decision.implementation_hashes(),'policy':semantics.POLICY,
              'max_http':max_http,'probes_counted_in_same_budget':True,
              'same_scoring_registry':True,'saved_super_outputs_reused':True,
              'new_super_calls':0,'searches':0,'fetches':0,'submitted':False}
    root.mkdir(parents=True,exist_ok=True)
    with task_lock(root):
        if (root/'identity.json').exists() and load(root/'identity.json')!=identity:raise ValueError('Frozen audit input, implementation or budget changed')
        save(root/'identity.json',identity)
        rows=[]
        for position,case in enumerate(cases):
            ident=case.name;folder=root/'cases'/ident
            common=load(case/'common-state.json');heads=load(case/'questions.json');spec=load(case/'spec.json')
            question=common['question'];old_brief=load(case/'brief/accepted.json') if (case/'brief/accepted.json').exists() else None
            row={'id':ident,'question':question['question'],'type':question['question_type'],
                 'status':'prepared','common_state_sha256':digest(common),'arms':{}}
            # Audit all saved provider-to-payload conversions without a model call.
            for arm in ('direct','brief'):
                old_result=load(case/arm/'result.json');old_response=load(case/arm/'decision/response.json')
                conversion=semantics.conversion_audit(old_response,spec,old_result)
                save(folder/'control'/arm/'conversion-audit.json',conversion)
                row['arms'][arm]={'status':'archived_control','summary':old_result['summary'],
                                  'response_sha256':live_trial.sha(case/arm/'decision/response.json')}
            save(folder/'common-state.json',common);save(folder/'questions.json',heads);save(folder/'spec.json',spec)
            candidate=None
            if old_brief:
                state,review_heads=semantics.review_request(common,old_brief)
                save(folder/'review/state.json',state);save(folder/'review/questions.json',review_heads)
            if execute:
                if old_brief:
                    try:
                        cached=(folder/'review/decision/response.json').exists()
                        outstanding_scores=sum(not (root/'cases'/c.name/'final/decision/response.json').exists() for c in cases[position:])
                        if not cached and len(receipts(root))+1+outstanding_scores>max_http:
                            raise RuntimeError('Reserve remaining requests for valid original-evidence scores')
                        reviewed=decision_http.call(state,folder/'review/decision',review_heads)
                        candidate,audit=semantics.apply_review(common,old_brief,reviewed)
                        save(folder/'review/interpretation-audit.json',audit)
                        row['review_status']='completed'
                        row['retained_fact_ids']=audit['retained_fact_ids']
                        row['quarantined_fact_ids']=[f['id'] for f in audit['quarantined_interpretations']]
                    except Exception as exc:
                        row.update(review_status='unavailable',review_error=type(exc).__name__+': '+str(exc))
                else:row['review_status']='no_valid_saved_brief'
                try:
                    if not (folder/'final/decision/response.json').exists() and len(receipts(root))>=max_http:raise RuntimeError('Preserved HTTP cap exhausted')
                    result=refs.score(question,common,heads,spec,folder/'final',candidate)
                    final_response=load(folder/'final/decision/response.json')
                    save(folder/'final/conversion-audit.json',semantics.conversion_audit(final_response,spec,result))
                    if result['original_state_sha256']!=digest(common):raise ValueError('Score changed common original text')
                    row['arms']['reviewed']={'status':'completed','summary':result['summary'],
                        'brief_delivered':candidate is not None,'same_originals':True,
                        'same_scoring_questions':load(folder/'final/decision/request.json')['questions']==heads}
                    row['status']='completed'
                except Exception as exc:
                    row.update(status='failed',error=type(exc).__name__+': '+str(exc))
            save(folder/'result.json',row);rows.append(row)
            if controls(parent)!=parent_identity:raise ValueError('Control files changed during trial')
            usage=live_trial.usage(receipts(root))
            report={'schema':semantics.PROTOCOL,'rows':rows,'cases':len(cases),
                    'completed':sum(r['status']=='completed' for r in rows),
                    'controls_preserved':True,'same_originals':True,'same_scoring_questions':True,
                    'comparison':'Exploratory repeat against archived controls. Separate model calls can vary; no measured accuracy without future resolutions.',
                    'usage':usage,'actual_http':len(receipts(root)),'max_http':max_http,
                    'new_super_calls':0,'searches':0,'fetches':0,'submitted':False,
                    'accuracy':None,'brier':None,'evaluation_warning':WARNING}
            save(root/'report.json',report)
            print({'id':ident,'status':row['status'],'review':row.get('review_status'),'http':len(receipts(root))},flush=True)
        return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent',type=Path,required=True);parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--max-http',type=int,default=12);parser.add_argument('--execute',action='store_true')
    args=parser.parse_args();run(args.parent,args.root,execute=args.execute,max_http=args.max_http)


if __name__=='__main__':main()
