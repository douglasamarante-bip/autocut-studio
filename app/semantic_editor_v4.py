from pathlib import Path
from . import reference_editor as base
from .reference_editor_patch import jump_edit
from .semantic_planner import make_plan
from .semantic_renderer import render

def process_semantic_video(src,dst,ratio="9:16",smart_cut=True,captions=True,callouts=True,intensity="reference",music_path=None,groq_key=None,semantic=True):
    parent=Path(dst).parent; stem=Path(dst).stem
    cut=str(parent/f"{stem}_cut.mp4"); frame=str(parent/f"{stem}_frame.mp4")
    jump=str(parent/f"{stem}_jump.mp4"); visual=str(parent/f"{stem}_visual.mp4")
    ass=str(parent/f"{stem}.ass")

    source_duration=base.probe_duration(src)
    speech_segments,_=base.smart_cut_fn(src,cut,smart_cut)
    base.normalize_frame(cut,frame,ratio)
    jump_cuts=jump_edit(frame,jump,ratio,intensity)

    transcript=base.transcribe(jump,groq_key)
    transcription_engine=transcript.get("engine","unknown")
    events=[]; semantic_engine="off"; broll_count=0
    source_for_final=jump
    if semantic:
        events,semantic_engine=make_plan(transcript,groq_key)
        events,broll_count=render(jump,visual,events,parent)
        source_for_final=visual

    caption_events=0; callout_events=0; subtitle=None
    if captions:
        caption_events,callout_events=base.write_reference_ass(transcript,ass,ratio,callouts)
        if caption_events: subtitle=ass

    base.final_render(source_for_final,dst,subtitle,music_path)
    edited_duration=base.probe_duration(dst)
    for p in [cut,frame,jump,visual]:
        try: Path(p).unlink(missing_ok=True)
        except Exception: pass

    return {
        "source_duration":round(source_duration,2),"edited_duration":round(edited_duration,2),
        "speech_segments":speech_segments,"jump_cuts":jump_cuts,
        "caption_events":caption_events,"callout_events":callout_events,
        "semantic_events":len(events),"broll_count":broll_count,
        "transcription_engine":transcription_engine,"semantic_engine":semantic_engine,
        "ratio":ratio,"intensity":intensity
    }
