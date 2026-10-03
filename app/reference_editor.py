from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple

import httpx

_MODEL = None

STOPWORDS = {
    "para","como","mais","essa","esse","isso","aqui","porque","quando","onde","uma","umas","uns",
    "que","com","sem","por","dos","das","nos","nas","você","vocês","voce","seu","sua","seus","suas",
    "the","and","with","this","that","from","your","you","are","was","for"
}


def run(cmd: List[str]) -> str:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-7000:])
    return p.stdout


def probe_duration(path: str) -> float:
    raw = run(["ffprobe","-v","error","-show_entries","format=duration","-of","json",path])
    return float(json.loads(raw)["format"]["duration"])


def has_audio(path: str) -> bool:
    p = subprocess.run(
        ["ffprobe","-v","error","-select_streams","a:0","-show_entries","stream=index","-of","csv=p=0",path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    return bool(p.stdout.strip())


def ratio_size(ratio: str) -> tuple[int,int]:
    if ratio == "1:1":
        return 1080,1080
    if ratio == "16:9":
        return 1920,1080
    return 1080,1920


def detect_silence(path: str, noise: str="-34dB", min_silence: float=0.42) -> List[Tuple[float,float]]:
    p = subprocess.run(
        ["ffmpeg","-hide_banner","-i",path,"-af",f"silencedetect=noise={noise}:d={min_silence}","-f","null","-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    starts=[float(x) for x in re.findall(r"silence_start: ([0-9.]+)",p.stderr)]
    ends=[float(x) for x in re.findall(r"silence_end: ([0-9.]+)",p.stderr)]
    dur=probe_duration(path)
    out=[]
    for i,s in enumerate(starts):
        out.append((s, ends[i] if i < len(ends) else dur))
    return out


def keep_intervals(duration: float, silences: List[Tuple[float,float]], pad: float=0.07) -> List[Tuple[float,float]]:
    if not silences:
        return [(0.0,duration)]
    out=[]
    cur=0.0
    for s,e in silences:
        b=min(duration,s+pad)
        if b-cur > 0.16:
            out.append((cur,b))
        cur=max(cur,e-pad)
    if duration-cur > 0.16:
        out.append((cur,duration))
    merged=[]
    for a,b in out:
        if merged and a-merged[-1][1] < 0.09:
            merged[-1]=(merged[-1][0],b)
        else:
            merged.append((a,b))
    return merged or [(0.0,duration)]


def smart_cut(src: str, dst: str, enabled: bool=True) -> tuple[int,float]:
    dur=probe_duration(src)
    intervals=keep_intervals(dur,detect_silence(src)) if enabled else [(0.0,dur)]
    if len(intervals)==1 and intervals[0][0] < 0.01 and abs(intervals[0][1]-dur) < 0.04:
        run(["ffmpeg","-y","-i",src,"-c:v","libx264","-preset","veryfast","-crf","20","-c:a","aac","-b:a","160k","-movflags","+faststart",dst])
        return 1,dur

    audio=has_audio(src)
    with tempfile.TemporaryDirectory() as td:
        parts=[]
        for i,(a,b) in enumerate(intervals):
            p=os.path.join(td,f"p{i:03d}.mp4")
            cmd=["ffmpeg","-y","-ss",f"{a:.3f}","-to",f"{b:.3f}","-i",src,"-c:v","libx264","-preset","veryfast","-crf","20"]
            if audio:
                cmd += ["-c:a","aac","-b:a","160k"]
            else:
                cmd += ["-an"]
            cmd += ["-movflags","+faststart",p]
            run(cmd)
            parts.append(p)
        listing=os.path.join(td,"list.txt")
        with open(listing,"w",encoding="utf-8") as f:
            for p in parts:
                f.write(f"file '{p}'\n")
        run(["ffmpeg","-y","-f","concat","-safe","0","-i",listing,"-c","copy",dst])
    return len(intervals),probe_duration(dst)


def normalize_frame(src: str, dst: str, ratio: str) -> None:
    w,h=ratio_size(ratio)
    audio=has_audio(src)
    fc=(
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=22:2[bg2];"
        f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    )
    cmd=["ffmpeg","-y","-i",src,"-filter_complex",fc,"-map","[v]"]
    if audio:
        cmd += ["-map","0:a?","-c:a","aac","-b:a","160k"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v","libx264","-preset","veryfast","-crf","19","-pix_fmt","yuv420p","-movflags","+faststart",dst]
    run(cmd)


def rhythm_profile(intensity: str) -> dict:
    if intensity=="soft":
        return {"periods":[2.2,2.6,2.0,2.8,2.35],"zooms":[1.00,1.045,1.00,1.06,1.02]}
    if intensity=="aggressive":
        return {"periods":[0.95,1.15,0.85,1.30,1.05,1.20],"zooms":[1.00,1.10,1.03,1.14,1.00,1.08]}
    return {"periods":[1.20,1.55,1.10,1.80,1.30,1.50],"zooms":[1.00,1.075,1.02,1.11,1.00,1.065]}


def jump_edit(src: str, dst: str, ratio: str, intensity: str) -> int:
    w,h=ratio_size(ratio)
    dur=probe_duration(src)
    profile=rhythm_profile(intensity)
    periods=profile["periods"]
    zooms=profile["zooms"]
    cuts=[]
    t=0.0
    i=0
    while t < dur-0.08:
        e=min(dur,t+periods[i % len(periods)])
        cuts.append((t,e,zooms[i % len(zooms)],i))
        t=e
        i+=1

    audio=has_audio(src)
    fs=[]
    vin=[]
    ain=[]
    xposes=[0.50,0.47,0.53,0.50,0.485,0.515]
    yposes=[0.50,0.48,0.51,0.49,0.50,0.50]
    for idx,(a,b,z,n) in enumerate(cuts):
        x=xposes[n % len(xposes)]
        y=yposes[n % len(yposes)]
        fs.append(
            f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,"
            f"scale=ceil(iw*{z:.4f}/2)*2:ceil(ih*{z:.4f}/2)*2,"
            f"crop={w}:{h}:(iw-{w})*{x:.3f}:(ih-{h})*{y:.3f}[v{idx}]"
        )
        vin.append(f"[v{idx}]")
        if audio:
            fs.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{idx}]")
            ain.append(f"[a{idx}]")
    if audio:
        fs.append("".join(vin+ain)+f"concat=n={len(cuts)}:v=1:a=1[vout][aout]")
    else:
        fs.append("".join(vin)+f"concat=n={len(cuts)}:v=1:a=0[vout]")

    cmd=["ffmpeg","-y","-i",src,"-filter_complex",";".join(fs),"-map","[vout]"]
    if audio:
        cmd += ["-map","[aout]","-c:a","aac","-b:a","160k"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v","libx264","-preset","veryfast","-crf","19","-pix_fmt","yuv420p","-movflags","+faststart",dst]
    run(cmd)
    return max(0,len(cuts)-1)


def transcribe_groq(path: str, key: str) -> dict:
    with open(path,"rb") as f:
        r=httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization":f"Bearer {key}"},
            files={"file":(Path(path).name,f,"video/mp4")},
            data={"model":"whisper-large-v3-turbo","response_format":"verbose_json","temperature":"0"},
            timeout=180,
        )
    r.raise_for_status()
    data=r.json()
    return {"engine":"groq","segments":data.get("segments") or [],"text":data.get("text","")}


def transcribe_local(path: str) -> dict:
    global _MODEL
    from faster_whisper import WhisperModel
    if _MODEL is None:
        model_name=os.getenv("WHISPER_MODEL","base")
        _MODEL=WhisperModel(model_name,device="cpu",compute_type="int8")
    segments,info=_MODEL.transcribe(path,beam_size=1,vad_filter=True,word_timestamps=True)
    out=[]
    words=[]
    for s in segments:
        seg={"start":float(s.start),"end":float(s.end),"text":s.text.strip()}
        out.append(seg)
        for w in (s.words or []):
            words.append({"start":float(w.start or s.start),"end":float(w.end or s.end),"word":str(w.word).strip()})
    return {"engine":"local","segments":out,"words":words,"language":getattr(info,"language",None)}


def transcribe(path: str, groq_key: str | None) -> dict:
    if groq_key:
        try:
            return transcribe_groq(path,groq_key)
        except Exception:
            pass
    return transcribe_local(path)


def words_from_transcript(t: dict) -> list[dict]:
    if t.get("words"):
        return [w for w in t["words"] if w.get("word")]
    words=[]
    for s in t.get("segments") or []:
        text=" ".join(str(s.get("text","")).split())
        ws=text.split()
        if not ws:
            continue
        a=float(s.get("start",0))
        b=max(a+0.2,float(s.get("end",a+0.2)))
        step=(b-a)/len(ws)
        for i,w in enumerate(ws):
            words.append({"start":a+i*step,"end":a+(i+1)*step,"word":w})
    return words


def ass_time(t: float) -> str:
    cs=int(round(max(0,t)*100))
    h=cs//360000
    cs%=360000
    m=cs//6000
    cs%=6000
    s=cs//100
    cs%=100
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def clean_word(w: str) -> str:
    return re.sub(r"[^\wÀ-ÿ'-]","",w).strip()


def ass_escape(s: str) -> str:
    return s.replace("\\",r"\\").replace("{",r"\{").replace("}",r"\}")


def choose_keyword(words: list[str]) -> int:
    candidates=[]
    for i,w in enumerate(words):
        c=clean_word(w).lower()
        if c and c not in STOPWORDS:
            candidates.append((len(c),i))
    return max(candidates)[1] if candidates else max(range(len(words)),key=lambda i:len(clean_word(words[i])))


def write_reference_ass(transcript: dict, path: str, ratio: str, callouts: bool=True) -> tuple[int,int]:
    words=words_from_transcript(transcript)
    if not words:
        return 0,0
    w,h=ratio_size(ratio)
    font=72 if ratio=="9:16" else (52 if ratio=="16:9" else 58)
    margin=270 if ratio=="9:16" else (95 if ratio=="16:9" else 125)
    header=f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DejaVu Sans,{font},&H00FFFFFF,&H0000E6FF,&H00101010,&H50000000,-1,0,0,0,100,100,0,0,1,5,0,2,70,70,{margin},1
Style: Callout,DejaVu Sans,{max(34,int(font*0.58))},&H00FFFFFF,&H00FFFFFF,&H00101010,&H720B1711,-1,0,0,0,100,100,0,0,3,2,0,8,90,90,{90 if ratio=='9:16' else 45},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    chunks=[]
    cur=[]
    for item in words:
        if not cur:
            cur=[item]
            continue
        span=float(item["end"])-float(cur[0]["start"])
        if len(cur)>=3 or span>1.05:
            chunks.append(cur)
            cur=[item]
        else:
            cur.append(item)
    if cur:
        chunks.append(cur)

    caption_events=0
    callout_events=0
    next_callout=0.7
    with open(path,"w",encoding="utf-8") as f:
        f.write(header)
        for chunk in chunks:
            ws=[str(x["word"]) for x in chunk]
            idx=choose_keyword(ws)
            decorated=[]
            for i,word in enumerate(ws):
                ew=ass_escape(word)
                if i==idx:
                    decorated.append(r"{\c&H0000E6FF&}"+ew+r"{\c&H00FFFFFF&}")
                else:
                    decorated.append(ew)
            text=" ".join(decorated)
            start=float(chunk[0]["start"])
            end=max(start+0.30,float(chunk[-1]["end"])+0.04)
            pop=r"{\fad(20,55)\fscx84\fscy84\t(0,90,\fscx108\fscy108)\t(90,165,\fscx100\fscy100)}"
            f.write(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{pop}{text}\n")
            caption_events+=1

            if callouts and start>=next_callout:
                raw=clean_word(ws[idx])
                if len(raw)>=6:
                    cstart=start+0.06
                    cend=min(end+0.35,cstart+1.05)
                    motion=r"{\fad(80,110)\fscx92\fscy92\t(0,120,\fscx103\fscy103)\t(120,220,\fscx100\fscy100)}"
                    f.write(f"Dialogue: 1,{ass_time(cstart)},{ass_time(cend)},Callout,,0,0,0,,{motion}◆ {ass_escape(raw.upper())}\n")
                    callout_events+=1
                    next_callout=start+6.3
    return caption_events,callout_events


def final_render(src: str, dst: str, ass_path: str | None, music_path: str | None) -> None:
    audio=has_audio(src)
    vf=None
    if ass_path:
        esc=ass_path.replace("\\","/").replace(":","\\:").replace("'","\\'")
        vf=f"subtitles='{esc}'"

    cmd=["ffmpeg","-y","-i",src]
    if music_path:
        cmd += ["-stream_loop","-1","-i",music_path]
    if vf:
        cmd += ["-vf",vf]

    if music_path and audio:
        cmd += ["-filter_complex","[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice];[1:a]volume=0.10[music];[voice][music]amix=inputs=2:duration=first:dropout_transition=2[aout]","-map","0:v:0","-map","[aout]"]
    elif audio:
        cmd += ["-map","0:v:0","-map","0:a:0","-af","loudnorm=I=-16:TP=-1.5:LRA=11"]
    elif music_path:
        cmd += ["-map","0:v:0","-map","1:a:0"]
    else:
        cmd += ["-map","0:v:0","-an"]

    cmd += ["-c:v","libx264","-preset","veryfast","-crf","19","-pix_fmt","yuv420p"]
    if music_path or audio:
        cmd += ["-c:a","aac","-b:a","192k"]
    cmd += ["-movflags","+faststart","-shortest",dst]
    run(cmd)


def process_reference_video(
    src: str,
    dst: str,
    ratio: str="9:16",
    smart_cut: bool=True,
    captions: bool=True,
    callouts: bool=True,
    intensity: str="reference",
    music_path: str | None=None,
    groq_key: str | None=None,
) -> dict:
    parent=Path(dst).parent
    stem=Path(dst).stem
    cut_path=str(parent/f"{stem}_cut.mp4")
    frame_path=str(parent/f"{stem}_frame.mp4")
    jump_path=str(parent/f"{stem}_jump.mp4")
    ass_path=str(parent/f"{stem}.ass")

    source_duration=probe_duration(src)
    speech_segments,cut_duration=smart_cut_fn(src,cut_path,smart_cut)
    normalize_frame(cut_path,frame_path,ratio)
    jump_cuts=jump_edit(frame_path,jump_path,ratio,intensity)

    transcription_engine="off"
    caption_events=0
    callout_events=0
    subtitle=None
    if captions:
        t=transcribe(jump_path,groq_key)
        transcription_engine=t.get("engine","unknown")
        caption_events,callout_events=write_reference_ass(t,ass_path,ratio,callouts)
        if caption_events:
            subtitle=ass_path

    final_render(jump_path,dst,subtitle,music_path)
    edited_duration=probe_duration(dst)

    for p in [cut_path,frame_path,jump_path]:
        try: Path(p).unlink(missing_ok=True)
        except Exception: pass

    return {
        "source_duration":round(source_duration,2),
        "edited_duration":round(edited_duration,2),
        "speech_segments":speech_segments,
        "jump_cuts":jump_cuts,
        "caption_events":caption_events,
        "callout_events":callout_events,
        "transcription_engine":transcription_engine,
        "ratio":ratio,
        "intensity":intensity,
    }


smart_cut_fn = smart_cut
