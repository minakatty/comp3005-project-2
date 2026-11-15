# Module 2 measurements

## 1. Join cost against the formula

M, N are data pages. Measured reads include each scan's directory page.

| join | M | N | B | measured reads | measured writes | measured total | formula | difference | comparisons |
|---|---|---|---|---|---|---|---|---|---|
| nested_loop_join | 11 | 11 | 4 | 84 | 0 | 84 | 77 | +7 | 62500 |
| nested_loop_join | 11 | 11 | 16 | 24 | 0 | 24 | 22 | +2 | 62500 |
| nested_loop_join | 11 | 11 | 64 | 24 | 0 | 24 | 22 | +2 | 62500 |
| nested_loop_join | 11 | 11 | 256 | 24 | 0 | 24 | 22 | +2 | 62500 |
| sort_merge_join | 11 | 11 | 4 | 105 | 33 | 138 | 88 | +50 | 5177 |
| sort_merge_join | 11 | 11 | 16 | 35 | 11 | 46 | 44 | +2 | 4953 |
| sort_merge_join | 11 | 11 | 64 | 24 | 0 | 24 | 44 | -20 | 4953 |
| sort_merge_join | 11 | 11 | 256 | 24 | 0 | 24 | 44 | -20 | 4953 |
| hash_join | 11 | 11 | 4 | 77 | 25 | 102 | 66 | +36 | 20850 |
| hash_join | 11 | 11 | 16 | 24 | 0 | 24 | 66 | -42 | 990 |
| hash_join | 11 | 11 | 64 | 24 | 0 | 24 | 66 | -42 | 990 |
| hash_join | 11 | 11 | 256 | 24 | 0 | 24 | 66 | -42 | 990 |
| nested_loop_join | 32 | 32 | 4 | 561 | 0 | 561 | 544 | +17 | 562500 |
| nested_loop_join | 32 | 32 | 16 | 132 | 0 | 132 | 128 | +4 | 562500 |
| nested_loop_join | 32 | 32 | 64 | 66 | 0 | 66 | 64 | +2 | 562500 |
| nested_loop_join | 32 | 32 | 256 | 66 | 0 | 66 | 64 | +2 | 562500 |
| sort_merge_join | 32 | 32 | 4 | 502 | 160 | 662 | 384 | +278 | 18320 |
| sort_merge_join | 32 | 32 | 16 | 162 | 96 | 258 | 256 | +2 | 17151 |
| sort_merge_join | 32 | 32 | 64 | 98 | 32 | 130 | 128 | +2 | 17158 |
| sort_merge_join | 32 | 32 | 256 | 66 | 0 | 66 | 128 | -62 | 17158 |
| hash_join | 32 | 32 | 4 | 299 | 66 | 365 | 192 | +173 | 188133 |
| hash_join | 32 | 32 | 16 | 141 | 75 | 216 | 192 | +24 | 3388 |
| hash_join | 32 | 32 | 64 | 66 | 0 | 66 | 192 | -126 | 4028 |
| hash_join | 32 | 32 | 256 | 66 | 0 | 66 | 192 | -126 | 4028 |
| nested_loop_join | 105 | 105 | 4 | 5724 | 0 | 5724 | 5670 | +54 | 6250000 |
| nested_loop_join | 105 | 105 | 16 | 954 | 0 | 954 | 945 | +9 | 6250000 |
| nested_loop_join | 105 | 105 | 64 | 318 | 0 | 318 | 315 | +3 | 6250000 |
| nested_loop_join | 105 | 105 | 256 | 212 | 0 | 212 | 210 | +2 | 6250000 |
| sort_merge_join | 105 | 105 | 4 | 1798 | 735 | 2533 | 1680 | +853 | 72558 |
| sort_merge_join | 105 | 105 | 16 | 527 | 315 | 842 | 840 | +2 | 82286 |
| sort_merge_join | 105 | 105 | 64 | 527 | 315 | 842 | 840 | +2 | 66410 |
| sort_merge_join | 105 | 105 | 256 | 212 | 0 | 212 | 420 | -208 | 66338 |
| hash_join | 105 | 105 | 4 | 2202 | 212 | 2414 | 630 | +1784 | 2082298 |
| hash_join | 105 | 105 | 16 | 436 | 224 | 660 | 630 | +30 | 12924 |
| hash_join | 105 | 105 | 64 | 477 | 265 | 742 | 630 | +112 | 11961 |
| hash_join | 105 | 105 | 256 | 212 | 0 | 212 | 630 | -418 | 10555 |

## 2. External sort passes

| N pages | B | runs after pass 0 | measured passes | formula 1+ceil(log(ceil(N/B))/log(B-1)) | reads | writes | peak frames |
|---|---|---|---|---|---|---|---|
| 8 | 3 | 3 | 3 | 3 | 23 | 14 | 3 |
| 80 | 4 | 20 | 4 | 4 | 313 | 232 | 4 |
| 160 | 8 | 20 | 3 | 3 | 481 | 320 | 8 |
| 64 | 16 | 4 | 2 | 2 | 129 | 64 | 16 |
| 100 | 5 | 20 | 4 | 4 | 381 | 280 | 5 |
| 320 | 16 | 20 | 3 | 3 | 961 | 640 | 16 |
| 40 | 4 | 10 | 4 | 4 | 153 | 112 | 4 |

## 3. Crossover

### 3a. Fixed relations, varying B

R = 42 pages, S = 42 pages.

| B | nested_loop_join | sort_merge_join | hash_join | cheapest |
|---|---|---|---|---|
| 3 | 1849 | 1244 | 1102 | hash_join |
| 4 | 946 | 651 | 566 | hash_join |
| 5 | 645 | 498 | 414 | hash_join |
| 6 | 516 | 506 | 355 | hash_join |
| 8 | 344 | 338 | 282 | hash_join |
| 12 | 258 | 338 | 274 | nested_loop_join |
| 16 | 172 | 338 | 282 | nested_loop_join |
| 24 | 129 | 338 | 300 | nested_loop_join |
| 32 | 129 | 338 | 308 | nested_loop_join |
| 48 | 86 | 170 | 86 | nested_loop_join |

### 3b. Fixed B = 8, varying size (R and S the same size)

| pages each | nested_loop_join | sort_merge_join | hash_join | cheapest |
|---|---|---|---|---|
| 3 | 8 | 8 | 8 | nested_loop_join |
| 7 | 24 | 30 | 54 | nested_loop_join |
| 17 | 72 | 138 | 114 | nested_loop_join |
| 38 | 312 | 306 | 240 | hash_join |
| 63 | 768 | 730 | 515 | hash_join |

## 4. Skew

40% of the rows on both sides have key 7; the rest are uniform over 0..999. Hash join at B = 16.

**uniform**: partitions (left pages) [5, 5, 5, 4, 5, 4, 5, 5, 4, 4, 5, 5, 5, 4, 5], (right pages) [4, 5, 4, 5, 5, 5, 5, 5, 5, 5, 4, 6, 4, 4, 5], overflowed partitions [], reads 269, writes 141, comparisons 4220, output 2263 rows, 0.11 s

**skewed**: partitions (left pages) [3, 3, 3, 3, 3, 3, 4, 27, 3, 3, 4, 3, 3, 3, 3], (right pages) [3, 3, 3, 3, 3, 2, 3, 28, 4, 3, 3, 3, 2, 3, 3], overflowed partitions [7], reads 312, writes 140, comparisons 433191, output 358257 rows, 0.48 s

## 5. The budget

Sort of 80 pages (20 x B) at B = 4: completed, 4 passes, 313 reads, 232 writes. Largest number of frames held at once (pinned + reserved, BufferPool.peak_held): **4**.

