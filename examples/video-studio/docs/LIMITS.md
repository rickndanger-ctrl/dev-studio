# Single-photo limits

How far each camera move can go on one still photo before the edges tear. Measured by
`tools/measure_limits.py` and written to `stillmove/limits.json`.

**Method.** Each type is swept up to its catalog value: angles in 1 degree steps, distances
in 0.05 steps. Each step runs one move (directions: dolly z and x both ways, dolly zoom z both
ways, crane up and down, orbit both ways, pan and tilt both ways). The share of pixels that
are holes (not seen from the start pose) is measured at the END frame. The safe limit is the
largest magnitude whose hole share is at most 8%; the table keeps the
smallest limit over directions. The picture is a generated 1024x576 ramp with a checker.
Depth is the fake gradient (3 to 8 units, near at the bottom). Real photos vary: a photo with
a different depth layout will tear at other magnitudes. These are not guarantees.

| Type | Param | Safe limit | Holes at limit | Catalog value | Holes at catalog value |
|---|---|---|---|---|---|
| dolly | distance | <= 0.2 scene units | 7.6 % | 1 scene units | 30.6 % |
| dolly_zoom | distance | <= 1 scene units | 6.9 % | 1 scene units | 6.9 % |
| crane | distance | <= 0.2 scene units | 7.1 % | 1 scene units | 35.6 % |
| orbit | angle | <= 8 degrees | 7.8 % | 45 degrees | 30.9 % |
| pan | angle | <= 4 degrees | 7.7 % | 30 degrees | 42.5 % |
| tilt | angle | <= 3 degrees | 7.1 % | 20 degrees | 41.7 % |
