# resolve-video-skills

AI agent skills that edit scripted talking-head videos in DaVinci Resolve Studio. You give it raw takes and a script. It builds the cut, frames it, grades it, mixes it and makes vertical shorts, all as **editable Resolve timelines**, not a flattened mp4.

> **Status: early.** Available now: `resolve-bridge` (the versioned, always-saved base the other skills build on) `video-setup` (prepares each video project), `video-organize` (finds and scores takes, catalogues b-roll) and `video-tighten` (cut edges and pauses). The other skills are next. See [docs/ROADMAP.md](docs/ROADMAP.md).

## What it does

| Step | Skill | What you get |
|---|---|---|
| Set up | `video-setup` | Tools and footage checked, first timeline tracked, an `AGENTS.md` with the editing rules for that video |
| Organise | `video-organize` | Raw files in random order → transcribed, takes found and scored per storyboard scene, b-roll catalogued, files renamed after you approve |
| First cut | `video-assemble` | Best take per script line, b-roll placed, a list of shots still missing |
| Pacing | `video-tighten` | Dead air removed, natural breathing room at every cut |
| Framing | `video-framing` | Cluttered edges cropped out, punch-ins on jump cuts, eyes stay in place across cuts, push-ins on key lines |
| Colour | `video-color` | Correct HDR → Rec.709 conversion, one consistent grade per camera setup |
| Sound | `video-sound` | Voice isolation, click-free cuts, YouTube loudness, music ducked under speech |
| Graphics | `video-graphics` | Animated callouts and overlays (Remotion) |
| Captions | `video-captions` | Subtitles corrected against the script |
| Review | `video-qc` | The render re-transcribed and checked against the script, cut edges, loudness and gaps |
| Delivery | `video-deliver` | Render, chapters, description draft |
| Shorts | `video-shorts` | 9:16 clips cut from the main video, face-tracked, captioned |

## How it works

- **Nothing is overwritten.** Every step creates a new timeline. Older versions move to an `Archive` folder in the media pool.
- **Steps build on each other across sessions.** A state file in your video project records which step is done and which timeline is current. Each new agent session continues from there.
- **A checklist per video.** `EDIT_CHECKLIST.md` in your project folder shows the next step, what is done or skipped, open issues to check (with clip and timecode), and your own notes. Tick an issue in the file to close it.
- **Your manual edits are kept.** If you change the timeline in Resolve between steps, the next step reads your changes in first.
- **Runs locally.** Transcription uses Whisper on your machine. No paid speech-to-text API key needed.

## Requirements

- DaVinci Resolve **Studio** 21.2 or later (most AI scripting functions are Studio-only)
- macOS on Apple Silicon (for now)
- An AI coding agent that supports skills (`SKILL.md` folders)
- `ffmpeg`, Python 3.11+

## Install

```bash
npx skills add johnnyfekete/resolve-video-skills
```

This uses the open [skills CLI](https://github.com/vercel-labs/skills). It detects which AI agents you have installed and copies the skills into each one. To install a single skill, add `--skill video-tighten`.

Then in Resolve: Preferences → System → General → External scripting using: **Local**.

For each new video, start your agent in that video's folder and ask it to run `video-setup`.

## Credits and related projects

Built on DaVinci Resolve's scripting API. Related work this project learns from: [samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp), [tiagonrodrigues/davinci-resolve-agent](https://github.com/tiagonrodrigues/davinci-resolve-agent), [sidart10/resolve-claude-starter](https://github.com/sidart10/resolve-claude-starter), [WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor), [remotion-dev/skills](https://github.com/remotion-dev/skills).

Not affiliated with Blackmagic Design. DaVinci Resolve is a trademark of Blackmagic Design.

## Licence

MIT
