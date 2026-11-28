# Review order

Review the implementation against the Issue acceptance criteria.

## Issue body

${issue_body}

## Diff

${diff}

On pass, emit one top-level reply block:

```ghdag
status: ok
code: REVIEW_PASS
```

On fail, do not use body headings or bullet lists for findings. Emit one top-level reply block with one problem per `problems` item:

```ghdag
status: failed
code: REVIEW
problems:
  - <problem 1>
  - <problem 2>
```