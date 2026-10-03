from __future__ import annotations

from . import reference_editor as base


def jump_edit(src: str, dst: str, ratio: str, intensity: str) -> int:
    w,h=base.ratio_size(ratio)
    dur=base.probe_duration(src)
    profile=base.rhythm_profile(intensity)
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

    audio=base.has_audio(src)
    fs=[]
    concat_inputs=[]
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
        concat_inputs.append(f"[v{idx}]")
        if audio:
            fs.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{idx}]")
            concat_inputs.append(f"[a{idx}]")

    if audio:
        fs.append("".join(concat_inputs)+f"concat=n={len(cuts)}:v=1:a=1[vout][aout]")
    else:
        fs.append("".join(concat_inputs)+f"concat=n={len(cuts)}:v=1:a=0[vout]")

    cmd=["ffmpeg","-y","-i",src,"-filter_complex",";".join(fs),"-map","[vout]"]
    if audio:
        cmd += ["-map","[aout]","-c:a","aac","-b:a","160k"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v","libx264","-preset","veryfast","-crf","19","-pix_fmt","yuv420p","-movflags","+faststart",dst]
    base.run(cmd)
    return max(0,len(cuts)-1)


base.jump_edit=jump_edit
process_reference_video=base.process_reference_video
