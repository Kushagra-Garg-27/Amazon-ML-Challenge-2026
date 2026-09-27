"""Prepare bounded query joins against existing complete-target DF metadata."""
import argparse,json
from common import ROOT,OUT,sha,write_new,execute_gate,research_connection,literal

def grams_sql(column,n):
    if column!='text_value' or n not in (3,4,5):raise ValueError('Unreviewed ngram arguments')
    return f'unnest(list_distinct(list_transform(range(1,length({column})-{n}+2), i -> substr({column},i,{n}))))'

def run(args):
    df=ROOT/args.df;receipt=json.loads((ROOT/args.receipt).read_text())
    if receipt.get('sha256')!=sha(df) or receipt.get('status')!='FULL_TARGET_DF':
        raise PermissionError('V3 complete-target DF receipt required; never use a research-GT index')
    if receipt.get('labels_used') is not False or receipt.get('field')!=args.field or receipt.get('n')!=args.n:
        raise PermissionError('DF provenance/configuration differs')
    con,s1_count=research_connection('ngram_preflight')
    field='name_norm' if args.field=='name' else 'addr_norm'
    con.execute(f"""CREATE TEMP VIEW queries AS SELECT k.entity_id s1,k.country_norm cc,k.{field} text_value
      FROM read_parquet({literal(ROOT/'work/keys/train_s1.parquet')}) k
      JOIN authorized_development a ON k.entity_id=a.s1""")
    expr=grams_sql('text_value',args.n);rows=[]
    for cap in (100,500,1000,5000):
      for keys in (4,8):
        selected=f"""SELECT * FROM (SELECT q.s1,q.cc,q.gram,d.df,
          row_number() OVER(PARTITION BY s1 ORDER BY df,gram) rk
          FROM (SELECT s1,cc,{expr} gram FROM queries WHERE length(text_value) BETWEEN {args.n} AND {args.max_chars}) q
          JOIN read_parquet({literal(df)}) d USING(cc,gram) WHERE d.df<={cap}) WHERE rk<={keys}"""
        con.execute('CREATE OR REPLACE TEMP TABLE selected AS '+selected)
        joins,worst,entities=con.sql('''SELECT coalesce(sum(n),0),coalesce(max(n),0),count(*) FROM
          (SELECT s1,sum(df) n FROM selected GROUP BY 1)''').fetchone()
        rows.append({'df_cap':cap,'keys':keys,'estimated_join_rows':joins,'worst_query_join_rows':worst,
          's1_with_eligible_key':entities,'unique_pair_upper_bound':joins,
          'quota_pair_upper_bound':entities*2*args.source_quota,
          'status':'PREFLIGHT_ONLY','within_join_budget':joins<=100000000})
    name=f'ngram_{args.field}{args.n}_preflight.json'
    write_new(OUT/name,{'status':'PREFLIGHT_ONLY','development_s1':s1_count,'rows':rows,
      'df_sha256':sha(df),'field':args.field,'n':args.n,'max_chars':args.max_chars,
      'materialization_partition_count':16,'pair_payload_bytes_per_row_lower_bound':24,
      'peak_RAM_budget_bytes':1800000000,'temp_disk_budget_bytes':20000000000,
      'runtime':'measure one deterministic shard before whole-run approval',
      'test_projection':'not extrapolated from GT misses; country-specific test preflight required after freeze',
      'labels_read':False,'assessment_labels_read':False})
    con.close();print(OUT/name,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true')
    p.add_argument('--field',choices=['name','address'],required=True);p.add_argument('--n',type=int,choices=[3,4,5],required=True)
    p.add_argument('--df',required=True);p.add_argument('--receipt',required=True)
    p.add_argument('--max-chars',type=int,default=96);p.add_argument('--source-quota',type=int,default=25)
    args=p.parse_args();execute_gate(args);run(args)
