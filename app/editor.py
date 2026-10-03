from __future__ import annotations
import json, math, os, re, subprocess, tempfile
from pathlib import Path
from typing import List, Tuple
import httpx


def run(cmd: List[str]) -> str:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-6000:])
    return p.stdout


def probe_duration(path: str) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path])
    return float(json.loads(out)["format"]["duration"])


def detect_silence(path: str, noise: str = "-35dB", min_silence: float = 0.48) -> List[Tuple[float, float]]:
    cmd = ["ffmpeg", "-hide_banner", "-i", path, "-af", f"silencedetect=noise={noise}:d={min_silence}", "-f", "null", "-"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", p.stderr)]
    duration = probe_duration(path)
    silences = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        silences.append((s, e))
    return silences


def keep_intervals(duration: float, silences: List[Tuple[float, float]], pad: float = 0.08) -> List[Tuple[float, float]]:
    if not silences:
        return [(0.0, duration)]
    out = []
    cur = 0.0
    for s, e in silences:
        a = max(cur, 0.0)
        b = min(duration, s + pad)
        if b - a > 0.16:
            out.append((a, b))
        cur = max(cur, e - pad)
    if duration - cur > 0.16:
        out.append((cur, duration))

    merged = []
    for a, b in out:
        if merged and a - merged[-1][1] < 0.10:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged or [(0.0, duration)]


def cut_concat(src: str, dst: str, intervals: List[Tuple[float, float]]) -> None:
    if len(intervals) == 1 and intervals[0][0] <= 0.01 and abs(intervals[0][1] - probe_duration(src)) < 0.05:
        run(["ffmpeg", "-y", "-i", src, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", dst])
        return

    with tempfile.TemporaryDirectory() as td:
        parts = []
        for i, (a, b) in enumerate(intervals):
            part = os.path.join(td, f"p{i:04d}.mp4")
            run(["ffmpeg", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", src,
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
                 "-movflags", "+faststart", part])
            parts.append(part)
        listing = os.path.join(td, "list.txt")
        with open(listing, "w", encoding="utf-8") as f:
            for p in parts:
                f.write(f"file '{p}'\n")
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", dst])


def transcribe_groq(video_path: str, api_key: str) -> dict:
    with open(video_path, "rb") as f:
        files = {"file": (Path(video_path).name, f, "video/mp4")}
        data = {"model": "whisper-large-v3-turbo", "response_format": "verbose_json", "temperature": "0"}
        r = httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files=files,
            data=data,
            timeout=180,
        )
        r.raise_for_status()
        return r.json()


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def ass_time(t: float) -> str:
    cs = int(round(max(0, t) * 100))
    h = cs // 360000
    cs %= 360000
    m = cs // 6000
    cs %= 6000
    s = cs // 100
    cs %= 100
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _segments(transcript: dict) -> list[dict]:
    segs = transcript.get("segments") or []
    if segs:
        return segs
    text = (transcript.get("text") or "").strip()
    if not text:
        return []
    return [{"start": 0, "end": 9999, "text": text}]


def write_srt(transcript: dict, path: str) -> bool:
    segs = _segments(transcript)
    if not segs:
        return False
    with open(path, "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            txt = " ".join(str(s.get("text", "")).strip().split())
            if not txt:
                continue
            f.write(f"{i}\n{srt_time(float(s.get('start', 0)))} --> {srt_time(float(s.get('end', 0)) + 0.02)}\n{txt}\n\n")
    return True


def _ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _accent_keyword(text: str) -> str:
    words = text.split()
    if not words:
        return text
    clean = [re.sub(r"[^\wÀ-ÿ]", "", w) for w in words]
    idx = max(range(len(clean)), key=lambda i: len(clean[i]))
    if len(clean[idx]) < 5:
        return _ass_escape(text)
    out = []
    for i, word in enumerate(words):
        w = _ass_escape(word)
        if i == idx:
            out.append(r"{\c&H00F0A8&}" + w + r"{\c&HFFFFFF&}")
        else:
            out.append(w)
    return " ".join(out)


def write_creator_ass(transcript: dict, path: str, ratio: str = "9:16") -> bool:
    segs = _segments(transcript)
    if not segs:
        return False
    w, h = ratio_filter(ratio)
    play_w, play_h = int(w), int(h)
    font_size = 72 if ratio == "9:16" else (54 if ratio == "16:9" else 58)
    margin_v = 300 if ratio == "9:16" else (120 if ratio == "16:9" else 150)
    header = f"""[Script Info]\nScriptType: v4.00+\nPlayResX: {play_w}\nPlayResY: {play_h}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Creator,DejaVu Sans,{font_size},&H00FFFFFF,&H0000FFFF,&H00111111,&H66000000,-1,0,0,0,100,100,0,0,1,5,0,2,72,72,{margin_v},1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        for s in segs:
            start = float(s.get("start", 0))
            end = max(start + 0.25, float(s.get("end", start + 1)))
            text = " ".join(str(s.get("text", "")).strip().split())
            if not text:
                continue
            decorated = _accent_keyword(text)
            pop = r"{\fad(35,70)\fscx88\fscy88\t(0,110,\fscx108\fscy108)\t(110,210,\fscx100\fscy100)}"
            f.write(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Creator,,0,0,0,,{pop}{decorated}\n")
    return True


def ratio_filter(ratio: str) -> tuple[str, str]:
    if ratio == "1:1":
        return "1080", "1080"
    if ratio == "16:9":
        return "1920", "1080"
    return "1080", "1920"


def dynamic_profile(intensity: str) -> dict:
    profiles = {
        "soft": {"period": 4.0, "zoom": 0.045},
        "balanced": {"period": 2.7, "zoom": 0.085},
        "aggressive": {"period": 1.75, "zoom": 0.135},
    }
    return profiles.get(intensity, profiles["balanced"])


def _subtitle_filter(subtitle_path: str, caption_style: str) -> str:
    escaped = subtitle_path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    if subtitle_path.lower().endswith(".ass"):
        return f"subtitles='{escaped}'"
    if caption_style == "clean":
        force = "FontName=DejaVu Sans,FontSize=18,PrimaryColour=&H00FFFFFF,Outline=1,Shadow=0,Alignment=2,MarginV=95"
    else:
        force = "FontName=DejaVu Sans,FontSize=22,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=0,Alignment=2,MarginV=115"
    return f"subtitles='{escaped}':force_style='{force}'"


def render_final(
    src: str,
    dst: str,
    ratio: str = "9:16",
    subtitle_path: str | None = None,
    caption_style: str = "impact",
    music_path: str | None = None,
    dynamic_edit: bool = True,
    edit_intensity: str = "balanced",
) -> None:
    w, h = ratio_filter(ratio)

    filters = [
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=18:2[bg]",
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease[fg]",
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]",
    ]
    last = "base"

    if dynamic_edit:
        p = dynamic_profile(edit_intensity)
        fps = 30
        frames = max(24, int(p["period"] * fps))
        amp = p["zoom"]
        zexpr = f"1+{amp:.4f}*(mod(on,{frames})/{frames})"
        xexpr = "iw/2-(iw/zoom/2)"
        yexpr = "ih/2-(ih/zoom/2)"
        filters.append(
            f"[{last}]fps={fps},zoompan=z='{zexpr}':x='{xexpr}':y='{yexpr}':d=1:s={w}x{h}:fps={fps}[dyn]"
        )
        last = "dyn"

    if subtitle_path:
        filters.append(f"[{last}]{_subtitle_filter(subtitle_path, caption_style)}[subbed]")
        last = "subbed"

    audio_label = None
    if music_path:
        filters.append("[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice]")
        filters.append("[1:a]volume=0.11[music]")
        filters.append("[voice][music]amix=inputs=2:duration=first:dropout_transition=2[aout]")
        audio_label = "[aout]"

    cmd = ["ffmpeg", "-y", "-i", src]
    if music_path:
        cmd += ["-stream_loop", "-1", "-i", music_path]
    cmd += ["-filter_complex", ";".join(filters), "-map", f"[{last}]"]

    if audio_label:
        cmd += ["-map", audio_label]
    else:
        cmd += ["-map", "0:a?", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]

    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", dst,
    ]
    run(cmd)


def process_video(
    src: str,
    dst: str,
    ratio: str = "9:16",
    smart_cut: bool = True,
    captions: bool = True,
    caption_style: str = "creator",
    music_path: str | None = None,
    groq_key: str | None = None,
    dynamic_edit: bool = True,
    edit_intensity: str = "balanced",
) -> dict:
    work = str(Path(dst).with_name(Path(dst).stem + "_cut.mp4"))
    duration = probe_duration(src)
    intervals = [(0.0, duration)]

    if smart_cut:
        silences = detect_silence(src)
        intervals = keep_intervals(duration, silences)
        cut_concat(src, work, intervals)
    else:
        run(["ffmpeg", "-y", "-i", src, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", work])

    edited_duration = probe_duration(work)
    subtitle = None
    caption_status = "off"
    if captions and groq_key:
        try:
            data = transcribe_groq(work, groq_key)
            if caption_style == "creator":
                subtitle = str(Path(dst).with_suffix(".ass"))
                ok = write_creator_ass(data, subtitle, ratio)
            else:
                subtitle = str(Path(dst).with_suffix(".srt"))
                ok = write_srt(data, subtitle)
            if ok:
                caption_status = "generated"
            else:
                subtitle = None
                caption_status = "empty"
        except Exception as e:
            subtitle = None
            caption_status = f"failed: {type(e).__name__}"
    elif captions:
        caption_status = "needs_GROQ_API_KEY"

    render_final(
        work,
        dst,
        ratio,
        subtitle,
        caption_style,
        music_path,
        dynamic_edit=dynamic_edit,
        edit_intensity=edit_intensity,
    )

    try:
        os.remove(work)
    except OSError:
        pass

    rhythm = dynamic_profile(edit_intensity)
    dynamic_cuts = max(0, math.floor(edited_duration / rhythm["period"])) if dynamic_edit else 0
    return {
        "source_duration": round(duration, 2),
        "edited_duration": round(edited_duration, 2),
        "kept_segments": len(intervals),
        "dynamic_cuts": dynamic_cuts,
        "dynamic_edit": dynamic_edit,
        "edit_intensity": edit_intensity if dynamic_edit else "off",
        "captions": caption_status,
        "ratio": ratio,
    }
