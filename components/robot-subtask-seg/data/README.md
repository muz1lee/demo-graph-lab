# Data

Place local input manifests here. Do not commit large video files.

Build a manifest from a RoboDojo filtered video folder:

```bash
robot-subtask-seg build-manifest \
  --input-dir /path/to/filtered_1024_task_videos \
  --output data/robodojo_1024_filtered.json
```

