"""Durable four-capture allocation with balanced, task-bound credential names."""
from __future__ import annotations
from copy import deepcopy
from contextlib import contextmanager
import json
import os
from pathlib import Path
from .contracts import canonical, now
from .repository import atomic_bytes, file_lock, ensure

POLICY="balanced-four-capture-v1"
ALIASES={
    "OPENROUTER2":("OPENROUTER_API_KEY",),"OPENROUTER":("OPENROUTER_API_KEY2",),
    "TAVILY_KEY3":("TAVILY_API_KEY","TAVILY_API_KEY3"),"TAVILY_KEY4":("TAVILY_API_KEY2","TAVILY_API_KEY4"),
    "EXA_API":("EXA_API_KEY",),"EXA_API2":("EXA_API_KEY2",)}
NAMES={"openrouter":("OPENROUTER2","OPENROUTER"),"tavily":("TAVILY_KEY3","TAVILY_KEY4"),"exa":("EXA_API","EXA_API2")}


def configure_credentials(environ=None):
    """Normalize existing aliases in memory; return names only, never values or hashes."""
    env=os.environ if environ is None else environ
    for target,aliases in ALIASES.items():
        if not env.get(target):
            for alias in aliases:
                if env.get(alias):env[target]=env[alias];break
    pools={}
    for provider,names in NAMES.items():
        ensure(all(env.get(name,"").strip() for name in names),"MISSING_"+provider.upper()+"_POOL")
        ensure(env[names[0]]!=env[names[1]],"DUPLICATE_"+provider.upper()+"_KEYS")
        pools[provider]=list(names)
    return pools


def credential_names(run,provider):
    params=run["execution_profile"]["parameters"]
    assigned=params.get("credentials",{}).get(provider)
    if assigned:
        ensure(assigned in NAMES[provider],"INVALID_CREDENTIAL_ASSIGNMENT")
        # Model tasks stay on their assigned pool. Tavily may fail over explicitly;
        # Exa is one physical attempt, so it also receives exactly one name.
        return (assigned,)+tuple(n for n in NAMES[provider] if n!=assigned) if provider=="tavily" else (assigned,)
    return ("OPENROUTER2",) if provider=="openrouter" else NAMES[provider]


class CaptureLanes:
    def __init__(self,root,pools=None):
        self.root=Path(root);self.path=self.root/"control/credential-lanes.json";self.lock=self.root/".locks/credential-lanes.lock"
        with file_lock(self.lock):
            if not self.path.exists():
                ensure(pools is not None,"LANES_NOT_CONFIGURED")
                ensure(pools=={p:list(n) for p,n in NAMES.items()},"INVALID_CREDENTIAL_POOLS")
                self.write(dict(policy=POLICY,capture_concurrency=4,slots_per_model_key=2,pools=pools,assignments={},allocations={}))
            state=self.read()
            ensure(state['policy']==POLICY and state['capture_concurrency']==4 and state['slots_per_model_key']==2,"LANE_POLICY_CHANGED")
            if pools is not None:ensure(state['pools']==pools,"CREDENTIAL_POOL_CHANGED")
    def read(self):return json.loads(self.path.read_text(encoding="utf-8"))
    def write(self,state):atomic_bytes(self.path,(canonical(state)+"\n").encode())
    def claim(self,question_id):
        with file_lock(self.lock):
            state=self.read();previous=state['assignments'].get(question_id)
            if previous:
                ensure(previous['capture_active'],"CAPTURE_ALREADY_RELEASED")
                return deepcopy(previous['credentials'])
            active=[r for r in state['assignments'].values() if r['capture_active']]
            if len(active)>=4:return None
            chosen={}
            for provider,names in state['pools'].items():
                counts=state['allocations'].setdefault(provider,{n:0 for n in names})
                loads={n:sum(r['credentials'][provider]==n for r in active) for n in names}
                eligible=[n for n in names if provider!='openrouter' or loads[n]<2]
                if not eligible:return None
                name=min(eligible,key=lambda n:(loads[n],counts[n],names.index(n)))
                chosen[provider]=name;counts[name]+=1
            state['assignments'][question_id]=dict(credentials=chosen,capture_active=True,allocated_at=now(),released_at=None)
            self.write(state);return deepcopy(chosen)
    def release(self,question_id):
        with file_lock(self.lock):
            state=self.read();row=state['assignments'].get(question_id)
            if row is None or not row['capture_active']:return False
            row.update(capture_active=False,released_at=now());self.write(state);return True


@contextmanager
def analysis_slot(root,timeout=1200):
    """One question at a time, independent from capture slot ownership."""
    with file_lock(Path(root)/'.locks/single-question-analysis.lock',timeout=timeout):yield
