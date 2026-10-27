"""Poll FutureEval without submitting forecasts; save point-in-time snapshots."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

API_ROOT = "https://www.metaculus.com/api/posts/"
TOURNAMENT = "fall-futureeval-2026"
OPEN_PAGE_LIMIT = 30
ARCHIVE_PAGES_PER_RUN = 5
RESEARCH_PER_RUN = 2
MAX_RESEARCH_RETRIES = 5


def get_json(url: str, token: str) -> dict:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "www.metaculus.com" or not parts.path.startswith("/api/posts/"):
        raise ValueError("Refusing non-Metaculus posts API URL")
    request = Request(url, headers={
        "Authorization": f"Token {token}", "Accept": "application/json",
        "User-Agent": "futureeval-question-monitor/0.2",
    })
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                data = json.load(response)
            detail = parts.path.rstrip('/').removeprefix('/api/posts/').isdigit()
            if not isinstance(data, dict) or (not detail and not isinstance(data.get("results"), list)) or (detail and not isinstance(data.get('id'), int)):
                raise ValueError("Unexpected Metaculus posts response")
            return data
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
            retry_after = exc.headers.get("Retry-After")
            try:
                delay = min(max(int(retry_after), 1), 30) if retry_after else 5 * (attempt + 1)
            except ValueError:
                delay = 5 * (attempt + 1)
            print(f"Metaculus HTTP {exc.code}; retrying in {delay}s", flush=True)
            time.sleep(delay)
    raise AssertionError("Unreachable retry state")


def _initial_url(open_only: bool) -> str:
    params = {"tournaments": TOURNAMENT, "limit": 100, "include_descriptions": "true", "with_cp": "false"}
    if open_only:
        params["statuses"] = "open"
    return API_ROOT + "?" + urlencode(params)


def _next_url(value: object) -> str | None:
    if not value:
        return None
    if not isinstance(value, str):
        raise ValueError("Unexpected pagination URL")
    parts = urlsplit(value)
    if parts.scheme != "https" or parts.netloc != "www.metaculus.com" or parts.path != "/api/posts/":
        raise ValueError("Unexpected pagination destination")
    return value


def collect_pages(token: str, start_url: str, max_pages: int) -> tuple[list[dict], str | None, int]:
    posts: list[dict] = []
    visited: set[str] = set()
    url: str | None = start_url
    pages = 0
    while url and pages < max_pages:
        if url in visited:
            raise ValueError("Metaculus pagination loop")
        visited.add(url)
        page = get_json(url, token)
        posts.extend(row for row in page["results"] if isinstance(row, dict))
        # Metaculus uses an infinite-count paginator: even an empty page has next.
        url = _next_url(page.get("next")) if page['results'] else None
        pages += 1
    return posts, url, pages


def collect_questions(token: str) -> tuple[list[dict], bool]:
    posts, next_url, _ = collect_pages(token, _initial_url(True), OPEN_PAGE_LIMIT)
    return posts, next_url is None


def collect_archive_pages(token: str, start_url: str) -> tuple[list[dict], str | None, int, str | None]:
    posts: list[dict] = []
    url: str | None = start_url
    visited: set[str] = set()
    pages = 0
    while url and pages < ARCHIVE_PAGES_PER_RUN:
        if url in visited:
            return posts, url, pages, "Metaculus pagination loop"
        visited.add(url)
        try:
            page = get_json(url, token)
            next_url = _next_url(page.get("next")) if page['results'] else None
        except Exception as exc:
            return posts, url, pages, f"{type(exc).__name__}: {exc}"
        posts.extend(row for row in page["results"] if isinstance(row, dict))
        url = next_url
        pages += 1
    return posts, url, pages, None


def _questions(post: dict) -> list[dict]:
    rows = []
    question = post.get("question")
    if isinstance(question, dict) and isinstance(question.get("id"), int):
        rows.append(question)
    group = post.get("group_of_questions")
    if isinstance(group, dict):
        rows.extend(row for row in group.get("questions") or [] if isinstance(row, dict) and isinstance(row.get("id"), int))
    return rows


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _load_state(path: Path) -> dict:
    if not path.exists():
        # Legacy snapshot artifacts have indexes but no durable state.json.
        # Import known IDs without claiming that acquisition was completed.
        seen = set()
        for index_path in path.parent.rglob('index.json'):
            index = json.loads(index_path.read_text(encoding='utf-8'))
            if index.get('tournament') != TOURNAMENT:
                continue
            for row in index.get('questions', []):
                if row.get('snapshot_saved') and isinstance(row.get('question_id'), int):
                    seen.add(row['question_id'])
        return {"schema_version": 1, "seen_question_ids": sorted(seen), "researched_question_ids": [], "archive_next_url": None,
                'legacy_snapshot_ids_imported': len(seen)}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema_version") != 1:
        raise ValueError("Unsupported monitor state schema")
    return state


def snapshot_questions(token: str, root: Path = Path("snapshots/monitor"), *, research: bool = False) -> Path:
    now = datetime.now(timezone.utc)
    output = root / now.strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=True)
    state_path = root / "state.json"
    state = _load_state(state_path)
    seen = set(state["seen_question_ids"])
    researched = set(state["researched_question_ids"])
    retries = state.setdefault('research_retries', {})
    # Repair IDs marked complete by older monitors after an interrupted run.
    for qid in list(researched):
        ledger = root / 'retrieval' / str(qid) / 'bundle.json'
        if ledger.exists():
            result = json.loads(ledger.read_text(encoding='utf-8')).get('result')
            if not result or result.get('incomplete'):
                researched.discard(qid)
    try:
        open_posts, open_complete = collect_questions(token)
    except Exception as exc:
        _write_json(output / "index.json", {"tournament": TOURNAMENT, "retrieved_at_utc": now.isoformat(),
                                            "error": f"Open scan failed: {type(exc).__name__}: {exc}", "questions": []})
        raise

    archive_start = state.get("archive_next_url") or _initial_url(False)
    archive_posts, archive_next, archive_pages, archive_error = collect_archive_pages(token, archive_start)

    posts = {row["id"]: row for row in archive_posts if isinstance(row.get("id"), int)}
    posts.update({row["id"]: row for row in open_posts if isinstance(row.get("id"), int)})
    # List responses cap group children at three; detail responses are complete.
    for post_id, post in list(posts.items()):
        if post.get('group_of_questions'):
            detail = get_json(f'{API_ROOT}{post_id}/?include_descriptions=true&with_cp=false', token)
            if detail.get('id') != post_id or not detail.get('group_of_questions'):
                raise ValueError('Group detail identity mismatch; refuse a partial question scan')
            posts[post_id] = detail
    index: list[dict] = []
    pending_research: list[tuple[dict, dict]] = []
    for post in posts.values():
        questions = _questions(post)
        if not questions:  # Ignore announcements.
            continue
        post_id = post["id"]
        _write_json(output / f"{post_id}.json", {"post": post, "retrieved_at_utc": now.isoformat()})
        for question in questions:
            qid = question["id"]
            is_new = qid not in seen
            from ForecastAgent.competition.lifecycle import describe
            lifecycle = describe(post, question, now.isoformat())
            is_open = lifecycle['open']
            index.append({"post_id": post_id, "question_id": qid,
                          "title": question.get("title") or post.get("title"),
                          "type": question.get("type"), "status": question.get("status") or post.get("status"),
                          "url": f"https://www.metaculus.com/questions/{post_id}/",
                          "new": is_new, **lifecycle, "open": is_open, "snapshot_saved": True})
            seen.add(qid)
            if is_open and question.get("type") == "binary" and qid not in researched:
                retry = retries.get(str(qid), {})
                due = datetime.fromisoformat(retry['next_retry_at']) if retry.get('next_retry_at') else now
                if retry.get('attempts', 0) < MAX_RESEARCH_RETRIES and due <= now:
                    pending_research.append((post, question))

    # Persist poll state before any long model work.
    state.update(seen_question_ids=sorted(seen), researched_question_ids=sorted(researched),
                 archive_next_url=archive_next, updated_at_utc=now.isoformat())
    _write_json(state_path,state)
    research_results=run_pending_research(pending_research,state,root,output,now) if research and open_complete else []
    researched=set(state['researched_question_ids'])

    _write_json(output / "index.json", {
        "tournament": TOURNAMENT, "retrieved_at_utc": now.isoformat(), "code_commit": os.environ.get("GITHUB_SHA"),
        "question_count": len(index), "new_question_count": sum(row["new"] for row in index),
        "open_scan_complete": open_complete, "open_question_count": sum(row["open"] for row in index),
        "lifecycle_counts": {phase: sum(row['lifecycle'] == phase for row in index) for phase in
            ('upcoming', 'open', 'closed_waiting_resolution', 'resolved', 'unpublished_or_unapproved', 'unknown')},
        "resolution_overdue_count": sum(row['resolution_overdue'] for row in index),
        "archive_pages_scanned": archive_pages, "archive_cycle_complete": archive_next is None,
        "archive_error": archive_error, "research": research_results, "questions": index,
        "research_requested": os.environ.get("QUEUE_READ_ONLY_RESEARCH", "1") == "1",
        "snapshot_finished_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    state.update({"seen_question_ids": sorted(seen), "researched_question_ids": sorted(researched),
                  "archive_next_url": archive_next, "updated_at_utc": now.isoformat()})
    _write_json(state_path, state)
    print(f"Saved {len(index)} snapshots; {sum(row['new'] for row in index)} new; "
          f"open complete={open_complete}; archive pages={archive_pages}; archive error={archive_error}", flush=True)
    for row in index:
        if row["new"]:
            print(f"NEW {row['question_id']} [{row['status']}] {row['title']}", flush=True)
    if not open_complete:
        raise RuntimeError("Open-question scan exceeded page limit; snapshot is incomplete")
    if archive_error and os.environ.get("OFFICIAL_OPEN_SCAN_PRIORITY") != "1":
        raise RuntimeError(f"Archive scan incomplete: {archive_error}")
    if any(row["status"] == "failed" for row in research_results):
        raise RuntimeError("One or more read-only research snapshots failed; see index.json")
    return output



def run_pending_research(pending_research, state, root, output, now):
    researched=set(state["researched_question_ids"])
    retries=state.setdefault('research_retries',{})
    research_results=[]
    from ForecastAgent.agent import run_research
    for post, question in pending_research[:RESEARCH_PER_RUN]:
        qid = question["id"]
        try:
            task_directory = root / "retrieval" / str(qid)
            request = {"question": question.get("title") or post.get("title") or "",
                       "resolution_criteria": question.get("resolution_criteria") or post.get("resolution_criteria") or "",
                       "fine_print": question.get("fine_print") or post.get("fine_print") or "", "mode": "live",
                       "pipeline":"collection","acquisition_profile":"collection_v3"}
            # Existing task input stays frozen even if the upstream question changes.
            # Updated question snapshots remain available separately for review.
            existing = task_directory / "bundle.json"
            if existing.exists():
                request = json.loads(existing.read_text(encoding="utf-8"))["request"]
            report = run_research(request, task_directory, os.environ["TAVILY_API_KEY"], os.environ["OPENROUTER_API_KEY"])
            _write_json(output / f"research-{qid}.json", report)
            if not report.get('result') or report['result'].get('incomplete'):
                raise RuntimeError('Agent interrupted; durable ledger retained for retry')
            researched.add(qid)
            retries.pop(str(qid), None)
            research_results.append({"question_id": qid, "status": "saved", "retrieval_status": report["result"]["status"]})
        except Exception as exc:
            attempts = retries.get(str(qid), {}).get('attempts', 0) + 1
            retries[str(qid)] = {'attempts': attempts, 'next_retry_at': (now + timedelta(minutes=min(15 * 2 ** (attempts - 1), 240))).isoformat(),
                                 'last_error': type(exc).__name__, 'needs_attention': attempts >= MAX_RESEARCH_RETRIES}
            research_results.append({"question_id": qid, "status": "failed", "retry": retries[str(qid)], "error": f"{type(exc).__name__}: {exc}"})
        state['researched_question_ids']=sorted(researched)
        _write_json(root / 'state.json',state)
    state['researched_question_ids']=sorted(researched)
    _write_json(root / 'state.json',state)
    return research_results


def research_saved_questions(source_root, root):
    """Consume captured open questions without blocking or repeating the API poll."""
    indexes=list(source_root.glob('*/index.json'))
    if not indexes: raise ValueError('No incoming monitor snapshot index')
    source=max(indexes,key=lambda p:p.parent.name)
    index=json.loads(source.read_text(encoding='utf-8'))
    if index.get('tournament')!=TOURNAMENT or not index.get('open_scan_complete'):
        raise ValueError('Incoming open scan is incomplete or belongs to another tournament')
    now=datetime.now(timezone.utc)
    root.mkdir(parents=True,exist_ok=True)
    state=_load_state(root / 'state.json')
    output=root / 'reports' / now.strftime('%Y%m%dT%H%M%SZ')
    output.mkdir(parents=True,exist_ok=True)
    pending=[]
    open_ids=set()
    # Prefer unfinished ledgers, then unseen open binary questions.
    for row in index['questions']:
        qid=row.get('question_id');post_id=row.get('post_id')
        if not row.get('open') or row.get('type')!='binary': continue
        open_ids.add(qid)
        if type(qid) is not int or type(post_id) is not int: raise ValueError('Invalid snapshot IDs')
        ledger=root/'retrieval'/str(qid)/'bundle.json'
        if qid in state['researched_question_ids']:
            if not ledger.exists(): raise ValueError('Completed question ledger missing; refusing budget reset')
            result=json.loads(ledger.read_text(encoding='utf-8')).get('result')
            if result and not result.get('incomplete'): continue
            state['researched_question_ids'].remove(qid)
        retry=state.get('research_retries',{}).get(str(qid),{})
        due=datetime.fromisoformat(retry['next_retry_at']) if retry.get('next_retry_at') else now
        if retry.get('attempts',0)>=MAX_RESEARCH_RETRIES or due>now: continue
        post=json.loads((source.parent/f'{post_id}.json').read_text(encoding='utf-8'))['post']
        question=next((q for q in _questions(post) if q['id']==qid),None)
        if question is None: raise ValueError('Question missing from captured post')
        pending.append((post,question))
    if not index.get('research_requested',True): pending=[]
    pending.sort(key=lambda item:not (root/'retrieval'/str(item[1]['id'])/'bundle.json').exists())
    results=run_pending_research(pending,state,root,output,now)
    updates=refresh_due_ledgers(root,open_ids,now) if index.get('research_requested',True) else []
    _write_json(output/'index.json',{'source_snapshot':str(source),'source_run_id':os.environ.get('SOURCE_MONITOR_RUN'),
        'code_commit':os.environ.get('GITHUB_SHA'),'research':results,'pending_count':len(pending),'updates':updates,
        'finished_at_utc':datetime.now(timezone.utc).isoformat()})
    if any(r['status']=='failed' for r in results): raise RuntimeError('Collection interrupted; durable state preserved')
    print(f'Collection worker: {len(results)} tasks; {len(pending)} pending; no forecasts',flush=True)
    return output


def refresh_due_ledgers(root,open_ids,now):
    """Refresh important completed live captures with existing update budgets only."""
    from ForecastAgent.runtime.retrieval import RetrievalTask,parse_time
    from ForecastAgent.runtime.task_lock import task_lock
    results=[]
    for qid in sorted(open_ids):
        directory=root/'retrieval'/str(qid)
        path=directory/'bundle.json'
        if not path.exists(): continue
        bundle=json.loads(path.read_text(encoding='utf-8'))
        if bundle.get('mode')!='live' or bundle.get('pipeline')!='collection' or not bundle.get('result') or bundle['result'].get('incomplete'): continue
        critical={n['id'] for n in bundle.get('plan') or [] if n.get('priority')=='critical'}
        targets={e['url'] for e in bundle.get('excerpts',[]) if critical.intersection(e.get('need_ids',[]))}
        targets.update(u for u,row in bundle.get('selected_sources',{}).items() if critical.intersection(row.get('need_ids',[])))
        targets.update(a.get('url') for a in bundle.get('fetch_attempts',[]) if a.get('channel')=='official' and critical.intersection(a.get('need_ids',[])))
        due=[]
        for url in sorted(targets):
            page=bundle['pages'].get(url)
            if not page or page.get('capture_method') not in {'direct_http','pdf_text','official_data','structured_data'}: continue
            stamp=parse_time(page.get('last_checked_at_utc') or page.get('retrieved_at_utc'))
            stamps=[parse_time(a.get('at')) for a in bundle.get('update_attempts',[]) if a.get('url')==url and parse_time(a.get('at'))]
            if stamp: stamps.append(stamp)
            attempted=max(stamps) if stamps else None
            if attempted and (now-attempted).total_seconds()<43200: continue
            due.append(url)
        if not due: continue
        with task_lock(directory):
            task=RetrievalTask(directory,bundle['request'])
            budget=task.budget()
            count=min(3,budget['update_http_remaining'],budget['update_http_today_remaining'])
            if count<=0:
                results.append({'question_id':qid,'status':'update_budget_exhausted'});continue
            report=task.refresh_sources({'urls':due[:count]})
            results.append({'question_id':qid,'status':'refreshed','report':report})
    return results


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--research-saved',action='store_true')
    parser.add_argument('--source-root',type=Path,default=Path('snapshots/incoming'))
    parser.add_argument('--root',type=Path,default=Path('snapshots/monitor'))
    args=parser.parse_args()
    if args.research_saved: research_saved_questions(args.source_root,args.root)
    else: snapshot_questions(os.environ["METACULUS_TOKEN"],root=args.root,research=os.environ.get("RUN_READ_ONLY_RESEARCH") == "1")
