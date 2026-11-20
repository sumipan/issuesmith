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

On fail, list problems under the `Problems:` heading, then emit:

```ghdag
status: failed
code: REVIEW
```