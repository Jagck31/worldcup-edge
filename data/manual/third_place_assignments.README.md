# Third-Place Assignment Table

The 2026 round-of-32 bracket depends on the exact set of eight third-placed groups that
advance. FIFA's regulations publish this as an Annex C table with 495 possible
combinations.

This project deliberately does not guess that mapping. Bracket-dependent markets such as
champion, finalist, exact route, or "third-place team faces Team X" should use
`data/manual/third_place_assignments.csv` with this schema:

```csv
qualified_groups,1A,1B,1D,1E,1G,1I,1K,1L
CDEFGHIJ,E,J,I,F,H,G,L,K
```

`qualified_groups` is the sorted eight-letter combination of third-placed groups that
qualified. The remaining columns are the third-place group assigned to each winner slot.
Values should be plain group letters, not `3E`.

Without this table, the simulator can still validate group standings and advancement
counts, but it should not produce bracket-dependent probabilities. That is intentional:
an approximate bracket silently corrupts every downstream sub-market.

The checked-in CSV contains 495 combinations parsed from the published 2026 knockout-stage
combination table mirrored at
`https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_knockout_stage#Combinations_of_matches_in_the_round_of_32`.
Values are stored as plain group letters because `load_third_place_assignment_table`
adds the `3` prefix when assigning slots.
