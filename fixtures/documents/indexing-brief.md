# Indexing brief

A short attachment so `read_document` has something to open in a fresh clone. Handwritten;
nothing here is researched material.

## What an index is

An index is a separate structure that stores keys in sorted order alongside a pointer back
to the row they came from. Looking a key up is then a walk down a tree rather than a scan
of every row in the table, which is why the win grows as the table does.

## B-tree lookups

A B-tree keeps its entries sorted and balanced, so a lookup costs a number of steps
proportional to the depth of the tree. The planner can also read the entries in order,
which is what lets an index satisfy a sort as well as a filter.

## Reading EXPLAIN

`EXPLAIN` prints the plan the planner chose without running the statement. `EXPLAIN
ANALYZE` runs it and prints the real timings next to the estimates, so a bad plan usually
shows up as a large gap between the expected and the actual row counts.

```sql
EXPLAIN ANALYZE
SELECT id, title
FROM articles
WHERE author_id = 42
ORDER BY published_at DESC
LIMIT 10;
```

## Costs

An index is not free: every insert, update and delete has to maintain it, and it occupies
disk of its own. An index nothing queries is pure overhead.
