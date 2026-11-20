"""Typed graders, first version: the definitions that score_v2.py builds on (units, label aliases, chainage,
quantity with tolerance, the M/K/J composition counts). Copied line by line from the evaluation code; the parts of the
original module that load the run harness and its command line were left out. Use score_v2.py for grading."""
import json,re
from decimal import Decimal,InvalidOperation
UNITS={'mm':('length','0.001'),'cm':('length','0.01'),'m':('length','1'),'m²':('area','1'),'mm²':('area','0.000001'),'m³':('volume','1'),'cm³':('volume','0.000001'),'kg':('mass','1'),'t':('mass','1000'),'W':('power','1'),'kW':('power','1000'),'根':('count','1'),'套':('count','1')}
ALIASES={'C－C':'C-C','C—C':'C-C','F－F':'F-F','F—F':'F-F','M15－5':'M15-5','朝跨内':'toward_span','向跨内':'toward_span'}
def label(s):
    if not isinstance(s,str):raise ValueError('not label')
    s=s.strip();return ALIASES.get(s,s)
def chainage(s):
    m=re.fullmatch(r'K(\d+)\+(\d+(?:\.\d{1,3})?)',s.strip(),re.I)
    if not m or Decimal(m[2])>=1000:raise ValueError('invalid chainage')
    return int(Decimal(m[1])*1000000+Decimal(m[2])*1000)
def quantity(a,g,tol):
    if not isinstance(a,dict) or set(a)!={'value','unit'} or type(a['value']) not in (int,float):return False
    au,gu=UNITS[a['unit']],UNITS[g['unit']]
    if au[0]!=gu[0]:return False
    av=Decimal(str(a['value']))*Decimal(au[1]);gv=Decimal(str(g['value']))*Decimal(gu[1])
    return av.is_finite() and abs(av-gv)<=Decimal(str(tol))*Decimal(gu[1])
PAIR_FIELDS=['model_id','budget_group','repeat_id','data_version','prompt_version','config_hash']
def metrics(manifest,rows,identity):
    keyed={};
    for r in rows:
        if all(r.get(k)==identity.get(k) and r.get(k) is not None for k in PAIR_FIELDS):
            k=(r['condition'],r['id'])
            if k in keyed:raise ValueError('Duplicate matched episode; do not select by correctness')
            keyed[k]=r
    N=len(manifest['groups']);M=K=J=0;incomplete=[];local_scores=[];by_count={}
    for g in manifest['groups']:
        paths=g['paths']
        if any(not p['local_ids'] for p in paths):raise ValueError('Empty local path')
        ids=sorted({i for p in paths for i in p['local_ids']})
        p=keyed.get(('parent_agent',g['parent_id']));ls=[keyed.get(('local_agent',i)) for i in ids]
        if not p or any(not r or r['run_status']!='completed' for r in [p,*ls]):incomplete.append(g['parent_id']);continue
        M+=1;lookup=dict(zip(ids,ls));A=any(all(lookup[i]['correct'] for i in path['local_ids']) for path in paths);Y=bool(p['correct']);K+=int(A);J+=int(A and Y)
        local_scores.extend(int(r['correct']) for r in ls);cell=by_count.setdefault(str(len(ids)),{'observed':0,'local_complete':0,'parent_correct_given_local':0});cell['observed']+=1;cell['local_complete']+=int(A);cell['parent_correct_given_local']+=int(A and Y)
    return {'diagnostic_manifest_hash':manifest['manifest_hash'],**identity,'condition':'parent_agent+local_agent','eligible_count':N,'observed_complete_count':M,'local_complete_count':K,'local_complete_parent_correct':J,'completion_rate':M/N if N else None,'local_coverage_observed':K/M if M else None,'local_coverage_fixed_lower':K/N if N else None,'local_coverage_fixed_upper':(K+N-M)/N if N else None,'ca_local':J/K if K else None,'cg_local':(K-J)/K if K else None,'gap_prevalence_observed':(K-J)/M if M else None,'provisional':M!=N,'incomplete_parent_ids':incomplete,'local_mean_accuracy_observed':sum(local_scores)/len(local_scores) if local_scores else None,'by_local_probe_count':by_count}
