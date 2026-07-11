# LOOP-006 result — Professional Plotly and static fallbacks

**Status:** Complete after direct PNG inspection.

- Timeline and actor Mermaid/text artifacts were replaced by Plotly HTML plus PNG fallbacks.
- Static CJK rendering now uses only verified installed glyph coverage; it never invents labels such as `Actor 4` or `Event` when a font is missing.
- Timeline labels use numbered key-event markers and a two-column key; actor-network labels use deterministic collision-planned annotations and show only the 16 highest-priority actors while every retained node remains available on hover.
- Scenario bars use the published forecast taxonomy as the canonical distribution. Ensemble rows may add uncertainty only after exact normalized ID/name matching; unrelated scenario taxonomies can no longer create totals above 100%.
- Matplotlib figures close on all success/failure paths, and Kaleido-unavailable fallbacks are covered.
- Focused visualizer regression set passed (133 before the final focused Plotly additions); the final scenario/timeline/actor subset also passes. Regenerated PNGs for the three reports were inspected directly.
