# -*- coding: utf-8 -*-
"""
Featurelines to Polygons
========================
QGIS Processing script that converts the outlines of archaeological features,
drawn as a mix of open and closed lines, into polygons covering the full
extent of each feature. Vertex Z values and attribute values are carried over.

    Author:  L453RP0MM35
    Version: 1.0.0
    License: MIT (see LICENSE)

Installation
    Processing Toolbox > Scripts (Python icon) > "Add Script to Toolbox..."
    and select this file. The tool appears under  Scripts > Archaeology.

Method (network approach, comparable to the native "Lines to polygons")
    1. Lines are grouped into feature clusters: same value in the optional
       group field, otherwise lines closer together than the gap tolerance.
    2. The lines of a cluster are noded (split at every intersection).
    3. Every free line end ("dangle") is bridged to the nearest other free
       end, or to the nearest point on another line, if it is closer than the
       gap tolerance. Only line ENDS are bridged - parallel lines are never
       glued together, so free areas with a narrow opening stay open.
    4. The network is polygonized and all faces of a cluster are united.
    5. Optionally, overlapping result polygons are merged.

Attributes are copied from the longest line of a cluster (no fields are
added). The Z values of the input vertices are preserved. All dialog settings
are stored in the QGIS project and restored the next time the tool is opened.
"""

import math

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsLineString,
    QgsPoint,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDistance,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProject,
    QgsRectangle,
    QgsSpatialIndex,
    QgsWkbTypes,
)

__author__ = 'L453RP0MM35'
__version__ = '1.0.0'
__license__ = 'MIT'

SETTINGS_SCOPE = 'Featurelines_to_Polygons'   # key prefix inside the project file
KEY_PRECISION = 6                             # decimals used to match line ends


# ---------------------------------------------------------------- settings
def _get(kind, key, default):
    """Read a stored setting from the current project (kind: Entry/DoubleEntry/...)."""
    reader = getattr(QgsProject.instance(), 'read' + kind)
    return reader(SETTINGS_SCOPE, key, default)[0]


# ---------------------------------------------------------------- helpers
class _UnionFind:
    """Minimal disjoint-set structure used for clustering and merging."""

    def __init__(self):
        self.parent = {}

    def add(self, x):
        self.parent.setdefault(x, x)

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def groups(self):
        out = {}
        for x in self.parent:
            out.setdefault(self.find(x), []).append(x)
        return list(out.values())


def _is_blank(value):
    return (value is None or value == ''
            or (hasattr(value, 'isNull') and value.isNull()))


def _end_key(pt):
    return (round(pt.x(), KEY_PRECISION), round(pt.y(), KEY_PRECISION))


def _mean_z(geoms):
    zs = [v.z() for g in geoms if g.constGet().is3D() for v in g.vertices()]
    zs = [z for z in zs if not math.isnan(z)]
    return sum(zs) / len(zs) if zs else 0.0


def _is_type(geom, geometry_type):
    return QgsWkbTypes.geometryType(geom.wkbType()) == geometry_type


class FeaturelinesToPolygons(QgsProcessingAlgorithm):
    INPUT = 'INPUT'
    GROUP = 'GROUP'
    TOL = 'TOL'
    FALLBACK = 'FALLBACK'
    CONC = 'CONC'
    MERGE = 'MERGE'
    MINAREA = 'MINAREA'
    OUTPUT = 'OUTPUT'
    QC = 'QC'

    FALLBACKS = ['Skip (no polygon)', 'Concave hull', 'Convex hull']

    # ------------------------------------------------------- identification
    def name(self):
        return 'featurelines_to_polygons'

    def displayName(self):
        return 'Featurelines to Polygons'

    def group(self):
        return 'Archaeology'

    def groupId(self):
        return 'archaeology'

    def tags(self):
        return ['archaeology', 'excavation', 'feature', 'outline', 'lines',
                'polygons', 'polygonize', 'hull']

    def shortDescription(self):
        return 'Closes open/closed feature outlines and creates feature polygons.'

    def createInstance(self):
        return FeaturelinesToPolygons()

    def shortHelpString(self):
        return (
            'Converts open and closed feature lines into polygons covering the '
            'full extent of each feature. Attributes and vertex Z values are '
            'carried over.\n\n'
            'Gap tolerance: a free line end is connected to the nearest other '
            'line end or line if it is closer than this distance (map units, '
            'usually metres). Only line ends are bridged; parallel lines are '
            'never merged, so free areas inside a feature stay free.\n\n'
            'Group field (optional): lines with the same value (e.g. a feature '
            'number) always belong to one feature. Without it, lines closer '
            'together than the tolerance form one feature.\n\n'
            'Fallback: used for clusters for which no closed outline could be '
            'built. Concavity (fallback "Concave hull" only): 1 = convex hull, '
            '0 = tightest concave hull.\n\n'
            'Attributes come from the longest line of each cluster. The optional '
            'QC output lists line ends that could not be closed. All settings '
            'are stored in the project.\n\n'
            'Version %s, author %s.' % (__version__, __author__)
        )

    # ----------------------------------------------------------- parameters
    def initAlgorithm(self, config=None):
        last_layer = _get('Entry', 'input', '')
        if not last_layer or QgsProject.instance().mapLayer(last_layer) is None:
            last_layer = None

        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, 'Feature lines', [QgsProcessing.TypeVectorLine],
            defaultValue=last_layer))
        self.addParameter(QgsProcessingParameterField(
            self.GROUP, 'Group field (optional, e.g. feature number)',
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Any, optional=True,
            defaultValue=_get('Entry', 'group', '') or None))
        self.addParameter(QgsProcessingParameterDistance(
            self.TOL, 'Gap tolerance (map units)',
            parentParameterName=self.INPUT, minValue=0.0,
            defaultValue=_get('DoubleEntry', 'tol', 0.05)))
        self.addParameter(QgsProcessingParameterEnum(
            self.FALLBACK, 'Fallback for clusters without closed outline',
            options=self.FALLBACKS, defaultValue=_get('NumEntry', 'fallback', 0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.CONC, 'Concavity (only for fallback "Concave hull"; 1 = convex)',
            QgsProcessingParameterNumber.Double, minValue=0.0, maxValue=1.0,
            defaultValue=_get('DoubleEntry', 'conc', 0.3)))
        self.addParameter(QgsProcessingParameterBoolean(
            self.MERGE, 'Merge overlapping result polygons',
            defaultValue=_get('BoolEntry', 'merge', True)))
        self.addParameter(QgsProcessingParameterNumber(
            self.MINAREA, 'Minimum area (smaller results are discarded)',
            QgsProcessingParameterNumber.Double, minValue=0.0,
            defaultValue=_get('DoubleEntry', 'minarea', 0.0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, 'Feature polygons', QgsProcessing.TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.QC, 'Unclosed line ends (QC points)',
            QgsProcessing.TypeVectorPoint, optional=True, createByDefault=False))

    def prepareAlgorithm(self, parameters, context, feedback):
        """Main thread: the safe place to store the settings in the project."""
        try:
            project = QgsProject.instance()
            layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
            if layer is not None:
                project.writeEntry(SETTINGS_SCOPE, 'input', layer.id())
            project.writeEntry(SETTINGS_SCOPE, 'group',
                               self.parameterAsString(parameters, self.GROUP, context) or '')
            project.writeEntryDouble(SETTINGS_SCOPE, 'tol',
                                     self.parameterAsDouble(parameters, self.TOL, context))
            project.writeEntry(SETTINGS_SCOPE, 'fallback',
                               self.parameterAsEnum(parameters, self.FALLBACK, context))
            project.writeEntryDouble(SETTINGS_SCOPE, 'conc',
                                     self.parameterAsDouble(parameters, self.CONC, context))
            project.writeEntryBool(SETTINGS_SCOPE, 'merge',
                                   self.parameterAsBool(parameters, self.MERGE, context))
            project.writeEntryDouble(SETTINGS_SCOPE, 'minarea',
                                     self.parameterAsDouble(parameters, self.MINAREA, context))
        except Exception as exc:  # storing settings must never block a run
            feedback.pushWarning('Settings could not be stored in the project: %s' % exc)
        return True

    # ------------------------------------------------------------ main run
    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))

        group_field = self.parameterAsString(parameters, self.GROUP, context)
        tol = self.parameterAsDouble(parameters, self.TOL, context)
        fallback = self.parameterAsEnum(parameters, self.FALLBACK, context)
        concavity = self.parameterAsDouble(parameters, self.CONC, context)
        do_merge = self.parameterAsBool(parameters, self.MERGE, context)
        min_area = self.parameterAsDouble(parameters, self.MINAREA, context)

        has_z = QgsWkbTypes.hasZ(source.wkbType())
        out_fields = source.fields()
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, out_fields,
            QgsWkbTypes.PolygonZ if has_z else QgsWkbTypes.Polygon, source.sourceCrs())
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        qc_sink, qc_id = self.parameterAsSink(
            parameters, self.QC, context, QgsFields(), QgsWkbTypes.Point, source.sourceCrs())

        feats = {f.id(): f for f in source.getFeatures()
                 if not f.geometry().isNull() and not f.geometry().isEmpty()}
        if not feats:
            raise QgsProcessingException('No valid lines in the input.')
        feedback.pushInfo('%d lines read.' % len(feats))

        clusters = self._cluster(feats, group_field, tol, feedback)
        if clusters is None:
            return {}
        feedback.pushInfo('%d feature clusters.' % len(clusters))

        records, skipped, open_ends_total = [], [], 0
        for n, members in enumerate(clusters):
            if feedback.isCanceled():
                return {}
            feedback.setProgress(15 + 70 * n / len(clusters))

            geoms = [feats[i].geometry() for i in members]
            poly, open_ends = self._faces(geoms, tol, has_z)
            open_ends_total += len(open_ends)
            if qc_sink is not None:
                for pt in open_ends:
                    qc = QgsFeature()
                    qc.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(pt.x(), pt.y())))
                    qc_sink.addFeature(qc, QgsFeatureSink.FastInsert)

            if poly is None and fallback > 0:
                poly = self._hull(QgsGeometry.unaryUnion(geoms), tol, fallback, concavity)

            parts = self._valid_parts(poly, min_area)
            if not parts:
                skipped.append(min(members))
                continue

            attrs = list(feats[max(members, key=lambda i: feats[i].geometry().length())]
                         .attributes())
            zmean = None
            for part in parts:
                if has_z and not part.constGet().is3D():   # hull fallback has no Z
                    zmean = _mean_z(geoms) if zmean is None else zmean
                    part.get().addZValue(zmean)
                records.append((part, attrs))

        if skipped:
            feedback.pushWarning(
                '%d clusters produced no polygon (no closed outline within the gap '
                'tolerance). First feature IDs: %s' % (len(skipped), sorted(skipped)[:15]))
        feedback.pushInfo('%d line ends could not be closed (see QC output).' % open_ends_total)

        if do_merge and len(records) > 1:
            records = self._merge(records, feedback)

        for geom, attrs in records:
            for part in geom.asGeometryCollection():
                if _is_type(part, QgsWkbTypes.PolygonGeometry):
                    out = QgsFeature(out_fields)
                    out.setAttributes(attrs)
                    out.setGeometry(part)
                    sink.addFeature(out, QgsFeatureSink.FastInsert)

        feedback.pushInfo('%d polygons written.' % len(records))
        result = {self.OUTPUT: dest_id}
        if qc_sink is not None:
            result[self.QC] = qc_id
        return result

    # ----------------------------------------------------------- clustering
    def _cluster(self, feats, group_field, tol, feedback):
        """Group line IDs into feature clusters; None if cancelled."""
        uf = _UnionFind()
        by_key, ungrouped = {}, []
        for fid, feat in feats.items():
            uf.add(fid)
            value = feat[group_field] if group_field else None
            if _is_blank(value):
                ungrouped.append(fid)
            else:
                by_key.setdefault(value, []).append(fid)

        for ids in by_key.values():
            for other in ids[1:]:
                uf.union(ids[0], other)

        if ungrouped:   # proximity clustering: buffers of tol/2 touch if lines are < tol apart
            radius = max(tol / 2.0, 1e-9)
            buffered, index = {}, QgsSpatialIndex()
            for fid in ungrouped:
                buffered[fid] = feats[fid].geometry().buffer(radius, 4)
                tmp = QgsFeature(fid)
                tmp.setGeometry(buffered[fid])
                index.addFeature(tmp)
            for n, fid in enumerate(ungrouped):
                if feedback.isCanceled():
                    return None
                for cand in index.intersects(buffered[fid].boundingBox()):
                    if cand > fid and buffered[fid].intersects(buffered[cand]):
                        uf.union(fid, cand)
                feedback.setProgress(15 * n / len(ungrouped))
        return uf.groups()

    # -------------------------------------------------------------- faces
    def _faces(self, geoms, tol, has_z):
        """Node the lines, bridge free line ends and polygonize.

        Returns (united polygon geometry or None, list of unbridged end points).
        """
        lines = []
        for g in geoms:
            if g.constGet().hasCurvedSegments():
                g = QgsGeometry(g)
                g.convertToStraightSegment()
            lines.append(g)
        pieces = [p for p in QgsGeometry.unaryUnion(lines).asGeometryCollection()
                  if _is_type(p, QgsWkbTypes.LineGeometry) and p.length() > 0]
        if not pieces:
            return None, []

        # end points of every noded piece; a point used by one piece only is a free end
        index = QgsSpatialIndex()
        piece_ends, degree = [], {}
        for i, piece in enumerate(pieces):
            feat = QgsFeature(i)
            feat.setGeometry(piece)
            index.addFeature(feat)
            curve = piece.constGet()
            ends = [(_end_key(pt), pt) for pt in (curve.startPoint(), curve.endPoint())]
            piece_ends.append(ends)
            for key, _ in ends:
                degree[key] = degree.get(key, 0) + 1
        free_keys = {k for k, d in degree.items() if d == 1}

        bridges, open_ends = [], []
        overshoot = tol * 0.01   # lets a bridge cross its target line so it is noded exactly
        for i, ends in enumerate(piece_ends):
            for key, pt in ends:
                if key not in free_keys:
                    continue
                target = self._bridge_target(pt, i, pieces, piece_ends, index, free_keys, tol)
                if target is None:
                    open_ends.append(pt)
                    continue
                bridge = self._make_bridge(pt, target, overshoot, has_z)
                if bridge is not None:
                    bridges.append(bridge)

        network = QgsGeometry.unaryUnion(pieces + bridges)
        faces = QgsGeometry.polygonize([network])
        if faces is None or faces.isNull() or faces.isEmpty():
            return None, open_ends
        faces = [f for f in faces.asGeometryCollection()
                 if _is_type(f, QgsWkbTypes.PolygonGeometry) and f.area() > 0]
        return (QgsGeometry.unaryUnion(faces) if faces else None), open_ends

    @staticmethod
    def _bridge_target(pt, own, pieces, piece_ends, index, free_keys, tol):
        """Nearest bridge target for a free end: (x, y, z, overshoot) or None.

        A free end of another piece is preferred when it is about as close as
        the nearest line, otherwise the nearest point on a line is used.
        """
        point = QgsGeometry.fromPointXY(QgsPointXY(pt.x(), pt.y()))
        search = QgsRectangle(pt.x() - tol, pt.y() - tol, pt.x() + tol, pt.y() + tol)
        best_line, best_end = None, None
        for c in index.intersects(search):
            if c == own:
                continue
            dist = pieces[c].distance(point)
            if dist > tol:
                continue
            if best_line is None or dist < best_line[0]:
                best_line = (dist, pieces[c].nearestPoint(point).asPoint())
            for key, end in piece_ends[c]:
                if key in free_keys:
                    d_end = math.hypot(end.x() - pt.x(), end.y() - pt.y())
                    if d_end <= tol and (best_end is None or d_end < best_end[0]):
                        best_end = (d_end, end)

        if best_line is None:
            return None
        if best_end is not None and best_end[0] <= 2.0 * best_line[0] + 1e-9:
            end = best_end[1]
            return end.x(), end.y(), end.z(), False
        return best_line[1].x(), best_line[1].y(), pt.z(), True

    @staticmethod
    def _make_bridge(pt, target, overshoot, has_z):
        tx, ty, tz, extend = target
        dx, dy = tx - pt.x(), ty - pt.y()
        length = math.hypot(dx, dy)
        if length < 1e-12:
            return None
        if extend:
            tx += dx / length * overshoot
            ty += dy / length * overshoot
        if has_z:
            z0 = 0.0 if math.isnan(pt.z()) else pt.z()
            z1 = z0 if math.isnan(tz) else tz
            points = [QgsPoint(pt.x(), pt.y(), z0), QgsPoint(tx, ty, z1)]
        else:
            points = [QgsPoint(pt.x(), pt.y()), QgsPoint(tx, ty)]
        return QgsGeometry(QgsLineString(points))

    # ------------------------------------------------------- hull, merge, io
    @staticmethod
    def _hull(lines, tol, fallback, concavity):
        if fallback == 1 and hasattr(lines, 'concaveHull'):   # needs QGIS >= 3.28
            dense = lines.densifyByDistance(max(tol, 1e-6))
            hull = dense.concaveHull(concavity, False)
            if hull is not None and not hull.isEmpty():
                return hull
        return lines.convexHull()

    @staticmethod
    def _valid_parts(poly, min_area):
        """Single, valid polygon parts of poly that reach the minimum area."""
        if poly is None or poly.isEmpty():
            return []
        if not poly.isGeosValid():
            poly = poly.makeValid()
        return [p for p in poly.asGeometryCollection()
                if _is_type(p, QgsWkbTypes.PolygonGeometry)
                and p.area() > 0 and p.area() >= min_area]

    @staticmethod
    def _merge(records, feedback):
        """Union result polygons that overlap by area; keep the largest one's attributes."""
        uf = _UnionFind()
        index = QgsSpatialIndex()
        for i, (geom, _) in enumerate(records):
            uf.add(i)
            feat = QgsFeature(i)
            feat.setGeometry(geom)
            index.addFeature(feat)
        for i, (geom, _) in enumerate(records):
            for j in index.intersects(geom.boundingBox()):
                if j > i and geom.intersects(records[j][0]):
                    if geom.intersection(records[j][0]).area() > 0:
                        uf.union(i, j)

        merged = []
        for ids in uf.groups():
            if len(ids) == 1:
                merged.append(records[ids[0]])
                continue
            group = [records[i] for i in ids]
            attrs = max(group, key=lambda r: r[0].area())[1]
            merged.append((QgsGeometry.unaryUnion([r[0] for r in group]), attrs))
        feedback.pushInfo('Merge: %d -> %d polygons.' % (len(records), len(merged)))
        return merged
