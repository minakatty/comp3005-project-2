# Module 2 measurements

## 1. Join cost against the formula

M, N are data pages. Measured reads include each scan's directory page.

| join | M | N | B | measured reads | measured writes | measured total | formula | difference | comparisons |
|---|---|---|---|---|---|---|---|---|---|
| nested_loop_join | 11 | 11 | 4 | 84 | 0 | 84 | 77 | +7 | 62500 |
| nested_loop_join | 11 | 11 | 16 | 24 | 0 | 24 | 22 | +2 | 62500 |
| nested_loop_join | 11 | 11 | 64 | 24 | 0 | 24 | 22 | +2 | 62500 |
| nested_loop_join | 11 | 11 | 256 | 24 | 0 | 24 | 22 | +2 | 62500 |
| sort_merge_join | 11 | 11 | 4 | 95 | 71 | 166 | 88 | +78 | 5096 |
| sort_merge_join | 11 | 11 | 16 | 35 | 11 | 46 | 44 | +2 | 4953 |
| sort_merge_join | 11 | 11 | 64 | 24 | 0 | 24 | 44 | -20 | 4953 |
| sort_merge_join | 11 | 11 | 256 | 24 | 0 | 24 | 44 | -20 | 4953 |
| hash_join | 11 | 11 | 4 | 77 | 23 | 100 | 66 | +34 | 31172 |
| hash_join | 11 | 11 | 16 | 24 | 0 | 24 | 66 | -42 | 990 |
| hash_join | 11 | 11 | 64 | 24 | 0 | 24 | 66 | -42 | 990 |
| hash_join | 11 | 11 | 256 | 24 | 0 | 24 | 66 | -42 | 990 |
| nested_loop_join | 32 | 32 | 4 | 561 | 0 | 561 | 544 | +17 | 562500 |
| nested_loop_join | 32 | 32 | 16 | 132 | 0 | 132 | 128 | +4 | 562500 |
| nested_loop_join | 32 | 32 | 64 | 66 | 0 | 66 | 64 | +2 | 562500 |
| nested_loop_join | 32 | 32 | 256 | 66 | 0 | 66 | 64 | +2 | 562500 |
| sort_merge_join | 32 | 32 | 4 | 354 | 288 | 642 | 384 | +258 | 17182 |
| sort_merge_join | 32 | 32 | 16 | 162 | 96 | 258 | 256 | +2 | 18086 |
| sort_merge_join | 32 | 32 | 64 | 98 | 32 | 130 | 128 | +2 | 17158 |
| sort_merge_join | 32 | 32 | 256 | 66 | 0 | 66 | 128 | -62 | 17158 |
| hash_join | 32 | 32 | 4 | 376 | 64 | 440 | 192 | +248 | 281046 |
| hash_join | 32 | 32 | 16 | 133 | 67 | 200 | 192 | +8 | 3567 |
| hash_join | 32 | 32 | 64 | 66 | 0 | 66 | 192 | -126 | 4028 |
| hash_join | 32 | 32 | 256 | 66 | 0 | 66 | 192 | -126 | 4028 |
| nested_loop_join | 105 | 105 | 4 | 5724 | 0 | 5724 | 5670 | +54 | 6250000 |
| nested_loop_join | 105 | 105 | 16 | 954 | 0 | 954 | 945 | +9 | 6250000 |
| nested_loop_join | 105 | 105 | 64 | 318 | 0 | 318 | 315 | +3 | 6250000 |
| nested_loop_join | 105 | 105 | 256 | 212 | 0 | 212 | 210 | +2 | 6250000 |
| sort_merge_join | 105 | 105 | 4 | 1555 | 1343 | 2898 | 1680 | +1218 | 67179 |
| sort_merge_join | 105 | 105 | 16 | 527 | 315 | 842 | 840 | +2 | 86374 |
| sort_merge_join | 105 | 105 | 64 | 527 | 315 | 842 | 840 | +2 | 66338 |
| sort_merge_join | 105 | 105 | 256 | 212 | 0 | 212 | 420 | -208 | 66338 |
| hash_join | 105 | 105 | 4 | 3105 | 210 | 3315 | 630 | +2685 | 3124696 |
| hash_join | 105 | 105 | 16 | 428 | 216 | 644 | 630 | +14 | 12573 |
| hash_join | 105 | 105 | 64 | 424 | 212 | 636 | 630 | +6 | 12624 |
| hash_join | 105 | 105 | 256 | 212 | 0 | 212 | 630 | -418 | 10555 |

## 2. External sort passes

W = B - 1 working frames (one frame is the input page, DESIGN_M2.md 3). Own formula: runs0 = ceil(N/W); passes = 1 + (merge passes with fan-in W-1 until <= W runs) + 1.

| N pages | B | runs after pass 0 | measured passes | spec formula 1+ceil(log(ceil(N/B))/log(B-1)) | own formula | reads | writes | peak frames |
|---|---|---|---|---|---|---|---|---|
| 8 | 3 | 4 | 3 | 3 | 3 | 25 | 16 | 3 |
| 80 | 4 | 27 | 6 | 4 | 6 | 471 | 390 | 4 |
| 160 | 8 | 23 | 3 | 3 | 3 | 481 | 320 | 8 |
| 64 | 16 | 5 | 2 | 2 | 2 | 129 | 64 | 16 |
| 100 | 5 | 25 | 4 | 4 | 4 | 397 | 296 | 5 |
| 320 | 16 | 22 | 3 | 3 | 3 | 961 | 640 | 16 |
| 40 | 4 | 14 | 5 | 4 | 5 | 197 | 156 | 4 |

## 3. Crossover

### 3a. Fixed relations, varying B

R = 42 pages, S = 42 pages.

| B | nested_loop_join | sort_merge_join | hash_join | cheapest |
|---|---|---|---|---|
| 3 | 1849 | 1548 | 1976 | sort_merge_join |
| 4 | 946 | 954 | 660 | hash_join |
| 5 | 645 | 818 | 448 | hash_join |
| 6 | 516 | 578 | 370 | hash_join |
| 8 | 344 | 506 | 332 | hash_join |
| 12 | 258 | 338 | 260 | nested_loop_join |
| 16 | 172 | 338 | 262 | nested_loop_join |
| 24 | 129 | 338 | 260 | nested_loop_join |
| 32 | 129 | 338 | 260 | nested_loop_join |
| 48 | 86 | 170 | 86 | nested_loop_join |

### 3b. Fixed B = 8, varying size (R and S the same size)

| pages each | nested_loop_join | sort_merge_join | hash_join | cheapest |
|---|---|---|---|---|
| 3 | 8 | 8 | 8 | nested_loop_join |
| 7 | 24 | 58 | 48 | nested_loop_join |
| 17 | 72 | 138 | 110 | nested_loop_join |
| 38 | 312 | 458 | 279 | hash_join |
| 63 | 768 | 746 | 493 | hash_join |

## 4. Skew

40% of the rows on both sides have key 7; the rest are uniform over 0..999. Hash join at B = 16.

**uniform**: partitions (left pages) [12, 12, 10, 10, 11, 11], (right pages) [10, 11, 11, 11, 11, 11], overflowed partitions [], reads 259, writes 131, comparisons 4393, output 2263 rows, 0.04 s

**skewed**: partitions (left pages) [7, 7, 6, 6, 31, 8], (right pages) [6, 6, 6, 7, 32, 8], overflowed partitions [4], reads 337, writes 130, comparisons 561547, output 358257 rows, 0.33 s

## 5. The budget

Sort of 80 pages (20 x B) at B = 4: completed, 6 passes, 471 reads, 390 writes. Largest number of frames held at once (pinned + reserved, BufferPool.peak_held): **4**.

