# Release notes

## v1.1.0 – 2026-11-19

### Changed
- **Renamed** to **Feature Outlines from Lines** (file `Feature-Outlines-from-Lines.py`, algorithm ID `feature_outlines_from_lines`). Settings stored in a project by v1.0.x are not carried over, and models or history entries that call the old algorithm ID need to be updated.
- **New defaults for the first run:** gap tolerance `0.05`, minimum area `0.0001`. A minimum area of `0` produced small triangular polygons from badly connected open lines.
- The fallback now works **per unclosed outline** instead of per cluster. Before, a cluster (for example all lines of one group value) was only wrapped if it had no closed polygon at all, so single unclosed stones inside a group were dropped silently. Now each connected group of lines without a usable polygon is wrapped separately by the chosen hull.

### Added
- **Minimum width** parameter (default `0.01`): polygons that are thinner than this everywhere are discarded as slivers. The test never changes the kept polygons or their Z values.
- **Z values** setting. Besides keeping the vertex heights (default), each result polygon can be made **flat** at the **mean**, **lowest** or **highest** Z of its vertices. Applied at the end of the run, so merged polygons are flattened as a whole. Stored in the project like the other settings.

### Documentation
- README rewritten in plainer language, with a new example of a wall made almost only of open lines (vertex heights and attribute values carried over).
- User manual updated. Both now show the tool dialog.

### Notes
- The fallback is still off by default (*Skip*).
- Not yet tested in QGIS: the per-outline fallback, the sliver filter and the Z option are new in this release.

## v1.0.1 – 2026-11-18

### Fixed
- **Different features were merged.** With a group field, the optional merge step of overlapping result polygons did not respect the group value. If the polygons of two features (e.g. `F5_0090` and `F5_0091`) overlapped, they were united and carried the attributes of the larger one. Now only polygons with the same group value are merged. Different features always stay separate.

### Notes
- Overlapping polygons of different features are kept as they are.
- Without a group field, behaviour is unchanged.

## v1.0.0 – 2026-11-18

First public release.

### What it does
- Converts open and closed feature lines into polygons covering the full extent of each archaeological feature.
- Carries over attribute values (taken from the longest line of a feature) and vertex Z values. No attribute fields are added.
- Output is a Polygon layer, or PolygonZ if the input has Z.

### Features
- **Network approach:** lines are noded, free line ends are bridged to the nearest line end or line within the gap tolerance, then the network is polygonized. Parallel lines are never glued together, so free areas with a narrow opening stay free.
- **Feature clustering** by an optional group field (e.g. feature number), otherwise by proximity.
- **Merge** of overlapping result polygons in the same run.
- **Fallback** for clusters without a closed outline: skip, concave hull or convex hull.
- **QC point layer** (optional) marking every line end that could not be closed.
- **Minimum area** filter.
- **Settings are stored in the QGIS project** and restored the next time the tool is opened.
- Progress reporting, cancel support and a summary in the Processing log.

### Compatibility
- Developed and tested with QGIS 4.2.2 (Qt 6.11, GEOS 3.14, Python 3.12).
- Expected to work on QGIS 3.x; the *Concave hull* fallback needs QGIS ≥ 3.28.

### Known limitations
- Free areas that are completely enclosed by lines are filled.
- Gaps larger than the gap tolerance stay open.
- Curved segments are converted to straight segments.
