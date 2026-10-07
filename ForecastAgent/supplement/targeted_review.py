"""Conservative local body screening over immutable targeted captures.

Only obvious short login/redirect shells are isolated. Substantive short numeric
records and announcements are retained. This is not source relevance analysis.
"""
import argparse
import copy
import json
import re
from pathlib import Path
from ForecastAgent.supplement.stage import save,now
from ForecastAgent.supplement.targeted import sha


def shell_reason(text):
    compact=' '.join(text.split())
    if len(compact)<=1000 and re.search(r'you will be redirected.*would you like to continue',compact,re.I):
        return 'redirect_consent_only'
    if len(compact)<=250 and re.search(r'log\s*in\s*sign\s*up',compact,re.I):
        return 'title_and_login_navigation_only'
    return None


def review(root):
    root=Path(root);rows=[]
    for path in sorted((root/'tasks').glob('*/supplement-package.json')):
        package=json.loads(path.read_text(encoding='utf-8'));checked=copy.deepcopy(package)
        rejected=[]
        for url,page in list(checked['pages'].items()):
            reason=shell_reason(page.get('content',''))
            if not reason:continue
            rejected.append({'url':url,'reason':reason,'captured_at_utc':page.get('retrieved_at_utc'),
                             'scope':'Raw response remains preserved; no event conclusion.'})
            del checked['pages'][url]
        checked['mechanical_body_screen']={'original_package_sha256':sha(path),
            'raw_captures_unchanged':True,'isolated_shells':rejected,'relevance_verified':False}
        save(path.parent/'checked-supplement-package.json',checked)
        rows.append({'id':package['question_id'],'raw_readable_count':len(package['pages']),
                     'material_count_after_shell_screen':len(checked['pages']),
                     'isolated_shells':rejected,'checked_package':str(path.parent/'checked-supplement-package.json')})
    report={'schema':'targeted-supplement-body-review-v1','reviewed_at_utc':now(),'rows':rows,
            'no_network_calls':True,'no_models':True,'source_relevance_verified':False,
            'scope':'Exclude obvious shells only; full target dates, indicators and official rules are deferred.'}
    save(root/'body-screen-review.json',report)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    args=p.parse_args();print(json.dumps(review(args.root),ensure_ascii=True))
