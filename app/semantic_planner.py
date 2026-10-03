import json, os, re
import httpx

CATS=[
(["dinheiro","preço","valor","venda","lucro","faturamento","pix"],"money business","broll"),
(["instagram","facebook","tiktok","rede social","celular"],"smartphone social media","broll"),
(["academia","treino","fitness","musculação"],"fitness gym training","broll"),
(["comida","restaurante","lanche","pizza","hamburguer","padaria"],"restaurant food","broll"),
(["casa","imóvel","imovel","apartamento","corretor"],"modern house real estate","broll"),
(["carro","veículo","veiculo"],"modern car","broll"),
(["computador","software","aplicativo","app","sistema","site"],"laptop software application","broll"),
(["câmera","camera","foto","vídeo","video","filmagem"],"camera video production","broll"),
(["marketing","anúncio","anuncio","tráfego","trafego"],"digital marketing advertising","broll"),
(["cliente","compra","produto","produtos"],"customer product shopping","product"),
(["atenção","olha","veja","importante","segredo"],"attention","arrow"),
(["resultado","resultados","número","numero","porcento","%"],"results growth chart","print"),
]

def clean_json(s):
    s=s.strip()
    s=re.sub(r"^\\x60\\x60\\x60(?:json)?","",s,flags=re.I).strip()
    s=re.sub(r"\\x60\\x60\\x60$","",s).strip()
    a=s.find("{"); b=s.rfind("}")
    return s[a:b+1] if a>=0 and b>a else s

def sanitize(events):
    out=[]; last=-99.0
    for e in events[:14]:
        try:
            t=max(0.0,float(e.get("time",0)))
            if t-last<2.8: continue
            typ=str(e.get("type","callout")).lower()
            if typ not in {"broll","product","arrow","print","callout"}: typ="callout"
            dur=min(2.4,max(0.65,float(e.get("duration",1.2))))
            q=re.sub(r"[^a-zA-Z0-9 \\-]"," ",str(e.get("query","")))[:60].strip()
            label=" ".join(str(e.get("label","DESTAQUE")).split())[:32]
            out.append({"time":round(t,2),"duration":round(dur,2),"type":typ,"query":q,"label":label})
            last=t
        except Exception: pass
    return out

def groq_plan(transcript,key):
    segs=[{"start":round(float(s.get("start",0)),2),"end":round(float(s.get("end",0)),2),"text":str(s.get("text","")).strip()} for s in transcript.get("segments",[])]
    prompt="Você é um editor de Shorts de alta retenção. Analise a transcrição e crie eventos visuais. Tipos: broll, product, arrow, print, callout. Use no máximo 1 evento a cada 4 segundos. Para broll, query em inglês com 1 a 4 palavras e sem marcas. Não invente fatos ou números. Retorne APENAS JSON no formato {\\\"events\\\":[{\\\"time\\\":1.2,\\\"duration\\\":1.5,\\\"type\\\":\\\"broll\\\",\\\"query\\\":\\\"coffee shop\\\",\\\"label\\\":\\\"CAFÉ\\\"}]}. Transcrição: "+json.dumps(segs,ensure_ascii=False)
    payload={"model":os.getenv("GROQ_TEXT_MODEL","openai/gpt-oss-20b"),"messages":[{"role":"user","content":prompt}],"temperature":0.15,"response_format":{"type":"json_object"},"max_tokens":1800}
    r=httpx.post("https://api.groq.com/openai/v1/chat/completions",headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"},json=payload,timeout=90)
    r.raise_for_status()
    raw=r.json()["choices"][0]["message"]["content"]
    return sanitize(json.loads(clean_json(raw)).get("events",[]))

def fallback_plan(transcript):
    out=[]; last=-9.0; seen=set()
    for s in transcript.get("segments",[]):
        txt=" ".join(str(s.get("text","")).lower().split()); st=float(s.get("start",0))
        if st-last<4.2: continue
        match=None
        for kws,q,typ in CATS:
            if any(k in txt for k in kws): match=(q,typ); break
        if not match: continue
        q,typ=match
        if (q,typ) in seen and typ=="broll": continue
        words=[w.strip(".,!?;:") for w in txt.split() if len(w.strip(".,!?;:"))>=5]
        label=(max(words,key=len).upper()[:24] if words else "DESTAQUE")
        out.append({"time":round(st+0.15,2),"duration":1.45 if typ=="broll" else 1.05,"type":typ,"query":q,"label":label})
        seen.add((q,typ)); last=st
    return sanitize(out)

def make_plan(transcript,key=None):
    if key:
        try:
            x=groq_plan(transcript,key)
            if x: return x,"groq-semantic"
        except Exception: pass
    return fallback_plan(transcript),"local-semantic"
