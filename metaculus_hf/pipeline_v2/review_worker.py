"""Independent bounded review worker. Never searches, fetches evidence or scores."""
import argparse,json,time
from pathlib import Path
from .repository import Repository,file_lock,atomic_bytes
from .contracts import canonical,now
from .review_lifecycle import requirements,outcome
from .review_recovery import recover_review


def tick(root,*,reviewer=None):
    repo=Repository(root);path=repo.root/'control/two-question-pilot.json'
    if not path.exists():return {}
    results={}
    with file_lock(repo.root/'.locks/final-review-worker.lock',timeout=0):
        for row in json.loads(path.read_text())['admitted']:
            qid=row['question_id'];record=repo.get_runtime(qid)
            for r in requirements(record):
                sid=r['snapshot_id'];prior=outcome(repo,qid,sid)
                if prior['terminal']:results[sid]=prior;continue
                try:recover_review(repo,qid,sid,reviewer=reviewer)
                except Exception as exc:
                    reconciled=outcome(repo,qid,sid)
                    if reconciled['terminal']:results[sid]=reconciled;continue
                    # Persist operational failures; never silently treat them as reviewed.
                    results[sid]=dict(status='blocked',terminal=False,error_code=getattr(exc,'code',type(exc).__name__));continue
                results[sid]=outcome(repo,qid,sid)
        atomic_bytes(repo.root/'control/final-review-worker.json',canonical(dict(observed_at=now(),results=results)).encode())
    return results


def main():
    p=argparse.ArgumentParser();p.add_argument('--data-dir',required=True);p.add_argument('--continuous',action='store_true');a=p.parse_args()
    while True:
        tick(a.data_dir)
        if not a.continuous:break
        time.sleep(3)


if __name__=='__main__':main()
