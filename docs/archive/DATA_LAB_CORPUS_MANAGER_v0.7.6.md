# Data Lab & Corpus Manager v0.7.6

> **Superseded / Archive.** Describes the Data Lab state of v0.7.6. The Data Lab
> still exists (filters, backups, hot reload); details are in `STUDIO.md`.

# Data Lab & Corpus Manager v0.7.6

## Which data Data Lab shows

Data Lab distinguishes four states:

- **Active token stream:** documents contained in the last prepared train/validation/test manifest. The actually used tokens are shown for every document.
- **New/pending:** new raw or audited files that have not been tokenized again yet.
- **Raw data:** own files under `data/raw`; these are editable and deletable.
- **Task data:** generated instruction examples. They are counted as tasks and not reported as new independent corpus diversity.

The former curriculum view can no longer override the corpus token value.

## Deleting

Only own files under `data/raw` can be deleted directly. This prevents generated shards, audited sources or derived tasks from being inconsistently changed individually.

Before deletion a backup is created under:

```text
.studio_backups/data_lab/deleted/
```

After a deletion or change the previously prepared token stream counts as stale. The data pipeline must be run again from the affected step.

## Hot reload

While Data Lab is open, the interface polls a compact revision every 2.5 seconds. New, changed or deleted files are adopted without a complete page reload. Unsaved editor changes are not overwritten.

## Large corpus expansion

The new catalog contains 30 sources. 23 of them form the new expansion and start disabled on existing installations.

- **5M candidates:** eight larger sources, catalog estimate 6.0M tokens.
- **Toward 20M:** additionally 15 further sources, total estimate 12.44M tokens.

The estimate is based on the expected amount of GDScript and is not a measured final value. Only the local run delivers a reliable value:

1. verify license locally
2. selectively load source code
3. detect Godot-4 files
4. parser check
5. remove duplicates and leaks
6. form the split
7. count with the current BPE tokenizer

A source with a missing or mismatching license file is not admitted to the corpus.

## Storage and download behavior

Large repositories are loaded with a partial Git clone and sparse checkout. By default only Godot project files, GDScript and a few text-based resources are requested. Images, audio, videos, build folders and caches are not part of the training corpus.
