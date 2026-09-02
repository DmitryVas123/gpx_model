import gpxpy
import gpxpy.gpx
import pandas as pd



gpx_file = open('input.gpx', 'r')

gpx = gpxpy.parse(gpx_file)

rows = []

for track_idx, track in enumerate(gpx.tracks):
    for segment_idx, segment in enumerate(track.segments):
        for point_idx, point in enumerate(segment.points):

            rows.append({
                "track": track_idx,
                "segment": segment_idx,
                "point": point_idx,

                "latitude": point.latitude,
                "longitude": point.longitude,
                "elevation": point.elevation,
                "time": point.time,

                "speed": point.speed,
                "course": point.course,

                "name": point.name,
                "comment": point.comment,
                "description": point.description,
            })

df = pd.DataFrame(rows)

print(df.head())
print(df.info())