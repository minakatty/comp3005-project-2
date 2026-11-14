# Module 1 measurements

## 1. Capacity

| schema | columns | record bytes L | computed floor(4088/(L+4)) | measured per full page | data pages for 3000 rows |
|---|---|---|---|---|---|
| all INT | `a:INT,b:INT,c:INT` | 12 | 255 | 255 | 12 |
| short VARCHAR | `a:INT,b:VARCHAR(5),c:INT` | 13 | 240 | 240 | 13 |
| VARCHAR(200) | `a:INT,s:VARCHAR(200)` | 206 | 19 | 19 | 158 |

## 2. Replacement policies (traces from `/tmp/traces_demo`)

| trace | policy | B | hits | misses | evictions | hit rate |
|---|---|---|---|---|---|---|
| trace1.txt | lru | 4 | 99 | 4901 | 4897 | 0.0198 |
| trace1.txt | lru | 16 | 398 | 4602 | 4586 | 0.0796 |
| trace1.txt | lru | 64 | 1580 | 3420 | 3356 | 0.3160 |
| trace1.txt | lru | 256 | 4800 | 200 | 0 | 0.9600 |
| trace1.txt | clock | 4 | 99 | 4901 | 4897 | 0.0198 |
| trace1.txt | clock | 16 | 401 | 4599 | 4583 | 0.0802 |
| trace1.txt | clock | 64 | 1594 | 3406 | 3342 | 0.3188 |
| trace1.txt | clock | 256 | 4800 | 200 | 0 | 0.9600 |
| trace2.txt | lru | 4 | 3817 | 1183 | 1179 | 0.7634 |
| trace2.txt | lru | 16 | 4731 | 269 | 253 | 0.9462 |
| trace2.txt | lru | 64 | 4920 | 80 | 16 | 0.9840 |
| trace2.txt | lru | 256 | 4922 | 78 | 0 | 0.9844 |
| trace2.txt | clock | 4 | 3632 | 1368 | 1364 | 0.7264 |
| trace2.txt | clock | 16 | 4692 | 308 | 292 | 0.9384 |
| trace2.txt | clock | 64 | 4915 | 85 | 21 | 0.9830 |
| trace2.txt | clock | 256 | 4922 | 78 | 0 | 0.9844 |
| trace3.txt | lru | 4 | 0 | 800 | 796 | 0.0000 |
| trace3.txt | lru | 16 | 0 | 800 | 784 | 0.0000 |
| trace3.txt | lru | 64 | 0 | 800 | 736 | 0.0000 |
| trace3.txt | lru | 256 | 720 | 80 | 0 | 0.9000 |
| trace3.txt | clock | 4 | 0 | 800 | 796 | 0.0000 |
| trace3.txt | clock | 16 | 0 | 800 | 784 | 0.0000 |
| trace3.txt | clock | 64 | 0 | 800 | 736 | 0.0000 |
| trace3.txt | clock | 256 | 720 | 80 | 0 | 0.9000 |
| trace4.txt | lru | 4 | 9 | 791 | 787 | 0.0112 |
| trace4.txt | lru | 16 | 21 | 779 | 763 | 0.0262 |
| trace4.txt | lru | 64 | 278 | 522 | 458 | 0.3475 |
| trace4.txt | lru | 256 | 477 | 323 | 67 | 0.5962 |
| trace4.txt | clock | 4 | 9 | 791 | 787 | 0.0112 |
| trace4.txt | clock | 16 | 22 | 778 | 762 | 0.0275 |
| trace4.txt | clock | 64 | 257 | 543 | 479 | 0.3212 |
| trace4.txt | clock | 256 | 464 | 336 | 80 | 0.5800 |
| trace5.txt | lru | 4 | 0 | 2000 | 1996 | 0.0000 |
| trace5.txt | lru | 16 | 0 | 2000 | 1984 | 0.0000 |
| trace5.txt | lru | 64 | 1764 | 236 | 172 | 0.8820 |
| trace5.txt | lru | 256 | 1764 | 236 | 0 | 0.8820 |
| trace5.txt | clock | 4 | 0 | 2000 | 1996 | 0.0000 |
| trace5.txt | clock | 16 | 0 | 2000 | 1984 | 0.0000 |
| trace5.txt | clock | 64 | 1764 | 236 | 172 | 0.8820 |
| trace5.txt | clock | 256 | 1764 | 236 | 0 | 0.8820 |


## 3. Scan cost

Table Big: 500 data pages + 1 directory page, 120000 rows of 13 bytes.

| B | page reads | hits | misses | evictions | max frames pinned |
|---|---|---|---|---|---|
| 2 | 501 | 0 | 501 | 501 | 1 |
| 8 | 501 | 0 | 501 | 495 | 1 |
| 64 | 501 | 0 | 501 | 439 | 1 |
