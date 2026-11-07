# Existing tools for agent-driven talking-head editing in DaVinci Resolve

Researched 7 Nov 2026. Stars and dates come from the GitHub API on that day. Every claim has a link. Where I could not verify something, I say so.

## Bottom line

1. **Partly covered already.** At least three public AI agent skill packs already drive Resolve Studio for talking-head/YouTube edits: [appariciojunior/video-editor-skills](https://github.com/appariciojunior/video-editor-skills) (424 stars, 12 skills, no code, CC BY-NC), [tiagonrodrigues/davinci-resolve-agent](https://github.com/tiagonrodrigues/davinci-resolve-agent) (script-to-takes, assemble, render, QC by re-transcribing; MIT, 1 day old) and [sidart10/resolve-claude-starter](https://github.com/sidart10/resolve-claude-starter) (plan file, checker, new timeline per version, MIT). Outside Resolve, [browser-use/video-use](https://github.com/browser-use/video-use) (30.7k stars, ffmpeg only) owns the "edit video with Claude Code" story.
2. **Reuse, don't rebuild, the bridge.** Resolve 21.1 ships Blackmagic's own MCP server ([press release](https://www.blackmagicdesign.com/media/partial/release/20260908-03); bundle path per [tiagonrodrigues README](https://github.com/tiagonrodrigues/davinci-resolve-agent)), and [samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp) (3,978 stars, MIT, released v4.19.3 on 6 Nov 2026) covers the full API and already has a plan, confirm, execute edit engine with ffmpeg silence ripple. Your `resolve-bridge` should be a thin layer on these, or a small `run.py`-style script runner.
3. **Reuse for the parts:** auto-editor (silence cuts, Unlicense), whisper via AutoSubs-style local transcription, Remotion's official skills, and existing face-crop code for 9:16.
4. **The gap:** nobody combines, in one MIT/Apache repo, script-matched take selection **plus** framing craft (declutter crop, eye-trace matching across jump cuts, punch-ins), iPhone HLG to Rec.709 colour, -14 LUFS sound with ducking, and shorts reframed **inside Resolve** as a non-destructive timeline. Existing packs each do 2 to 4 of these. The framing and colour skills are the least covered.
5. **Positioning and naming:** "first-pass editor for scripted talking-head YouTube, built on Resolve 21's own API and MCP, local-only (whisper, no ElevenLabs key)". Most competitors need ElevenLabs or a cloud STT key. Avoid `video-editor-skills` and `video-editing-skills` (crowded). `resolve-skills`, `davinci-skills`, `resolve-agent` exist as tiny GitHub repos but are free on npm and PyPI.

**Update (checked locally, 7 Nov 2026):** both functions exist in the Resolve **21.2** stub shipped with Resolve (`Developer/Scripting/DaVinciResolveScript.pyi`): `TimelineItem.RippleDeleteSilence(removeSilenceOptions)` and `TimelineItem.ApplyAutoColor()`. They were added after 21.1, so they need 21.2 or later.

Original caveat: I could not confirm that `RippleDeleteSilence` or `ApplyAutoColor` exist in the scripting API. They are not in the 21.1 API stub mirrored by samuelgursky ([DaVinciResolveScript.pyi](https://github.com/samuelgursky/davinci-resolve-mcp/blob/main/docs/reference/DaVinciResolveScript.pyi), [21.1 changelog](https://github.com/samuelgursky/davinci-resolve-mcp/blob/main/docs/reference/resolve_scripting_changelog_21.1.md)), and that repo's [v2.63.0 release notes](https://github.com/samuelgursky/davinci-resolve-mcp/releases/tag/v2.63.0) say Ripple Delete Silence is UI-only. `NormalizeAudioLevel`, `SetVoiceIsolationState`, `SmartReframe`, `CreateSubtitlesFromAudio`, `DetectSceneCuts`, `AddTransition`, `GetTranscription` and the `DynamicZoomEnabled`/`DynamicZoomEase` properties **are** in that stub. Check Resolve's bundled `Developer/Scripting/README.txt` on your machine before you design around the two missing ones.

---

## 1. DaVinci Resolve MCP servers

| Name | What it does | Last push | Stars | Licence | Reuse verdict |
|---|---|---|---|---|---|
| Blackmagic official MCP (Resolve Studio 21.1+) | Ships inside Resolve, set up via File > Setup AI Assistants. Tools: `run_script`, `run_script_unsafe`, `search_scripting_api`, `get_scripting_api`, `get_scripting_docs`, `get_whats_new`, LUT and DCTL tools. Sources: [BMD press release](https://www.blackmagicdesign.com/media/partial/release/20260908-03), tool list per [tiagonrodrigues README](https://github.com/tiagonrodrigues/davinci-resolve-agent) and [sidart10 README](https://github.com/sidart10/resolve-claude-starter). | ships with Resolve | n/a | Proprietary (BMD) | **Use as the default transport.** Can't redistribute it. The press release does not use the word "MCP"; that detail comes from the READMEs and press coverage ([Digital Production](https://digitalproduction.com/2026/09/08/resolve-21-1-adds-mcp-and-finally-gets-presets/)). |
| [samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp) | Full API coverage (37 compact tools, 390 in full mode), edit engine with plan, confirm and execute, ffmpeg silence ripple, frame-as-image, control panel, free-edition bridge for 21.0.x. Installer configures the agent. | 2026-11-06 | 3,978 | MIT | **Best base layer.** Active, MIT, published on npm. Recommend it as an optional dependency rather than forking it. |
| [barckley75/resolve-claude-mcp](https://github.com/barckley75/resolve-claude-mcp) | 48 Resolve tools, colour nodes, LUTs, CDL, local transcription and subtitles. Says "do not use in production". | 2026-05-14 | 392 | MIT | Reference only. No pushes since May. |
| [hiteshK03/davinci-resolve-mcp](https://github.com/hiteshK03/davinci-resolve-mcp) | 44 tools, runs on the free edition through a bridge script inside Resolve, local Whisper. | 2026-03-28 | 157 | MIT | Not useful. The free-edition trick no longer works on 21.1 ([samuelgursky README](https://github.com/samuelgursky/davinci-resolve-mcp)). |
| [apvlv/davinci-resolve-mcp](https://github.com/apvlv/davinci-resolve-mcp) | MCP for Resolve and Fusion. | 2026-10-22 | 79 | MIT | Reference only. I did not read its README in full. |
| [wassermanproductions/unofficial-davinci-mcp](https://github.com/wassermanproductions/unofficial-davinci-mcp) | Live Studio control or FCPXML fallback. Deterministic engines: beat grid, reference colour match to a .cube LUT, dialogue tightening, EBU R128 loudness. | 2026-07-20 | 37 | Apache-2.0 | Worth reading for its loudness and colour-match code. |
| [lordhoell/davinci-resolve-mcp](https://github.com/lordhoell/davinci-resolve-mcp) | 440+ tools, one per API call, plus an agent skill. | 2026-04-03 | 25 | MIT | Too wide, so it costs too many tokens. Skip. |
| [Tooflex/davinci-resolve-mcp](https://github.com/Tooflex/davinci-resolve-mcp) | MCP server, no description. | 2026-07-25 | 22 | none listed | Skip. No licence. |
| [emircbngl/davinci-resolve-mcp-free](https://github.com/emircbngl/davinci-resolve-mcp-free) | Free edition: FCPXML, Lua snippets, macOS UI automation. | 2026-10-15 | 17 | MIT | Not relevant (you target Studio). |
| [guycochran/resolve-mcp-server](https://github.com/guycochran/resolve-mcp-server) | 53 tools. | 2026-09-16 | 15 | MIT | Skip. |
| [CiprianSpiridon/davinci-resolve-mcp](https://github.com/CiprianSpiridon/davinci-resolve-mcp) | 334 tools. | 2026-08-14 | 7 | MIT | Skip. |

Source for the table rows: `gh search repos "davinci resolve mcp"`, 7 Nov 2026.

## 2. AI agent skills and plugins for video, Resolve, ffmpeg, Remotion and shorts

### Resolve-specific skill packs (closest overlap)

| Name | What it does | Last push | Stars | Licence | Overlap with your plan |
|---|---|---|---|---|---|
| [appariciojunior/video-editor-skills](https://github.com/appariciojunior/video-editor-skills) | 12 skills, **no code**. The agent builds the scripts with you. Skills: setup, edit-video, rough-cut, vlog-pacing (silence, pads, jump-cut zoom), broll, resolve-scripting, voice-and-audio (loudness), music-and-sound (ducking, SFX, Epidemic Sound), captions, motion-overlays (Remotion), figma-roundtrip, youtube-export. Adds only, never touches templates. Needs a cloud STT key. | 2026-10-23 | 424 | CC BY-NC 4.0 (not OSI open source) | **High.** Covers assemble, tighten, sound, graphics, captions, deliver. Missing: colour, framing craft, shorts, QC, actual code. The NC licence means you can't reuse its text in an MIT repo. |
| [tiagonrodrigues/davinci-resolve-agent](https://github.com/tiagonrodrigues/davinci-resolve-agent) | One skill plus scripts: ElevenLabs transcription, dossier, keep the last complete take per script paragraph, cut list on quiet frames, alternating punch-in, Voice Isolation, render presets, QC (LUFS, true peak, frozen frames, re-transcribe the final mix), deliver. Tested on Resolve Studio 21.1.2 on macOS. | 2026-11-06 | 0 | MIT | **Very high** for ingest, assemble, tighten, QC and deliver. Created the day before this research. No colour, framing craft, graphics or shorts. MIT, so code can be reused with credit. |
| [sidart10/resolve-claude-starter](https://github.com/sidart10/resolve-claude-starter) | A plan file, a checker, a new timeline per version that is read back clip by clip, colour by node label, a render tag check, a smoke test. Resolve Studio 21.1+, uses the official MCP. | 2026-10-22 | 1 | MIT | **High** for `resolve-bridge`. Same non-destructive idea. |
| [ChaiWithJai/davinci-resolve-claude-skills](https://github.com/ChaiWithJai/davinci-resolve-claude-skills) | 11 skills for Resolve 20, mostly knowledge (wikis built from BMD training PDFs): screen-recording cuts, webcam grade, podcast audio, titles, multi-platform export, social editor. | 2026-05-17 | 32 | MIT | Medium. Knowledge-heavy, not script-driven. |
| [MusicProWorkshop/davinci-resolve-skills](https://github.com/MusicProWorkshop/davinci-resolve-skills) | Builds editable Resolve timelines from screenshots and Remotion videos. | 2026-11-06 | 1 | not checked | Low. |
| [Simiely/davinci-skills](https://github.com/Simiely/davinci-skills) | Chinese-language Resolve scripting skills for edit, colour and delivery, with runnable Python. | 2026-11-01 | 0 | not checked | Low (language and audience). |
| [timmyha2612-droid/beon-edit](https://github.com/timmyha2612-droid/beon-edit) | One studio's Resolve agent plugin. | 2026-11-03 | 0 | none | Low. |
| [fuuuuuuma/premiere-skills](https://github.com/fuuuuuuma/premiere-skills) | Premiere, not Resolve: silence cut and Whisper SRT. | 2025/26 | 14 | none | Shows the pattern exists for Premiere too. |

### General video-editing skills (ffmpeg, Remotion, HyperFrames)

| Name | What it does | Last push | Stars | Licence | Verdict |
|---|---|---|---|---|---|
| [browser-use/video-use](https://github.com/browser-use/video-use) | Agent skill: filler and dead-air cuts, auto colour via ffmpeg, 30 ms fades, burned subtitles, Remotion/Manim overlays, self-eval at every cut. ffmpeg render, **no NLE**. Needs ElevenLabs. | 2026-10-08 | 30,687 | MIT | The big name. Your angle is "editable Resolve timeline, not a flat mp4". Its transcript-packing and `timeline_view` ideas are worth borrowing. |
| [digitalsamba/claude-code-video-toolkit](https://github.com/digitalsamba/claude-code-video-toolkit) | Remotion, ElevenLabs, FFmpeg, Playwright skills. | 2026-11-06 | 2,471 | MIT | Graphics reference. |
| [remotion-dev/skills](https://github.com/remotion-dev/skills) | Official Remotion agent skills. | 2026-11-05 | 6,529 | not stated by API | **Depend on it** for `graphics`, don't rewrite it. |
| [pycodebr/heditor](https://github.com/pycodebr/heditor) | Vertical video editing skill (HyperFrames). | 2026-11-04 | 109 | other | Shorts reference. |
| [nopefallacy/vertical-video-editing-skills](https://github.com/nopefallacy/vertical-video-editing-skills) | 9:16 editing: captions, b-roll, presets. | 2026-06-21 | 68 | MIT | Shorts reference. |
| [6missedcalls/video-editing-skill](https://github.com/6missedcalls/video-editing-skill) | Trim, jump cut, captions, speed, using ffmpeg. | 2026-02-27 | 50 | MIT | Low. |
| [WhiteTowerAI/cut-as-code](https://github.com/WhiteTowerAI/cut-as-code) | Agentic editing skills, pitched against Opus Clip and CapCut. | 2026-11-05 | 30 | MIT | Shorts reference. |
| [RoverEva/eva-video-style](https://github.com/RoverEva/eva-video-style) | Talking-head templates, captions, motion graphics. | 2026-11-07 | 44 | MIT | Low. |
| [snow-shen/snowcut](https://github.com/snow-shen/snowcut) | Talking-head skill: transcribe, cut gaps, browser preview, render. | 2026-11-06 | 1 | other | Low. |
| [joe-jns/long-video-to-shorts](https://github.com/joe-jns/long-video-to-shorts) | Local 9:16 captioned shorts, as an agent skill. | 2026-07-15 | 1 | other | Shorts reference. |
| [Jaycheng1103/chatgpt-video-editing-skills](https://github.com/Jaycheng1103/chatgpt-video-editing-skills) | Chinese short-video editing workflow for Codex. | 2026-07-26 | 616 | MIT | Different audience. |

Several Chinese "口播" (talking-head) skill packs also exist, e.g. [darrenli6/JJKoubo](https://github.com/darrenli6/JJKoubo) (42 stars). None target Resolve.

### Official repo and curated lists

- [anthropics/skills](https://github.com/anthropics/skills) has no video, ffmpeg or Resolve skill. Its `skills/` folder has 19 skills (pdf, docx, pptx, xlsx, slack-gif-creator, etc.). Checked via the GitHub contents API.
- [VoltAgent/awesome-agent-skills](https://github.com/VoltAgent/awesome-agent-skills) lists Remotion's official skills and fal.ai video skills. No Resolve entry found by grepping the README.
- [ComposioHQ/awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills) and [BehiSecc/awesome-claude-skills](https://github.com/BehiSecc/awesome-claude-skills): video downloader, YouTube transcript, VideoDB, the claude-code-video-toolkit. No Resolve entry found.
- [travisvn/awesome-claude-skills](https://github.com/travisvn/awesome-claude-skills): no video entries found.
- So no curated list has a Resolve editing skill yet. That is an easy listing win.

## 3. Open-source tools for talking-head editing

| Name | What it does | Last push | Stars | Licence | Reuse verdict |
|---|---|---|---|---|---|
| [WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor) | Cuts silence or low motion. Exports `--export resolve`, Premiere, FCP, ShotCut, Kdenlive. | 2026-11-07 | 5,802 | Unlicense | **Reuse** as an optional `tighten` backend or a baseline to compare against. Note: you said VAD; auto-editor works on loudness and motion, not VAD. |
| [tmoroney/auto-subs](https://github.com/tmoroney/auto-subs) | Local on-device subtitles, runs from Workspace > Scripts in Resolve. Also Premiere and After Effects. | 2026-10-30 | 4,513 | MIT | Reference for `captions`. Resolve's own `CreateSubtitlesFromAudio` may be enough. |
| [carykh/jumpcutter](https://github.com/carykh/jumpcutter) | The original silence jump-cutter. | 2024-02-28 | 3,145 | MIT | Historic. Skip. |
| [lagmoellertim/unsilence](https://github.com/lagmoellertim/unsilence) | CLI and library to remove silent parts. | 2023-03-28 | 596 | MIT | Unmaintained. Skip. |
| [cobanov/autocut](https://github.com/cobanov/autocut) | Desktop app: remove silences, export MP4 or Resolve-friendly FCPXML. | 2026-08-31 | 76 | none | Reference only. |
| [pedrolabonia/pydavinci](https://github.com/pedrolabonia/pydavinci) | Typed Python wrapper for the Resolve API. | 2026-04-01 | 183 | MIT | Optional. The official .pyi stub now does much of this. |
| [Anil-matcha/AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator) | Long video to 9:16 shorts with LLM highlight picking. | 2026-11-06 | 5,801 | MIT | Reference for highlight selection in `shorts`. |
| [kamilstanuch/Autocrop-vertical](https://github.com/kamilstanuch/Autocrop-vertical) | YOLOv8 and FFmpeg 16:9 to 9:16 crop. | 2026-02-15 | 443 | **none** | Can't copy code (no licence). Ideas only. For 9:16 you can use Resolve's own `SmartReframe()` (in the API stub). |
| [xixihhhh/hotclip](https://github.com/xixihhhh/hotclip) | Chinese Opus Clip alternative: highlights, captions, horizontal to vertical. | 2026-10-26 | 437 | AGPL-3.0 | Avoid copying (AGPL). |
| [Shaarav4795/ClippedAI](https://github.com/Shaarav4795/ClippedAI) | Opus Clip alternative. | 2026-09-04 | 231 | other | Reference. |
| [ColinGPT9/clips-studio](https://github.com/ColinGPT9/clips-studio) | Local clipper for streams. | 2026-11-07 | 216 | AGPL-3.0 | Avoid copying. |
| [fralapo/clippyme](https://github.com/fralapo/clippyme) | Self-hosted Opus Clip alternative with 9:16 reframe. | 2026-10-14 | 62 | MIT | Reference for face-tracked crop. |
| [Greenysmac/awesome-davinci-resolve](https://github.com/Greenysmac/awesome-davinci-resolve) | Curated list of Resolve tools. | 2026-09-07 | 397 | other | Place to list the repo once it ships. |

I did not find an open-source tool for **eye-trace matching across jump cuts** or **per-setup declutter crops**. My searches ("reframe vertical video", "talking head video editing", "smart crop vertical video face") turned up only 9:16 face-centring tools. This looks like a real gap, but "I didn't find it" is weaker than "it doesn't exist".

## 4. Commercial competitors

| Product | What it does (one line) | Source |
|---|---|---|
| Descript | Edit video by editing the transcript, filler-word removal, Studio Sound, eye contact, and the Underlord AI assistant. Its own editor; the homepage doesn't show Resolve export. | [descript.com](https://www.descript.com/) |
| Gling | For YouTubers: removes bad takes, silences and filler words, adds captions and auto zooms, exports to FCP, Resolve and Premiere. | [gling.ai](https://www.gling.ai/) |
| OpusClip | Long video to short clips, AI reframe with object tracking, captions. The homepage doesn't confirm NLE export. | [opus.pro](https://www.opus.pro/) |
| FireCut | Plugin inside Premiere and Resolve: silence removal, zoom cuts, captions in 50+ languages, chapters. | [firecut.ai](https://firecut.ai/) |
| AutoCut | Plugin inside Premiere and Resolve 18.6+ (Free and Studio): silences, captions, zooms, B-roll, repeated takes, podcast multicam, reframe. | [autocut.com](https://www.autocut.com/en/) |
| TimeBolt | Local Mac/Windows app: removes silence, filler words and bad takes, then hands off to Premiere, FCP or Resolve. | [timebolt.io](https://www.timebolt.io/) |
| Resolve 21.1 itself | Built-in AI assistant hookup. BMD says assistants can "create highlight edits from long form video, remove unwanted clips and render deliverables". | [BMD press release](https://www.blackmagicdesign.com/media/partial/release/20260908-03) |

**The gap against these:** all are closed and subscription-priced (pricing not checked here). None follows a script, matches eye-lines across cuts, or does colour (HLG to Rec.709, groups, look). They stop at silence, zoom and captions. An open skill pack can be inspected, changed and run locally. It also keeps the result as an editable Resolve timeline.

## 5. Name collisions

Checked on 7 Nov 2026: `registry.npmjs.org/<name>` and `pypi.org/pypi/<name>/json` (404 = free), and GitHub repos whose name ends exactly in `/<name>`.

| Name | npm | PyPI | GitHub repos with this exact name |
|---|---|---|---|
| `video-editing-skills` | free | free | **16 repos**, e.g. [liangali/video-editing-skills](https://github.com/liangali/video-editing-skills) (22 stars). Crowded. |
| `video-editor-skills` | free | free | 3, including [appariciojunior/video-editor-skills](https://github.com/appariciojunior/video-editor-skills) (424 stars, a direct competitor). **Avoid.** |
| `resolve-skills` | free | free | 2: [oiamario/resolve-skills](https://github.com/oiamario/resolve-skills) (Resolve knowledge-base skills, 0 stars), [erishen/resolve-skills](https://github.com/erishen/resolve-skills) (unrelated). |
| `davinci-skills` | free | free | 2: [Simiely/davinci-skills](https://github.com/Simiely/davinci-skills) (Resolve scripting, Chinese, 0 stars), [zwyin/davinci-skills](https://github.com/zwyin/davinci-skills) (0 stars). |
| `davinci-resolve-skills` | free | free | 1: [MusicProWorkshop/davinci-resolve-skills](https://github.com/MusicProWorkshop/davinci-resolve-skills) (1 star, Resolve timelines). |
| `talking-head-skills` | free | free | 2: [iart-ai/talking-head-skills](https://github.com/iart-ai/talking-head-skills) (AI-generated talking heads, different meaning), [muhaiminmiyn99-blip/talking-head-skills](https://github.com/muhaiminmiyn99-blip/talking-head-skills) (empty description). |
| `resolve-agent` | free | free | 8, mostly unrelated ("resolve" as in customer-service tickets), e.g. [ai-guru-global/resolve-agent](https://github.com/ai-guru-global/resolve-agent). Similar: [tiagonrodrigues/davinci-resolve-agent](https://github.com/tiagonrodrigues/davinci-resolve-agent) (direct competitor). |
| `talking-head-editor` | free | free | 5 small repos. |
| `resolve-kit` | free | free | 3 unrelated repos. |

None of the names is taken on npm or PyPI. On GitHub, a repo name only needs to be unique per account, so these collisions are about search and confusion, not legal blocks. `resolve-skills` and `davinci-skills` are only lightly used (0 stars each). The word "resolve" is ambiguous in search. A name built around the job (talking-head, script-to-cut) may stand out more than one built around the tool. I did not check trademarks. "DaVinci Resolve" is a Blackmagic trademark, so a name that starts with it may read as official.
