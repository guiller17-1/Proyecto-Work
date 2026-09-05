import csv, os, tempfile, time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import requests
from flask import Flask, jsonify, render_template
app=Flask(__name__)
CSV_URL=os.getenv("CSV_URL","")
CACHE_SECONDS=int(os.getenv("CACHE_SECONDS","600"))
cache={"at":0,"data":None}
def txt(v): return "" if v is None else str(v).strip()
def dt(v):
    for f in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M"):
        try:return datetime.strptime(txt(v),f)
        except ValueError:pass
    return None
def col(headers,*names):
    m={x.strip().lower():x for x in headers}
    return next((m[n.lower()] for n in names if n.lower() in m),None)
def get_file():
    if not CSV_URL: raise ValueError("Falta CSV_URL en Render")
    r=requests.get(CSV_URL,stream=True,timeout=60);r.raise_for_status()
    f=tempfile.NamedTemporaryFile(delete=False,suffix='.csv')
    for b in r.iter_content(65536):
        if b:f.write(b)
    f.close();return f.name
def reader(path):
    try:
        f=open(path,encoding='utf-8-sig',newline='');f.read(2048);f.seek(0);return f
    except UnicodeDecodeError:
        f.close();return open(path,encoding='latin-1',newline='')
def ctype(row,c,ig,it):
    if c and txt(row.get(c)):return txt(row.get(c))
    s=' '.join([txt(row.get(ig)) if ig else '',txt(row.get(it)) if it else '']).lower()
    if any(x in s for x in ('reefer','refrigerated','heated','self-powered')):return 'Reefer'
    if any(x in s for x in ('tank','tanque')):return 'Tanque'
    return 'Dry'
def build():
    path=get_file()
    try:
        with reader(path) as f:
            rd=csv.DictReader(f);h=rd.fieldnames or []
            C={'start':col(h,'Start Date'),'handled':col(h,'Handled'),'license':col(h,'Truck Visit Truck License'),'status':col(h,'Status'),'trans':col(h,'Transaction Type'),'freight':col(h,'Unit Frght Kind'),'sede':col(h,'SEDE','Stow'),'gate':col(h,'Truck Visit Gate'),'booking':col(h,'Booking Number'),'line':col(h,'Line Id'),'stow':col(h,'Stow'),'ig':col(h,'ISO Group (order)'),'it':col(h,'ISO Type','CÓDIGO ISO','CODIGO ISO'),'ct':col(h,'CONTAINER TYPE')}
            missing=[k for k in ('start','handled','license','status','trans','freight') if not C[k]]
            if missing:raise ValueError('Faltan columnas: '+', '.join(missing))
            dup=Counter();source=0
            for r in rd:
                s=dt(r.get(C['start']))
                if s:dup[(txt(r.get(C['status'])),txt(r.get(C['license'])),s.isoformat())]+=1;source+=1
        targets={'Deliver EmptyDry':45,'Deliver ImportDry':45,'Dray OffDry':35,'Dray InDry':35,'Receive EmptyDry':45,'Receive ExportDry':35,'Deliver EmptyReefer':90,'Deliver ImportReefer':45,'Dray OffReefer':35,'Dray InReefer':35,'Receive EmptyReefer':45,'Receive ExportReefer':35}
        G=defaultdict(lambda:{'count':0,'sumTime':0,'targetCount':0,'sumTarget':0,'sumCompliance':0,'duplicates':0});now=datetime.now()
        with reader(path) as f:
            for r in csv.DictReader(f):
                s=dt(r.get(C['start']))
                if not s:continue
                hand=dt(r.get(C['handled'])) or now;lic=txt(r.get(C['license']));status=txt(r.get(C['status']));sede=txt(r.get(C['sede'])) if C['sede'] else 'Sin sede';tr=txt(r.get(C['trans']));fr=txt(r.get(C['freight']))
                if tr=='Receive Export' and fr=='Empty':tr='Receive Empty'
                elif tr=='Deliver Import' and fr=='Empty':tr='Deliver Empty'
                d=dup[(status,lic,s.isoformat())]>1;typ=ctype(r,C['ct'],C['ig'],C['it']);target=targets.get(tr+typ);mins=round((hand-s).total_seconds()/60,1)
                if tr in ('Deliver Empty','Receive Empty') and d:mins=round(mins/2,1)
                gate=txt(r.get(C['gate'])) if C['gate'] else '';book=txt(r.get(C['booking'])) if C['booking'] else '';stow=txt(r.get(C['stow'])) if C['stow'] else '';line=txt(r.get(C['line'])) if C['line'] else ''
                consider=not(gate in {'ARR_ARGGATE','DAS_ARGGATE','ARR_VNTGATE'} or book.startswith(('DAS','ARR')) or stow.startswith('AR') or typ=='Tanque' or mins<=10 or line in {'DPW','NEP','NPT'} or target is None)
                shift='DÍA' if 6<s.hour<19 else 'NOCHE';date=(s-timedelta(days=1)).date() if s.hour<7 else s.date();key=(lic,sede,status,'CONSIDERAR' if consider else 'NO CONSIDERAR',shift,tr,typ,date.isoformat());g=G[key];g['count']+=1;g['sumTime']+=mins
                if target is not None:g['targetCount']+=1;g['sumTarget']+=target;g['sumCompliance']+=int(mins<=target)
                if d:g['duplicates']+=1
        rec=[]
        for k,g in G.items():
            lic,sede,status,consider,shift,tr,typ,date=k;rec.append({'license':lic,'sede':sede,'status':status,'considerar':consider,'turno':shift,'transaccion':tr,'containerType':typ,'fecha':date,**g})
        return {'records':rec,'sourceRows':source,'generatedAt':datetime.now().astimezone().isoformat(timespec='seconds')}
    finally:
        try:os.remove(path)
        except:pass
def payload():
    if cache['data'] is not None and time.time()-cache['at']<CACHE_SECONDS:return cache['data']
    cache['data']=build();cache['at']=time.time();return cache['data']
@app.get('/')
def home():return render_template('index.html')
@app.get('/health')
def health():return jsonify(status='ok')
@app.get('/api/data')
def data():
    try:return jsonify(payload())
    except Exception as e:app.logger.exception('CSV error');return jsonify(error=str(e)),500
