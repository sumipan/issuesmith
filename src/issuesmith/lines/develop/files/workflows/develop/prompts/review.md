# Review order

Review the implementation against the Issue acceptance criteria.

## Issue body

${issue_body}

## Diff

${diff}

On pass, emit a standalone line: `PIPELINE_STATUS: REVIEW_PASS`

On fail, emit `PIPELINE_STATUS: REVIEW_FAIL` and list problems under the `Problems:` heading.