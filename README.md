# Featurelines-to-Polygons.py
QGIS Processing script: converts open and closed archaeological feature lines into polygons of the maximum extent of each feature outline. Closes line gaps within a tolerance, keeps free areas free, carries over attributes and Z values, merges overlaps, flags unclosed ends in a QC layer. Settings are stored in the project.
