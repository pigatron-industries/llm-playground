# MiniMax H3 — I2V & FL2V Prompt Format

Three fields, always in this order:

1. `integrated_multimodal_description` — the visual timeline (subject, action, camera)
2. `overall_soundscape` — ambient/physical/human sounds (no dialogue, no music)
3. `non_diegetic_music` — score only, described by instrumentation/tempo/rhythm, never mood words. Use `N/A` if none.

Never leave a field blank — an empty field still gets a soundtrack chosen for you, not silence. Use `N/A` to explicitly request silence.

## I2V (image-to-video)

One reference image as the opening frame.

First line (verbatim, then a blank line):

`For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.`


Then write `integrated_multimodal_description` describing how the scene develops *forward* from that image — preserve what's in the picture (position, look, background) and describe the new motion/action/camera on top of it.

## FL2V (first-last-frame-to-video)

Two reference images: opening and closing frames.

First line (verbatim, then a blank line):

`How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.`


(`S.SS` = exact clip duration to 2 decimals; `N` = final shot index.)

Then describe **only the motion between the two frames** — don't re-describe either image's contents, since both are already given. State that the clip converges onto the composition/pose/lighting established by Picture 2 at the end.

## Shared body structure (`integrated_multimodal_description`)

- `[Shot 1]` opens a shot (no timestamp on the first shot). Later shots: `[Shot 2] At 00:06.000, ...` with strictly increasing timestamps.
- One primary action per shot, with a clear end state.
- Camera moves written as natural action, not stacked labels: e.g. "the camera pushes in with small amplitude at slow speed." Recognized terms: Zoom In/Out, Push In/Out, Pan, Truck, Tilt, Pedestal, Arc Shot, Tracking Shot, Static Shot, Shake, POV, Roll — each with an amplitude (small/large) + speed (slow/fast).
- Dialogue (if used): stable speaker IDs `(S1)`, `(S2)`; exact words inside `<d>[English] ...</d>`, verbatim, never translated.
- On-screen text must be quoted exactly in `"double quotes"`.
- `<scenetrans>` marks a line continuing across a cut; `<cutoff>` marks speech truncated by clip end.

## Key rules

- Budget ~4 seconds per prop change/hand-off; don't overload a short clip with beats.
- Put the most important beat in the middle, not the very end (it can get squeezed).
- Only cut to a new shot if it adds new information (subject/space/state/viewpoint/time); otherwise move the camera instead.
- For FL2V specifically: the two frames already establish appearance — spend the prompt on the *transition*, that's the actual point of control.