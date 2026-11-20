"""Typed, deterministic graders for probes and option sets, grading version cad-grading-v2.3.

Builds on the first version of the graders (score.py) and differs from it as follows:
- units: count units 块/个/组/处/株 are added, and every count unit is its own dimension
  (an answer in 个 does not match gold in 块; the first version treated 根 and 套 as one dimension);
- quantity answers given as a "数值+单位" string are parsed deterministically into {value, unit};
  grade_detail() reports parse_source so parsed answers can be counted separately;
- label-like answers (label, endpoints, slot labels, direction, chainage) are normalized:
  strip, full-width to half-width, one hyphen for all dash variants, angle brackets removed;
- choice_set grades 6-option multi-select items by exact match of the selected set;
- bare_json_answer: when a response holds no {"answer": ...} object, a final JSON object (or array) whose shape is
  exactly what the grader expects is read as the answer, with parse_source bare_json (counted apart).
"""
import json,re
from collections import Counter
from decimal import Decimal,InvalidOperation
from score import UNITS as UNITS_V1,ALIASES as ALIASES_V1,chainage as chainage_v1,metrics,PAIR_FIELDS
GRADING_VERSION='cad-grading-v2.3'
COUNT_UNITS=['根','套','块','个','组','处','株']
UNITS={**{u:d for u,d in UNITS_V1.items() if d[0]!='count'},**{u:('count:'+u,'1') for u in COUNT_UNITS}}
GRADERS={'chainage','quantity','directed_quantity','endpoints','label','slots'}
PARSE_SOURCES=['structured','string_parsed','unparsed','bare_json']
ITEM_GRADERS={'choice_set'}  # item-level graders; not in the probe registry GRADERS
DASHES='‐‑‒–—―−－﹣⁃'
BRACKETS='<>〈〉《》‹›'
def halfwidth(s):
    return ''.join(' ' if c=='　' else chr(ord(c)-0xFEE0) if '！'<=c<='～' else c for c in s)
def normalize(s):
    if not isinstance(s,str):raise ValueError('not label')
    s=halfwidth(s).strip()
    for d in DASHES:s=s.replace(d,'-')
    for b in BRACKETS:s=s.replace(b,'')
    return s.strip()
ALIASES={normalize(k):v for k,v in ALIASES_V1.items()}
def label(s):
    s=normalize(s);return ALIASES.get(s,s)
def chainage(s):return chainage_v1(normalize(s))
_UNIT_ALT='|'.join(sorted(map(re.escape,UNITS),key=len,reverse=True))
_QTY=re.compile(r'([+-]?\d+(?:\.\d+)?)\s*('+_UNIT_ALT+r')')
def parse_quantity(a):
    """Return ({value,unit}, parse_source). Only a whole-string "数值+单位" is parsed; nothing is guessed."""
    if isinstance(a,dict):return a,'structured'
    if not isinstance(a,str):return a,'unparsed'
    m=_QTY.fullmatch(re.sub(r'(?<=\d),(?=\d{3}(?!\d))','',normalize(a)))
    if not m:return a,'unparsed'
    return {'value':float(m[1]) if '.' in m[1] else int(m[1]),'unit':m[2]},'string_parsed'
def quantity(a,g,tol):
    if not isinstance(a,dict) or set(a)!={'value','unit'} or type(a['value']) not in (int,float):return False
    au,gu=UNITS[a['unit']],UNITS[g['unit']]
    if au[0]!=gu[0]:return False
    av=Decimal(str(a['value']))*Decimal(au[1]);gv=Decimal(str(g['value']))*Decimal(gu[1])
    return av.is_finite() and abs(av-gv)<=Decimal(str(tol))*Decimal(gu[1])
def _merge(sources):
    return 'string_parsed' if 'string_parsed' in sources else 'unparsed' if 'unparsed' in sources else 'structured'
_ANSWER_LINE=re.compile(r'答案\s*:\s*(.*)')
_SEPARATORS=re.compile(r'[\s,、;/]+')
def parse_choice_set(text,option_keys):
    """Letters from the LAST line starting with 答案: / 答案： (answer_line_v1). None = format error.
    Per line: full-width -> half-width, strip spaces and * # > at both ends. Content after the colon: drop * ` 【】[],
    a trailing 。 or ., then the separators (whitespace , 、 ; /). What remains must be letters only (upper-cased),
    every one an actual option key; duplicates count once; order ignored; the empty set is a format error."""
    if not isinstance(text,str):return None
    content=None
    for line in re.split(r'\r\n|\r|\n',text):
        m=_ANSWER_LINE.fullmatch(halfwidth(line).strip().strip('*#> ').strip())
        if m:content=m[1]
    if content is None:return None
    content=re.sub(r'[*`【】\[\]]','',content).strip().rstrip('。.').strip()
    letters=_SEPARATORS.sub('',content)
    if not re.fullmatch(r'[A-Za-z]+',letters):return None
    letters=letters.upper()
    if not set(letters)<=set(option_keys):return None
    return sorted(set(letters))
def choice_set_detail(probe,text):
    keys=probe['option_keys'];gold=set(probe['gold_answer']);sel=parse_choice_set(text,keys)
    chosen=set(sel or [])
    return {'correct':sel is not None and chosen==gold,'parse_source':'answer_line' if sel is not None else 'unparsed',
            'format_error':sel is None,'selected':sel,'tp':len(chosen&gold),'fp':len(chosen-gold),'fn':len(gold-chosen),
            'tn':len(set(keys)-chosen-gold)}
def grade_detail(probe,answer):
    """{'correct': bool, 'parse_source': structured|string_parsed|unparsed}; parse_source matters for quantity slots only.
    choice_set (v2.1): answer is the raw final text; see choice_set_detail for the extra fields."""
    if probe['grader_id']=='choice_set':return choice_set_detail(probe,answer)
    g=probe['gold_answer'];kind=probe['grader_id'];tol=probe['tolerance'];source='structured'
    try:
        if kind=='chainage':ok=chainage(answer)==chainage(g)
        elif kind=='quantity':
            answer,source=parse_quantity(answer);ok=quantity(answer,g,tol)
        elif kind=='directed_quantity':ok=isinstance(answer,dict) and set(answer)=={'value','unit','direction'} and label(answer['direction'])==label(g['direction']) and quantity({k:answer[k] for k in ('value','unit')},{k:g[k] for k in ('value','unit')},tol)
        elif kind=='endpoints':ok=isinstance(answer,list) and len(answer)==2 and len({label(x) for x in answer})==2 and {label(x) for x in answer}=={label(x) for x in g}
        elif kind=='label':ok=label(answer)==label(g)
        elif kind=='slots':
            ok=isinstance(answer,dict) and set(answer)==set(g);sources=[]
            for k,v in (g.items() if ok else []):
                if isinstance(v,dict) and 'unit' in v:
                    parsed,s=parse_quantity(answer[k]);sources.append(s);ok=ok and quantity(parsed,v,tol)
                else:ok=ok and label(answer[k])==label(v)
            source=_merge(sources)
        else:ok=False
    except (ValueError,TypeError,KeyError,AttributeError,InvalidOperation):ok=False
    return {'correct':bool(ok),'parse_source':source}
def grade(probe,answer):return grade_detail(probe,answer)['correct']
def bare_json_answer(probe,raw):
    """v2.3: the last JSON value that closes the final text, if it has exactly the expected shape (slots: the gold
    fields; quantity: value+unit; directed_quantity: value+unit+direction; endpoints: a 2-item list); else None."""
    raw=(raw or '').rstrip().removesuffix('```').rstrip()
    kind,g=probe['grader_id'],probe['gold_answer']
    want={'slots':set(g) if isinstance(g,dict) else None,'quantity':{'value','unit'},'directed_quantity':{'value','unit','direction'}}.get(kind)
    for i in range(len(raw)-1,-1,-1):
        if raw[i] not in '{[':continue
        try:v=json.loads(raw[i:])
        except ValueError:continue
        if want is not None and isinstance(v,dict) and set(v)==want:return v
        if kind=='endpoints' and isinstance(v,list) and len(v)==2:return v
    return None
def parse_source_counts(details):
    """Separate tallies so string-parsed answers are never silently pooled with structured ones."""
    c=Counter((d['parse_source'],d['correct']) for d in details)
    return {s:{'total':c[(s,True)]+c[(s,False)],'correct':c[(s,True)]} for s in PARSE_SOURCES}
