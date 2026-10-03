from pathlib import Path
import httpx
from . import reference_editor as base

FONT="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

def commons_image(query,out_path):
    if not query: return False
    params={"action":"query","generator":"search","gsrsearch":"file:"+query,"gsrnamespace":"6","gsrlimit":"8","prop":"imageinfo","iiprop":"url|mime","format":"json","origin":"*"}
    try:
        r=httpx.get("https://commons.wikimedia.org/w/api.php",params=params,headers={"User-Agent":"AutoCutStudio/1.0"},timeout=18)
        r.raise_for_status()
        pages=(r.json().get("query") or {}).get("pages") or {}
        cand=[]
        for p in pages.values():
            info=(p.get("imageinfo") or [{}])[0]; mime=info.get("mime",""); url=info.get("url","")
            if mime in {"image/jpeg","image/png","image/webp"} and url: cand.append((0 if mime=="image/jpeg" else 1,url))
        for _,url in sorted(cand):
            img=httpx.get(url,headers={"User-Agent":"AutoCutStudio/1.0"},timeout=25,follow_redirects=True)
            if img.status_code==200 and 8000<len(img.content)<15000000:
                Path(out_path).write_bytes(img.content); return True
    except Exception: pass
    return False

def esc(s):
    return str(s).replace("\\","\\\\").replace("'","\\'").replace(":","\\:").replace("%","\\%").replace("[","\\[").replace("]","\\]")

def render(src,dst,events,workdir):
    dur=base.probe_duration(src); usable=[]; images=[]; broll=0
    for i,e0 in enumerate(events):
        e=dict(e0)
        if e["time"]>=dur-0.1: continue
        e["duration"]=min(e["duration"],max(0.4,dur-e["time"]))
        if e["type"]=="broll":
            p=str(workdir/f"broll_{i}.jpg")
            if commons_image(e.get("query",""),p):
                e["image"]=p; images.append(p); broll+=1
            else: e["type"]="callout"
        usable.append(e)
    if not usable:
        base.run(["ffmpeg","-y","-i",src,"-c","copy",dst]); return usable,broll

    cmd=["ffmpeg","-y","-i",src]
    for p in images: cmd += ["-loop","1","-i",p]
    filters=[]; last="[0:v]"; imgidx=1; step=0
    for e in usable:
        st=e["time"]; en=st+e["duration"]; typ=e["type"]; label=esc(e.get("label") or "DESTAQUE")
        if typ=="broll" and e.get("image"):
            tag=f"[img{step}]"; out=f"[v{step}]"
            filters.append(f"[{imgidx}:v]scale=720:720:force_original_aspect_ratio=decrease,pad=760:760:(ow-iw)/2:(oh-ih)/2:color=0x090b0c@0.94{tag}")
            filters.append(f"{last}{tag}overlay=(W-w)/2:150:enable='between(t,{st:.2f},{en:.2f})'{out}")
            last=out; imgidx+=1; step+=1
        elif typ=="product":
            out=f"[v{step}]"; filters.append(f"{last}drawbox=x=iw*0.12:y=ih*0.17:w=iw*0.76:h=ih*0.63:color=0x75f6ad@0.82:t=7:enable='between(t,{st:.2f},{en:.2f})'{out}"); last=out; step+=1
            out=f"[v{step}]"; filters.append(f"{last}drawtext=fontfile='{FONT}':text='{label}':fontsize=52:fontcolor=white:box=1:boxcolor=0x0b1510@0.86:boxborderw=16:x=(w-text_w)/2:y=h*0.12:enable='between(t,{st:.2f},{en:.2f})'{out}"); last=out; step+=1
        elif typ=="arrow":
            out=f"[v{step}]"; filters.append(f"{last}drawtext=fontfile='{FONT}':text='↓':fontsize=150:fontcolor=0xffe34d:borderw=4:bordercolor=black:x=(w-text_w)/2:y=h*0.22:enable='between(t,{st:.2f},{en:.2f})'{out}"); last=out; step+=1
        else:
            out=f"[v{step}]"; y="h*0.13" if typ=="print" else "h*0.20"; fs=50 if typ=="print" else 46
            filters.append(f"{last}drawtext=fontfile='{FONT}':text='{label}':fontsize={fs}:fontcolor=white:box=1:boxcolor=0x0b1510@0.88:boxborderw=18:x=(w-text_w)/2:y={y}:enable='between(t,{st:.2f},{en:.2f})'{out}"); last=out; step+=1

    audio=base.has_audio(src)
    cmd += ["-filter_complex",";".join(filters),"-map",last]
    if audio: cmd += ["-map","0:a:0","-c:a","aac","-b:a","160k"]
    else: cmd += ["-an"]
    cmd += ["-c:v","libx264","-preset","veryfast","-crf","19","-pix_fmt","yuv420p","-t",f"{dur:.3f}","-movflags","+faststart",dst]
    base.run(cmd)
    return usable,broll
