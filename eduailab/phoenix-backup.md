Backup Methods

A. Local SQLite (Default)

Phoenix stores local data inside ~/.phoenix/ or the custom path set by your PHOENIX_WORKING_DIR environment variable.

 - To backup: Copy or archive the entire directory contents (e.g., using tar -czvf phoenix_backup.tar.gz ~/.phoenix).
 - To restore: Place the backed-up files back into the original directory or point PHOENIX_WORKING_DIR to the restored folder before launching the server.
 See (https://arize.com/docs/phoenix/self-hosting/architecture)

B. Exporting with Phonix CLI (CLI snapshoting)

 - To backup: px trace list --limit 1000 --format json > phoenix_traces.json
 - To resore use Pyton SDK
```
pythonimport os
import phoenix as px

# 1. Point to your active Docker/Phoenix collector endpoint
os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = "http://localhost:6006"

# 2. Load and push your saved traces back into the application
px.launch_app(
    trace=px.TraceDataset.load(
        "my-restored-project", 
        directory="./phoenix_traces.json"
    )
)
```

C. Exporting and Importing Traces via Python SDK

 - To export/backup individual records: Use the Phoenix Client to query spans and export them programmatically into dataframes or JSON files.
   See more (https://arize.com/docs/phoenix/tracing/how-to-tracing/importing-and-exporting-traces/extract-data-from-spans)
            (https://arize.com/docs/phoenix/release-notes)
 - To import/restore records: Use built-in trace loading functions from the Phoenix tracing utilities to ingest existing trace logs back into a running server instance.
   See more (https://arize.com/docs/phoenix/tracing/how-to-tracing/importing-and-exporting-traces)

