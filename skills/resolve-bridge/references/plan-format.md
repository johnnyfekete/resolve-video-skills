# Plan format (`rvs.plan/1`)

A plan is a JSON description of a timeline. `export` writes one, `build` reads one. All positions are integer **frames**, so there is no rounding.

```json
{
  "schema": "rvs.plan/1",
  "timeline": {"name": "My_Video_v03_framing", "fps": 24.0, "width": 1920, "height": 1080, "start_frame": 86400},
  "tracks": {
    "video": [{"index": 1, "name": "On camera"}, {"index": 2, "name": "B-roll"}],
    "audio": [{"index": 1, "name": "Dialogue"}]
  },
  "items": [
    {
      "id": "i001",
      "clip": "S01_IMG_5365.MOV",
      "path": "/Volumes/Footage/S01_IMG_5365.MOV",
      "media": "both",
      "track": 1,
      "audio_track": 1,
      "start": 0,
      "end": 347,
      "src_start": 3260,
      "src_end": 4127,
      "src_fps": 60.0,
      "props": {"ZoomX": 1.12, "ZoomY": 1.12, "Pan": -40.0},
      "fades": {"FadeIn": 2, "FadeOut": 2},
      "audio_props": {"AudioVolume": -3.0},
      "audio_fades": {"FadeIn": 2, "FadeOut": 2},
      "audio_mapping": {"1": {"channel_idx": [1, 2], "mute": false, "type": "stereo"}}
    }
  ],
  "unsupported": [{"track_type": "video", "track": 4, "name": "Text+", "start": 120, "end": 200}]
}
```

## Item fields

| Field | Meaning |
|---|---|
| `clip` | Media pool clip name. Used to find the clip when `path` is missing. |
| `path` | File path of the source. Preferred way to find the clip (names can repeat). |
| `media` | `both` (linked video + audio), `video` or `audio`. |
| `track` | Video track index (1-based). Absent for audio-only items. |
| `audio_track` | Audio track index. Absent for video-only items. |
| `start`, `end` | Timeline frames, relative to the timeline start (0 = first frame). `end` is exclusive. |
| `src_start`, `src_end` | Source frames in the clip's own frame rate. `src_end` is exclusive. For a new item, set `src_end = src_start + (end - start) * src_fps / timeline fps`, rounded up. |
| `src_fps` | Source frame rate, for reference. |
| `props` | Video item properties that differ from default: `Pan`, `Tilt`, `ZoomX`, `ZoomY`, `ZoomGang`, `RotationAngle`, `AnchorPointX/Y`, `FlipX/Y`, `CropLeft/Right/Top/Bottom`, `CropSoftness`, `DynamicZoomEnabled`, `DynamicZoomEase`, `Opacity`, `CompositeMode`. |
| `audio_props` | `AudioVolume`, `AudioPan`, `AudioVoiceIsolationEnabled`, `AudioVoiceIsolationAmount`. |
| `fades`, `audio_fades` | Fade-in/out lengths in frames. |
| `audio_mapping` | Which source channels the audio item plays (Resolve's `track_mapping`, exactly one track). |
| `enabled` | `false` if the clip is disabled. Absent means enabled. |
| `id` | Stable within one plan file only. Do not use it to match items across versions; match on `track`/`audio_track` + `start`. |

`unsupported` lists timeline items with no media pool clip (titles, generators, Fusion compositions). They are kept by forks but not by builds.

## Editing a plan for a build

- To cut a piece out of an item: split it into two items, shift `src_start` on the second, and move every later item on the same tracks earlier by the removed length (ripple).
- Keep linked video and audio as one `both` item, so they stay linked and in sync.
- Do not invent `path` values. Copy them from existing items or look them up in the media pool.
