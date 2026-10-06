"""Freeze release code independently from per-task acquisition reservations."""
import argparse
import hashlib
from pathlib import Path
from ForecastAgent.competition.queue import load, save

ROOT=Path(__file__).resolve().parents[2]
PATH=Path('ForecastAgent/releases/manifest.json')


def sha(path):return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def build(root=ROOT):
    root=Path(root)
    paths=[p for p in (root/'ForecastAgent').rglob('*') if p.is_file() and '__pycache__' not in p.parts and
           p.suffix in {'.py','.md','.txt'} and 'tests' not in p.parts]
    base=load(root/'ForecastAgent/experiments/acquisition_v2_baseline.json')
    result={'schema':'forecastagent-release-source-v1','version':'1.0.2',
            'composition':'V3 acquisition + independent supplement + unchanged 1.0.1 live analysis core',
            'analysis_core_tag':'v1.0.1','analysis_core_commit':base['baseline_commit'],
            'acquisition_base_commit':'4f7bbed','budget_policy':base['baseline_limits'],
            'hash_encoding':'UTF-8 source bytes with LF newlines',
            'files_sha256_lf':{p.relative_to(root).as_posix():sha(p) for p in sorted(paths)}}
    save(root/PATH,result);return result


def verify(root=ROOT):
    root=Path(root);m=load(root/PATH)
    if m.get('schema')!='forecastagent-release-source-v1' or m.get('version')!='1.0.2':raise ValueError('Wrong release manifest')
    for name,expected in m['files_sha256_lf'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path)!=expected:raise ValueError('Frozen release source changed: '+name)
    return m


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build',action='store_true');args=p.parse_args()
    result=build() if args.build else verify()
    print(f"Release {result['version']}: {len(result['files_sha256_lf'])} frozen source files")
