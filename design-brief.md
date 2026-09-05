# Design Brief — One-Stop Workflow for Short-Form Creators

## What this is

A web app where a creator describes what they want to post, and the system plans it,
generates it using AI models, and assembles a finished short-form video.

The user never picks a model, never picks a tool from a menu, and never touches a timeline.

## Who it's for

Independent creators making TikToks, Reels, and Shorts. One person, no team, no editor,
no budget for one. Currently juggling CapCut plus three separate AI tools and a lot of
file-shuffling between them.

Assume: often on a phone, working fast, publishing frequently, not a professional editor.

## The core UX principle

**Outcome-first, not tool-first.** The user says what they want to end up with. The system
proposes how to get there.

A flat menu of tools ("Video Generation", "Voice Over", "Subtitles") is the thing we are
deliberately not building. It forces the user to diagnose their own problem before they can
start, which is exactly the tool-hopping we're replacing.

## The four screens

### 1. Start
One input area. Accepts a video, an image, a script, a link, or plain typed text. The system
detects what it got. No tabs, no file-type pickers, no mode selector.

This screen should feel like the easiest thing in the app. It is the whole first impression.

### 2. Recipe
The system proposes an outcome in one plain sentence, e.g. *"A 60-second explainer Short from
this article — 12 scenes, your voice, animated captions."* The user confirms or adjusts.

Four recipes at launch: Explainer, Talking-head from script, Clip my long video, Subtitle only.

Show the estimated credit cost here, before anything runs.

### 3. Storyboard — this is the product
A vertical list of scene cards, one per shot. Not a timeline. Not a chat log.

Each card holds: its image or clip, its script line, its voiceover, its caption text.
Each card can be regenerated, edited, swapped, retimed, or deleted independently.

Design priorities for this screen:
- Works one-handed on a phone
- Per-scene progress, never one global spinner
- Regenerating one card must obviously not disturb the others
- Reordering should feel physical

### 4. Deliver
Render and export. One source storyboard produces 9:16, 1:1, and 16:9.

Plus two supporting screens: **Identity kit** (voice profile, logo, palette, fonts, caption
style, intro/outro — inherited by every new project) and **Credits**.

## Interaction mechanics that shape the design

**Preview-first.** Every scene is first generated cheaply — a still image plus text-to-speech.
The user sees the whole video in seconds, for almost nothing. They then mark individual scenes
to upgrade to real video generation. The UI must make this two-tier state legible at a glance:
which scenes are previews, which are finished, what upgrading costs.

This is the most important mechanic in the product. It solves cost and iteration at once.

**Cost before every action.** Any button that spends credits shows what it will spend.
Never surprise someone with a drained balance.

**Jobs take minutes.** The app must survive a closed tab. Progress is per-scene and resumable.
Notification when work finishes.

**Voice profile.** The creator records ~60 seconds once; every later video uses their voice.
Give this real prominence in onboarding — it is the strongest retention hook.

**Series memory.** Episode 2 inherits character, voice, caption style, and palette from
episode 1. Consistency across episodes should feel automatic, not configured.

## Constraints that must inform the design

**Built for any language from day one.** Not English-first with translation bolted on later.
This is a real design constraint, not a checkbox:
- Test layouts with long strings and with complex scripts, never Latin placeholder text
- Many writing systems have no uppercase, so designs leaning on capitalised labels or
  all-caps eyebrows break outside English
- Scripts with stacked marks and diacritics need generous line-height and no tight tracking
- Label and button widths must flex; the same word varies enormously in length across languages

**Mobile-first, genuinely.** Not a desktop layout that reflows. Thumb-reachable primary
actions, bottom-anchored controls on the storyboard.

**Variable connections.** Skeleton states, progressive image loading, low-resolution previews
first. Assume media arrives slowly and sometimes not at all.

**Free tier must be genuinely usable**, and the upgrade path must not nag.

## Non-goals

- No editing timeline
- No canvas or node graph
- No model picker
- No plugin or template marketplace
- No screen recording

## Open decisions

- Whether Explainer is the single launch recipe, or all four ship together
- Whether preview mode is the default for everyone or only on the free tier
- How prominently the credit balance sits in the persistent chrome

## Design direction — deliberately unspecified

No palette, typeface, or visual style has been chosen. That is open.
