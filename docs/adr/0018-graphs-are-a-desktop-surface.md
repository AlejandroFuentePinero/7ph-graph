# The graph is a desktop surface, and phone fit is not forced onto it

Issue #161 found that at phone width the interactive graph drew bare coloured dots: vis.js drops a label once its size times the fitted zoom falls under about 4px, and a 67-node neighbourhood fitted into a 390 CSS px frame lands well under it. PR #174 fixed that head-on, solving the label size from the fitted zoom, stretching the layout onto the frame's shape, and stating a fallback where names could not fit. Every acceptance criterion was measured and met.

It was reverted anyway (commit 40505c8 is the fix; this ADR rides with the revert), because review in the running browser found the phone fit was paid for at the width where the graph is actually read:

- **Dead physics.** The stretch moves nodes directly, which physics would undo, so the settle froze physics after stabilisation. Dragging a node no longer pulled its neighbours, at every width.
- **Bowed edges.** The edges use vis.js dynamic smoothing, whose curve control points are solved by that same physics. Frozen mid-flight and then left behind by the stretch, they bent every edge.
- **Oversized quantities.** Edge labels (the percentages) were pinned to the same fixed 12 CSS px as node names instead of scaling with the zoom.

## The decision

A 67-node neighbourhood on a 390px screen is not a readable object, labelled or not, and making it one is not worth degrading the desktop rendering. The central claim of this system against the 7phstats site is better visualisation, and that claim is made on a desktop browser. So:

- Graph views target desktop width. Phone-width graph legibility is a non-goal and will not be forced; a phone reader's answers are the colour key, the details panel, and the charts, which do fit.
- No mechanism that serves phone fit may regress desktop interaction or rendering. #161's three regressions above are the concrete tests.
- This narrows the v1 PRD's (#85) phone-primary framing for the graph specifically. Criterion 11 still holds as it always did (the colour key names each kind, so colour is not the sole encoder); criterion 12 (fills its space from phone width up) is withdrawn for graph frames.

Desktop label legibility remains a real, open problem on its own terms: pre-#174 desktop labels drew around 5px effective, and the densest view may cross the 4px drop threshold. That is #178, which inherits #174's diagnosis and paint-measuring harness (`tests/test_graph_phone.py` at 40505c8) without its constraints.

A future pass that sees unlabelled dots on a phone should land here, not re-file #161.
