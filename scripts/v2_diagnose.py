"""Research-only baseline metrics and miss/opportunity forensics after candidate audit."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import json
import os
from pathlib import Path
import re
import sys

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from rapidfuzz.fuzz import ratio, token_sort_ratio, WRatio

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'code/business_entity_resolution/src'))
from er.candidates_v2.runtime import R,W,sha,write_json,log,connect,populations,Monitor
from er.candidates_v2.prospective import scoped_gt_rows,assert_research_only
from er.features.exact import script_class,postal,digit_sequence


def grams(text,n):
    return set(text[i:i+n] for i in range(max(0,len(text)-n+1)))


def jaccard(a,b):
    return len(a&b)/len(a|b) if a or b else 0.0


def load_gt():
    research,sealed=populations()
    manifest_path=R/'v1_baseline_manifest.json'
    manifest=json.loads(manifest_path.read_text())
    if manifest['status']!='GENERATED_CHECKSUMMED_STRUCTURALLY_AUDITED' or manifest['gt_used']:
        raise PermissionError('Label-free candidate audit must finish first')
    audit=json.loads((R/'v1_structural_audit.json').read_text())
    if (audit['status']!='PASS' or audit['label_access'] or
            audit['baseline_manifest_sha256']!=sha(manifest_path) or
            audit['outside_research'] or audit['missing_targets'] or audit['duplicate_pairs']):
        raise PermissionError('Independent label-free candidate audit must pass first')
    for part in manifest['parts']:
        if sha(ROOT/part['path'])!=part['sha256']: raise PermissionError('Candidate artifact changed before GT join')
    receipt=R/'research_gt_receipt.json'
    path=R/'research_gt.parquet'
    if receipt.exists():
        saved=json.loads(receipt.read_text())
        if sha(path)!=saved['sha256'] or sha(manifest_path)!=saved['baseline_manifest_sha256']:
            raise PermissionError('Research GT restart checksum mismatch')
        assert_research_only(pq.read_table(path,columns=['s1'])['s1'].to_pylist(),research,sealed)
        return
    log('research_label_access_start',command='.venv\\Scripts\\python.exe -B scripts\\v2_diagnose.py',
        v2_research_label_read=True,research_s1=len(research),
        baseline_manifest_sha256=sha(manifest_path),
        label_source='dataset/train/train_ground_truth.tsv',
        scope='Exact membership filter before target-label decoding; only v2_candidate_research',
        format_limitation='TSV physically streamed; nonresearch target-label bytes skipped without decoding, splitting, retention or aggregates')
    rows=[]; universe=[]
    with (ROOT/'dataset/train/train_ground_truth.tsv').open('rb') as stream:
        for entity,targets in scoped_gt_rows(stream,research,sealed):
            universe.append({'s1':entity,'truth_n':len(targets)})
            rows.extend({'s1':entity,'mid':target} for target in targets)
    pq.write_table(pa.Table.from_pylist(rows,schema=pa.schema([('s1',pa.string()),('mid',pa.string())])),path,compression='zstd')
    pq.write_table(pa.Table.from_pylist(sorted(universe,key=lambda x:x['s1'])),R/'research_truth_counts.parquet',compression='zstd')
    write_json(receipt,{'research_s1':len(universe),'gt_links':len(rows),'sha256':sha(path),
                       'baseline_manifest_sha256':sha(manifest_path),'label_scope':'v2_candidate_research only'})
    log('research_label_access_complete',command='.venv\\Scripts\\python.exe -B scripts\\v2_diagnose.py',
        v2_research_label_read=True,receipt_sha256=sha(receipt),output_sha256=sha(path))


def run():
    os.chdir(ROOT); load_gt()
    research,sealed=populations()
    c=connect('diagnose')
    c.execute("CREATE TEMP VIEW gt AS SELECT * FROM read_parquet('work/v2_research/research_gt.parquet')")
    c.execute("CREATE TEMP VIEW candidates AS SELECT * FROM read_parquet('work/v2_research/v1_candidates/*.parquet')")
    c.execute("""CREATE TEMP TABLE target AS SELECT entity_id, country_norm, name_norm,name_nosuffix,name_sorted,name_acronym,addr_norm FROM read_parquet('work/keys/train_s2.parquet')
      UNION ALL SELECT entity_id,country_norm,name_norm,name_nosuffix,name_sorted,name_acronym,addr_norm FROM read_parquet('work/keys/train_s3.parquet')""")
    c.execute("""CREATE TEMP TABLE links AS SELECT g.*,s.country_norm country,t.country_norm target_country,
      s.name_norm sn,t.name_norm tn,s.name_nosuffix sx,t.name_nosuffix tx,s.name_sorted ss,t.name_sorted ts,
      s.name_acronym sac,t.name_acronym tac,s.addr_norm sa,t.addr_norm ta,
      p.source1_entity_id IS NOT NULL recovered,coalesce(p.provenance,0) provenance,
      rn.rsource name_rank,ra.rsource address_rank,hr.rk heavy_rank
      FROM gt g JOIN read_parquet('work/keys/train_s1.parquet') s ON s.entity_id=g.s1
      JOIN target t ON t.entity_id=g.mid
      LEFT JOIN candidates p ON p.source1_entity_id=g.s1 AND p.target_entity_id=g.mid
      LEFT JOIN read_parquet('work/v2_research/v1_ranks/*_rank_name.parquet') rn ON rn.s1=g.s1 AND rn.mid=g.mid
      LEFT JOIN read_parquet('work/v2_research/v1_ranks/*_rank_addr.parquet') ra ON ra.s1=g.s1 AND ra.mid=g.mid
      LEFT JOIN read_parquet('work/v2_research/v1_ranks/*_heavy_rank.parquet') hr ON hr.s1=g.s1 AND hr.mid=g.mid""")
    if c.sql('SELECT count(*) FROM links').fetchone()[0]!=c.sql('SELECT count(*) FROM gt').fetchone()[0]:
        raise RuntimeError('GT source/target join lost or multiplied pairs')
    for field,s,t in [('name','sx','tx'),('addr','sa','ta')]:
        c.execute(f"""CREATE TEMP TABLE shared_{field} AS SELECT s1,mid,count(*) shared_tokens,
         count(*) FILTER(WHERE d.df<=2000) eligible_tokens,min(d.df) min_df,max(d.df) max_df
         FROM (SELECT s1,mid,country,unnest(list_intersect(list_distinct(string_split({s},' ')),list_distinct(string_split({t},' ')))) tok
               FROM links WHERE country=target_country) x
         JOIN read_parquet('work/freeze_gate/df_{field}.parquet') d ON d.cc=x.country AND d.tok=x.tok WHERE length(x.tok)>0 GROUP BY 1,2""")
    cursor=c.execute("""SELECT l.*,coalesce(n.eligible_tokens,0) name_eligible,coalesce(a.eligible_tokens,0) addr_eligible,
      n.min_df name_min_df,n.max_df name_max_df,a.min_df addr_min_df,a.max_df addr_max_df
      FROM links l LEFT JOIN shared_name n USING(s1,mid) LEFT JOIN shared_addr a USING(s1,mid) ORDER BY s1,mid""")
    names=[x[0] for x in cursor.description]
    rows=[dict(zip(names,row)) for row in cursor.fetchall()]
    byentity=defaultdict(list)
    for row in rows: byentity[row['s1']].append(row)
    for i,row in enumerate(rows):
        sn,tn,sa,ta=row['sn'],row['tn'],row['sa'],row['ta']
        eligible=row['country']==row['target_country'] and bool((row['ss'] and row['ss']==row['ts']) or (sa and sa==ta) or row['name_eligible'] or row['addr_eligible'])
        row['v1_key_eligible']=eligible
        row['miss_category']='recovered' if row['recovered'] else ('eligible_but_lost_through_ranking_cap' if eligible else 'no_v1_retrieval_key_eligible')
        row.update(exact_name=bool(sn and sn==tn),exact_nosuffix=bool(row['sx'] and row['sx']==row['tx']),
                   exact_sorted=bool(row['ss'] and row['ss']==row['ts']),
                   name_token_overlap=len(set(row['sx'].split()) & set(row['tx'].split())),
                   address_token_overlap=len(set(sa.split())&set(ta.split())),
                   name_ratio=ratio(sn,tn)/100,name_token_sort_ratio=token_sort_ratio(sn,tn)/100,name_wratio=WRatio(sn,tn)/100,
                   address_ratio=ratio(sa,ta)/100 if sa and ta else 0.,
                   acronym_equal=bool(len(row['sac'])>=3 and row['sac']==row['tac']),
                   initials_equal=bool(''.join(x[0] for x in sn.split()) and ''.join(x[0] for x in sn.split())==''.join(x[0] for x in tn.split())),
                   prefix_overlap=len(os.path.commonprefix([sn,tn])),suffix_overlap=len(os.path.commonprefix([sn[::-1],tn[::-1]])),
                   numeric_overlap=len(set(digit_sequence(sa)) & set(digit_sequence(ta))),
                   postal_equal=bool(postal(sa) and postal(sa)==postal(ta)),
                   house_equal=bool(digit_sequence(sa) and digit_sequence(ta) and digit_sequence(sa)[0]==digit_sequence(ta)[0]),
                   both_address=bool(sa and ta),s1_address_missing=not bool(sa),target_address_missing=not bool(ta),
                   same_script=script_class(sn)==script_class(tn),devanagari_latin={script_class(sn),script_class(tn)}=={1,2},
                   script_relation=f'{script_class(sn)}:{script_class(tn)}',target_source=row['mid'][:2],truth_n=len(byentity[row['s1']]))
        for n in (2,3,4):
            a,b=grams(sn,n),grams(tn,n);row[f'name_{n}gram_overlap']=len(a&b);row[f'name_{n}gram_jaccard']=jaccard(a,b)
            a,b=grams(sa,n),grams(ta,n);row[f'address_{n}gram_overlap']=len(a&b);row[f'address_{n}gram_jaccard']=jaccard(a,b)
        sisters=[x for x in byentity[row['s1']] if x['mid']!=row['mid']]
        seeds=[x for x in sisters if x['recovered']]
        row['true_sister_retrieved']=bool(seeds)
        row['sister_name_similarity']=max((ratio(tn,x['tn'])/100 for x in seeds),default=0.)
        row['sister_address_similarity']=max((ratio(ta,x['ta'])/100 for x in seeds if ta and x['ta']),default=0.)
        row['sister_closer_than_s1']=bool(seeds) and row['sister_name_similarity']>row['name_ratio']
        row['cross_source_sister']=any(x['mid'][:2]!=row['mid'][:2] for x in seeds)
        row['strongest_target_target_name']=max((ratio(tn,x['tn'])/100 for x in sisters),default=0.)
        if i and i%100000==0: print('diagnosed research links',i,flush=True)
    assert_research_only(byentity,research,sealed)
    out=R/'v1_link_diagnostics.parquet'
    pq.write_table(pa.Table.from_pylist(rows),out,compression='zstd')
    c.execute(f"CREATE TEMP VIEW diagnostics AS SELECT * FROM read_parquet('{out.as_posix()}')")
    c.execute("""CREATE TEMP TABLE entity AS SELECT u.s1,u.truth_n,coalesce(h.recovered_n,0) recovered_n,
     coalesce(n.candidates,0) candidates,k.country_norm country,k.addr_norm='' address_missing,
     CASE WHEN u.truth_n=0 THEN 1.0 ELSE 1.25*coalesce(h.recovered_n,0)/(0.25*u.truth_n+coalesce(h.recovered_n,0)) END oracle_f05
     FROM read_parquet('work/v2_research/research_truth_counts.parquet') u
     JOIN read_parquet('work/keys/train_s1.parquet') k ON k.entity_id=u.s1
     LEFT JOIN (SELECT s1,count(*) FILTER(WHERE recovered) recovered_n FROM diagnostics GROUP BY 1) h USING(s1)
     LEFT JOIN (SELECT source1_entity_id s1,count(*) candidates FROM candidates GROUP BY 1) n USING(s1)""")
    c.execute("COPY (SELECT * FROM entity ORDER BY s1) TO 'work/v2_research/v1_entity_metrics.parquet' (FORMAT PARQUET,COMPRESSION ZSTD)")
    values=c.sql("""SELECT count(*),sum(candidates),avg(candidates),median(candidates),quantile_cont(candidates,.90),quantile_cont(candidates,.95),quantile_cont(candidates,.99),max(candidates),
      sum(truth_n),sum(recovered_n),avg(oracle_f05),count(*) FILTER(WHERE truth_n>0 AND recovered_n=truth_n),
      count(*) FILTER(WHERE recovered_n>0 AND recovered_n<truth_n),count(*) FILTER(WHERE truth_n>0 AND recovered_n=0),count(*) FILTER(WHERE truth_n=0)
      FROM entity""").fetchone()
    metrics=dict(zip(['s1','candidates','mean','median','p90','p95','p99','max','gt_links','recovered_links','oracle_macro_f05','all_link_recovery_s1','some_link_recovery_s1','no_link_recovery_s1','singleton_s1'],values))
    metrics['pair_recall']=metrics['recovered_links']/metrics['gt_links']
    metrics['link_breakdowns']={}
    for column in ('country','target_source','both_address','script_relation','truth_n'):
        metrics['link_breakdowns'][column]=[dict(zip([column,'gt_links','recovered_links','pair_recall'],x)) for x in c.sql(f"SELECT {column},count(*),sum(recovered::INT),avg(recovered::INT) FROM diagnostics GROUP BY 1 ORDER BY 1").fetchall()]
    metrics['entity_country']=[dict(zip(['country','s1','oracle_macro_f05','mean_candidates','p95','p99','max'],x)) for x in c.sql('SELECT country,count(*),avg(oracle_f05),avg(candidates),quantile_cont(candidates,.95),quantile_cont(candidates,.99),max(candidates) FROM entity GROUP BY 1 ORDER BY 1').fetchall()]
    misses=[x for x in rows if not x['recovered']]
    categories=Counter(x['miss_category'] for x in misses)
    categories['other_verified_v1_retrieval_policy_loss']=0
    if sum(categories.values())!=len(misses): raise RuntimeError('Miss categories not exhaustive')
    signals=['exact_name','exact_nosuffix','exact_sorted','acronym_equal','initials_equal','postal_equal','house_equal','both_address','same_script','devanagari_latin','true_sister_retrieved','sister_closer_than_s1','cross_source_sister']
    signals += [f'name_{n}gram_overlap' for n in (2,3,4)] + [f'address_{n}gram_overlap' for n in (2,3,4)]
    signals += ['name_token_overlap','address_token_overlap','numeric_overlap','prefix_overlap','suffix_overlap']
    opportunity={key:{'missed_links_with_signal':sum(bool(row[key]) for row in misses),'denominator_missed_links':len(misses)} for key in signals}
    for key in ['name_ratio','address_ratio','sister_name_similarity','sister_address_similarity',*[f'name_{n}gram_jaccard' for n in (2,3,4)]]:
        opportunity[key]={str(t):sum(row[key]>=t for row in misses) for t in (.3,.5,.7,.8,.9)}|{'denominator_missed_links':len(misses)}
    entity_set={
        'research_entity_sets':len(research),'nonempty_entity_sets':len(byentity),
        'cross_source_entity_sets':sum(len({r['target_source'] for r in group})>1 for group in byentity.values()),
        'missed_links_sister_closer_than_s1':sum(row['sister_closer_than_s1'] for row in misses),
        'missed_links_cross_source_sister_name_at_least_08':sum(row['cross_source_sister'] and row['sister_name_similarity']>=.8 for row in misses),
        'mean_strongest_target_target_name_similarity':float(np.mean([x['strongest_target_target_name'] for x in rows])),
        'source_multiplicity':dict(Counter(','.join(f'{s}:{sum(r["target_source"]==s for r in group)}' for s in ('S2','S3')) for group in byentity.values())),
        'diagnostic_only':'Sister labels describe opportunity; no inference seeds may use these labels'}
    within_name=[];within_address=[];strongest_s1_name=[];strongest_target_name=[]
    closest_source_patterns=Counter()
    for group in byentity.values():
        strongest_s1_name.append(max((r['name_ratio'] for r in group),default=0.))
        strongest_target_name.append(max((r['strongest_target_target_name'] for r in group),default=0.))
        for left_index,left in enumerate(group):
            for right in group[left_index+1:]:
                within_name.append(ratio(left['tn'],right['tn'])/100)
                if left['ta'] and right['ta']:
                    within_address.append(ratio(left['ta'],right['ta'])/100)
            if not left['recovered']:
                closest_source_patterns[(left['target_source'],left['script_relation'],left['target_address_missing'])]+=1
    entity_set.update({
        'intra_set_target_name_pairs':len(within_name),
        'intra_set_mean_target_name_similarity':float(np.mean(within_name)) if within_name else None,
        'intra_set_target_address_pairs':len(within_address),
        'intra_set_mean_target_address_similarity':float(np.mean(within_address)) if within_address else None,
        'mean_strongest_s1_to_target_name_evidence':float(np.mean(strongest_s1_name)),
        'mean_strongest_target_to_target_name_evidence_per_set':float(np.mean(strongest_target_name)),
        'missed_link_source_script_address_patterns':[
            {'target_source':src,'script_relation':script,'target_address_missing':missing,'missed_links':n}
            for (src,script,missing),n in sorted(closest_source_patterns.items())],
    })
    write_json(R/'v1_metrics.json',metrics)
    write_json(R/'v1_miss_decomposition.json',{'missing_links':len(misses),'categories':dict(categories),
        'category_rule':'Same-country exact sorted/address or shared token with full target DF<=2000 => eligible; absent eligible pair lost at shortlist/rank/cap. Otherwise no eligible key.',
        'rank_evidence':'name_rank,address_rank,heavy_rank stored per research GT link; null token ranks indicate loss before evidence ranking',
        'diagnostics_sha256':sha(out)})
    write_json(R/'v1_opportunity.json',opportunity);write_json(R/'v1_entity_set_analysis.json',entity_set)
    log('research_forensics_complete',command='.venv\\Scripts\\python.exe -B scripts\\v2_diagnose.py',v2_research_label_read=True,
        scope='v2_candidate_research only',diagnostics_sha256=sha(out))
    print(json.dumps({'baseline':{k:v for k,v in metrics.items() if not isinstance(v,(dict,list))},'misses':dict(categories),'opportunity':opportunity,'entity_sets':entity_set},indent=2))


if __name__=='__main__':
    with Monitor(R/'tmp') as monitor: run()
    write_json(R/'diagnostic_resources.json',monitor.result())
